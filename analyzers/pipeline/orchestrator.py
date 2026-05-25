"""Pipeline Orchestrator — 分析流水线驱动器

受 audit 的 orchestrator.py 启发，实现测试分析的多阶段流水线：

  Recon → Hunt(并行) → Validate(对抗) → Feedback(扩散) → Report

每个阶段之间有成本检查，支持断点续跑。
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from ..base import AnalysisResult
from ..llm_client import LLMClient, LLMConfig
from .agents import get_agent_prompt
from .runner import AgentRunner, AgentResult, SchemaValidationError
from .state import PipelineState

logger = logging.getLogger(__name__)


class CostExceeded(RuntimeError):
    """预算耗尽"""
    pass


@dataclass
class PipelineConfig:
    """流水线配置"""
    # 预算控制
    max_tokens: int = 100000  # 单次分析的 token 上限
    max_hunt_agents: int = 10  # 最大并行 Hunt agent 数

    # 阶段开关
    enable_recon: bool = True
    enable_validate: bool = True  # 对抗验证
    enable_feedback: bool = True  # 反馈扩散
    feedback_iterations: int = 1  # 反馈循环次数

    # LLM 配置
    llm_config: Optional[LLMConfig] = None
    repair_attempts: int = 1

    # 是否使用 LLM（False = 纯规则引擎 fallback）
    use_llm: Optional[bool] = None  # None = 自动检测

    def __post_init__(self):
        if self.llm_config is None:
            self.llm_config = LLMConfig()


@dataclass
class PipelineResult:
    """流水线执行结果"""
    run_id: str
    status: str  # completed / aborted / failed
    confirmed_findings: List[Dict[str, Any]]
    rejected_count: int
    quality_score: float
    recommendations: List[str]
    cost_summary: Dict[str, Any]
    duration_ms: int
    report: Optional[Dict[str, Any]] = None


# ─── 规则引擎 Fallback（无 LLM 时） ──────────────────────

class RuleBasedAnalyzer:
    """规则引擎分析器 — LLM 不可用时的 fallback。

    保持与原有 BugDiscoveryAnalyzer + QualityGuard + AnomalyDetector 相同的能力。
    """

    def run_recon(self, events: List[Dict[str, Any]]) -> Dict[str, Any]:
        """规则引擎版 Recon"""
        from collections import Counter

        type_counts: Dict[str, int] = Counter()
        projects: set = set()
        frameworks: set = set()
        sessions: set = set()

        for e in events:
            type_counts[e["type"]] += 1
            src = e.get("source", {})
            if src.get("project"):
                projects.add(src["project"])
            if src.get("framework"):
                frameworks.add(src["framework"])
            if e.get("session_id"):
                sessions.add(e["session_id"])

        total = len(events)
        passed = type_counts.get("test.end", 0)
        failed = type_counts.get("test.fail", 0)
        pass_rate = passed / (passed + failed) if (passed + failed) > 0 else 1.0

        # 生成分析任务
        tasks = []
        task_idx = 0

        # 总是有 flaky 和 coverage 检测
        if total > 0:
            tasks.append({
                "task_id": f"t_flaky_{task_idx}",
                "agent_type": "flaky_detector",
                "scope_hint": f"全量事件中检测 flaky test（{total} 事件，{len(projects)} 项目）",
                "target_events": [],
                "priority": 1,
            })
            task_idx += 1
            tasks.append({
                "task_id": f"t_coverage_{task_idx}",
                "agent_type": "coverage_analyzer",
                "scope_hint": f"检测覆盖盲区和无断言测试",
                "target_events": [],
                "priority": 2,
            })
            task_idx += 1

        # 通过率低时加高优先级
        if pass_rate < 0.8:
            tasks.append({
                "task_id": f"t_regression_{task_idx}",
                "agent_type": "regression_detector",
                "scope_hint": f"通过率 {pass_rate:.1%}，检测性能回归",
                "target_events": [],
                "priority": 1,
            })

        # 有 agent 事件时加语义评估
        if type_counts.get("agent.tool_result", 0) > 0:
            tasks.append({
                "task_id": f"t_semantic_{task_idx}",
                "agent_type": "semantic_evaluator",
                "scope_hint": f"评估 Agent 工具输出的语义质量",
                "target_events": [],
                "priority": 3,
            })

        # 事件间隔异常时加性能分析
        if total > 5:
            tasks.append({
                "task_id": f"t_perf_{task_idx}",
                "agent_type": "performance_analyzer",
                "scope_hint": f"检测执行时间异常和阻塞",
                "target_events": [],
                "priority": 3,
            })

        return {
            "summary": {
                "total_events": total,
                "sessions": len(sessions),
                "projects": list(projects),
                "frameworks": list(frameworks),
                "pass_rate": pass_rate,
            },
            "event_breakdown": dict(type_counts),
            "analysis_tasks": tasks,
            "anomalies_detected": (
                [f"通过率过低: {pass_rate:.1%}"] if pass_rate < 0.5 else []
            ),
        }

    def run_hunt(self, agent_type: str, events: List[Dict[str, Any]], task: Dict[str, Any]) -> Dict[str, Any]:
        """规则引擎版 Hunt — 复用原有分析器逻辑"""
        from ..bug_discovery import BugDiscoveryAnalyzer
        from ..quality_guard import QualityGuard
        from ..anomaly_detector import AnomalyDetector

        findings = []

        if agent_type == "flaky_detector":
            findings.extend(self._detect_flaky(events))
        elif agent_type == "coverage_analyzer":
            findings.extend(self._detect_coverage_gaps(events))
        elif agent_type == "regression_detector":
            findings.extend(self._detect_regressions(events))
        elif agent_type == "performance_analyzer":
            findings.extend(self._detect_perf_issues(events))
        elif agent_type == "semantic_evaluator":
            # 复用原有 SemanticEvaluator 的规则引擎部分
            from ..semantic_eval import SemanticEvaluator
            evaluator = SemanticEvaluator(use_llm=False)
            result = evaluator.analyze(events)
            findings.extend(result.findings)

        return {
            "task_id": task["task_id"],
            "agent_type": agent_type,
            "findings": findings,
            "analysis_summary": f"规则引擎分析完成，发现 {len(findings)} 个问题",
        }

    def _detect_flaky(self, events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """检测 flaky test"""
        findings = []
        test_events: Dict[str, List[str]] = {}  # test_name -> [event types]

        for e in events:
            name = e.get("data", {}).get("test_name", "")
            if name:
                test_events.setdefault(name, []).append(e["type"])

        for name, types in test_events.items():
            has_pass = "test.end" in types
            has_fail = "test.fail" in types
            if has_pass and has_fail:
                findings.append({
                    "finding_id": f"flaky_{hash(name) % 10000:04d}",
                    "category": "flaky_test",
                    "severity": "high",
                    "description": f"测试 {name} 既有通过又有失败，是 flaky test",
                    "evidence": {
                        "event_ids": [],
                        "snippet": f"pass={types.count('test.end')}, fail={types.count('test.fail')}",
                    },
                    "affected_tests": [name],
                    "confidence": 0.9,
                })

        return findings

    def _detect_coverage_gaps(self, events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """检测覆盖盲区（含 QualityGuard 的检测能力）"""
        findings = []
        started = set()
        ended = set()
        has_assertions: Dict[str, bool] = {}
        test_durations: Dict[str, float] = {}

        for e in events:
            name = e.get("data", {}).get("test_name", "")
            if not name:
                continue
            if e["type"] == "test.start":
                started.add(name)
            elif e["type"] in ("test.end", "test.fail", "test.skip"):
                ended.add(name)
                dur = e.get("data", {}).get("duration_ms", 0)
                if dur > 0:
                    test_durations[name] = dur
            elif e["type"] in ("assert.pass", "assert.fail"):
                has_assertions[name] = True

        # 未完成的测试
        for name in started - ended:
            findings.append({
                "finding_id": f"incomplete_{hash(name) % 10000:04d}",
                "category": "coverage_gap",
                "severity": "high",
                "description": f"测试 {name} 开始但未结束（可能崩溃或超时）",
                "evidence": {"event_ids": [], "snippet": "test.start without test.end/test.fail"},
                "affected_tests": [name],
                "confidence": 0.95,
            })

        # 无断言的测试
        for name in started:
            if not has_assertions.get(name):
                findings.append({
                    "finding_id": f"no_assert_{hash(name) % 10000:04d}",
                    "category": "assertion_gap",
                    "severity": "medium",
                    "description": f"测试 {name} 没有任何断言",
                    "evidence": {"event_ids": [], "snippet": "no assert.pass or assert.fail events"},
                    "affected_tests": [name],
                    "confidence": 0.85,
                })

        # 测试粒度过粗（超过 10 秒）
        for name, duration in test_durations.items():
            if duration > 10000:
                findings.append({
                    "finding_id": f"too_long_{hash(name) % 10000:04d}",
                    "category": "assertion_gap",
                    "severity": "low",
                    "description": f"测试 {name} 耗时 {duration:.0f}ms，建议拆分",
                    "evidence": {"event_ids": [], "snippet": f"duration={duration:.0f}ms"},
                    "affected_tests": [name],
                    "confidence": 0.8,
                })

        # 所有测试都通过 = 可能缺少错误路径测试
        has_any_fail = any(e["type"] == "test.fail" for e in events)
        if not has_any_fail and len(events) > 20:
            findings.append({
                "finding_id": "no_failure_tests_0000",
                "category": "coverage_gap",
                "severity": "low",
                "description": "所有测试都通过了，可能缺少边界条件和错误路径测试",
                "evidence": {"event_ids": [], "snippet": "no test.fail events in session"},
                "affected_tests": [],
                "confidence": 0.7,
            })

        return findings

    def _detect_regressions(self, events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """检测性能回归"""
        findings = []
        durations: Dict[str, float] = {}

        for e in events:
            if e["type"] in ("test.end", "test.fail"):
                name = e.get("data", {}).get("test_name", "")
                dur = e.get("data", {}).get("duration_ms", 0)
                if name and dur > 0:
                    durations[name] = dur

        if not durations:
            return findings

        values = list(durations.values())
        avg = sum(values) / len(values)
        threshold = max(avg * 3, 10000)  # 3 倍平均或 10 秒

        for name, dur in durations.items():
            if dur > threshold:
                findings.append({
                    "finding_id": f"reg_{hash(name) % 10000:04d}",
                    "category": "performance_regression",
                    "severity": "high" if dur > 30000 else "medium",
                    "description": f"测试 {name} 耗时 {dur:.0f}ms（平均 {avg:.0f}ms）",
                    "evidence": {"event_ids": [], "snippet": f"duration={dur:.0f}ms, avg={avg:.0f}ms"},
                    "affected_tests": [name],
                    "confidence": 0.8,
                })

        return findings

    def _detect_perf_issues(self, events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """检测执行时间异常（含慢测试检测）"""
        findings = []

        # 1. 检测异常长间隔
        if len(events) >= 2:
            timestamps = [e["timestamp"] for e in events if "timestamp" in e]
            if len(timestamps) >= 2:
                gaps = [timestamps[i] - timestamps[i - 1] for i in range(1, len(timestamps))]
                avg_gap = sum(gaps) / len(gaps)
                max_gap = max(gaps)

                if max_gap > avg_gap * 10 and max_gap > 30000:
                    findings.append({
                        "finding_id": f"gap_{hash(str(max_gap)) % 10000:04d}",
                        "category": "race_condition",
                        "severity": "medium",
                        "description": f"检测到异常长间隔: {max_gap/1000:.1f}秒（平均 {avg_gap/1000:.1f}秒）",
                        "evidence": {"event_ids": [], "snippet": f"max_gap={max_gap:.0f}ms, avg_gap={avg_gap:.0f}ms"},
                        "confidence": 0.7,
                    })

        # 2. 检测慢测试（超过 10 秒）
        durations: Dict[str, float] = {}
        for e in events:
            if e["type"] in ("test.end", "test.fail"):
                name = e.get("data", {}).get("test_name", "")
                dur = e.get("data", {}).get("duration_ms", 0)
                if name and dur > 0:
                    durations[name] = dur

        for name, dur in durations.items():
            if dur > 10000:
                findings.append({
                    "finding_id": f"slow_{hash(name) % 10000:04d}",
                    "category": "race_condition",
                    "severity": "medium" if dur < 30000 else "high",
                    "description": f"测试 {name} 耗时 {dur:.0f}ms，建议检查是否有不必要的等待",
                    "evidence": {"event_ids": [], "snippet": f"duration={dur:.0f}ms"},
                    "affected_tests": [name],
                    "confidence": 0.8,
                })

        return findings


# ─── Pipeline Orchestrator ────────────────────────────────

class AnalysisPipeline:
    """分析流水线编排器。

    实现 audit 风格的多阶段分析：
    1. Recon — 扫描事件，生成分析任务
    2. Hunt — 多个窄 Agent 并行分析
    3. Validate — 对抗验证（推翻 findings）
    4. Feedback — 从确认的 findings 中提取模式，扩散检测
    5. Report — 汇总报告

    支持两种模式：
    - LLM 模式：使用 LLM Agent（需要配置 LLMClient）
    - 规则模式：使用规则引擎 fallback（无需 LLM）
    """

    def __init__(
        self,
        state: PipelineState,
        config: Optional[PipelineConfig] = None,
        llm_client: Optional[LLMClient] = None,
    ):
        self.state = state
        self.config = config or PipelineConfig()
        self.rule_engine = RuleBasedAnalyzer()

        # 判断是否使用 LLM
        from ..llm_client import is_llm_available
        if self.config.use_llm is not None:
            self.use_llm = self.config.use_llm
        else:
            self.use_llm = llm_client is not None or is_llm_available()

        # 初始化 LLM runner（仅在 LLM 可用时）
        self.runner: Optional[AgentRunner] = None
        if self.use_llm:
            try:
                self.runner = AgentRunner(
                    llm_client=llm_client,
                    llm_config=self.config.llm_config,
                    repair_attempts=self.config.repair_attempts,
                )
            except Exception as e:
                logger.warning("LLM 初始化失败，降级到规则引擎: %s", e)
                self.use_llm = False

    def run(
        self,
        events: List[Dict[str, Any]],
        session_id: str,
        run_id: Optional[str] = None,
    ) -> PipelineResult:
        """执行完整的分析流水线。

        Args:
            events: 测试事件列表
            session_id: 测试 session ID
            run_id: 分析 run ID（不提供则自动生成）

        Returns:
            PipelineResult 包含所有分析结果
        """
        run_id = run_id or f"analysis_{uuid.uuid4().hex[:8]}"
        start_time = time.time()

        # 创建 run
        self.state.create_run(session_id, run_id, {
            "use_llm": self.use_llm,
            "max_tokens": self.config.max_tokens,
        })

        logger.info("[%s] 开始分析流水线 (%s 模式, %d 事件)",
                    run_id, "LLM" if self.use_llm else "规则引擎", len(events))

        try:
            # ─── Stage 1: Recon ───────────────────────────
            self._budget_check(run_id, "recon")
            recon_result = self._run_recon(run_id, events)
            self.state.save_recon(run_id, recon_result)

            # ─── Stage 2: Hunt (并行) ─────────────────────
            tasks = recon_result.get("analysis_tasks", [])
            self._budget_check(run_id, "hunt")
            hunt_findings = self._run_hunt(run_id, events, tasks)

            # ─── Stage 3: Validate (对抗) ─────────────────
            if self.config.enable_validate and self.use_llm:
                self._budget_check(run_id, "validate")
                self._run_validate(run_id, events, hunt_findings)
            elif not self.use_llm:
                # 规则引擎模式：无对抗验证，自动确认所有 findings
                for f in self.state.get_unvalidated_findings(run_id):
                    self.state.set_validation(f.finding_id, "confirmed", {
                        "verdict": "confirmed",
                        "rationale": "规则引擎模式，跳过对抗验证",
                    })

            # ─── Stage 4: Feedback (扩散) ─────────────────
            if self.config.enable_feedback:
                for i in range(self.config.feedback_iterations):
                    self._budget_check(run_id, f"feedback(iter={i})")
                    new_tasks = self._run_feedback(run_id, events)
                    if new_tasks == 0:
                        break
                    # 用新任务再次 Hunt
                    self._budget_check(run_id, f"feedback-hunt(iter={i})")
                    feedback_tasks = self.state.get_pending_tasks(run_id)
                    if feedback_tasks:
                        self._run_hunt(run_id, events, [t.raw_json for t in feedback_tasks])

            # ─── Stage 5: Report ──────────────────────────
            self._budget_check(run_id, "report")
            report = self._run_report(run_id, events)

            # 完成
            duration_ms = int((time.time() - start_time) * 1000)
            self.state.finish_run(run_id, "completed")

            confirmed = self.state.get_confirmed_findings(run_id)
            all_findings = self.state.get_all_findings(run_id)

            return PipelineResult(
                run_id=run_id,
                status="completed",
                confirmed_findings=[f.raw_json for f in confirmed],
                rejected_count=len([f for f in all_findings if f.validation_status == "rejected"]),
                quality_score=report.get("metrics", {}).get("quality_score", 0),
                recommendations=report.get("recommendations", []),
                cost_summary=self.state.cost_summary(run_id),
                duration_ms=duration_ms,
                report=report,
            )

        except CostExceeded as e:
            logger.error("[%s] 预算耗尽: %s", run_id, e)
            self.state.finish_run(run_id, "aborted")
            duration_ms = int((time.time() - start_time) * 1000)
            return PipelineResult(
                run_id=run_id,
                status="aborted",
                confirmed_findings=[],
                rejected_count=0,
                quality_score=0,
                recommendations=["分析因预算限制中止"],
                cost_summary=self.state.cost_summary(run_id),
                duration_ms=duration_ms,
            )

        except Exception as e:
            logger.error("[%s] 流水线失败: %s", run_id, e, exc_info=True)
            self.state.finish_run(run_id, "failed")
            raise

    def _budget_check(self, run_id: str, stage: str) -> None:
        """检查 token 预算"""
        total = self.state.total_tokens(run_id)
        if total >= self.config.max_tokens:
            raise CostExceeded(
                f"预算耗尽于 {stage}: {total} >= {self.config.max_tokens} tokens"
            )

    # ─── Stage 实现 ───────────────────────────────────────

    def _run_recon(self, run_id: str, events: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Stage 1: Recon — 扫描事件，生成分析任务"""
        logger.info("[recon] 扫描 %d 个事件", len(events))

        if self.use_llm and self.runner:
            result = self.runner.run(
                stage="recon",
                system_prompt=get_agent_prompt("recon"),
                user_input={"events": events[:500]},  # 限制事件数避免 token 爆炸
                agent_type="recon",
                task_id="recon",
            )
            self.state.record_cost(run_id, "recon", None, result.input_tokens, result.output_tokens, result.duration_ms)
            payload = result.payload
        else:
            payload = self.rule_engine.run_recon(events)

        # 注册任务到 state
        for task in payload.get("analysis_tasks", []):
            self.state.add_task(run_id, task, source="recon")

        return payload

    def _run_hunt(
        self, run_id: str, events: List[Dict[str, Any]], tasks: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """Stage 2: Hunt — 多个窄 Agent 并行分析"""
        all_findings: List[Dict[str, Any]] = []

        for task in tasks[:self.config.max_hunt_agents]:
            agent_type = task["agent_type"]
            task_id = task["task_id"]
            logger.info("[hunt] 执行 %s (%s)", agent_type, task_id)

            try:
                if self.use_llm and self.runner:
                    result = self.runner.run(
                        stage="hunt",
                        system_prompt=get_agent_prompt(agent_type),
                        user_input={"events": events[:200], "task": task},
                        agent_type=agent_type,
                        task_id=task_id,
                    )
                    self.state.record_cost(run_id, "hunt", task_id, result.input_tokens, result.output_tokens, result.duration_ms)
                    hunt_result = result.payload
                else:
                    hunt_result = self.rule_engine.run_hunt(agent_type, events, task)

                # 存储 findings
                for finding in hunt_result.get("findings", []):
                    self.state.add_finding(run_id, task_id, finding)
                    all_findings.append(finding)

                self.state.update_task_status(task_id, "completed")

            except Exception as e:
                logger.warning("[hunt] %s 失败: %s", task_id, e)
                self.state.update_task_status(task_id, "failed")

        logger.info("[hunt] 完成，共发现 %d 个问题", len(all_findings))
        return all_findings

    def _run_validate(
        self, run_id: str, events: List[Dict[str, Any]], findings: List[Dict[str, Any]]
    ) -> None:
        """Stage 3: Validate — 对抗验证（用不同 prompt 视角推翻 findings）"""
        unvalidated = self.state.get_unvalidated_findings(run_id)
        logger.info("[validate] 验证 %d 个 findings", len(unvalidated))

        for finding in unvalidated:
            try:
                if self.runner:
                    result = self.runner.run(
                        stage="validate",
                        system_prompt=get_agent_prompt("validate"),
                        user_input={
                            "finding": finding.raw_json,
                            "events_sample": events[:100],
                        },
                        agent_type="validate",
                        task_id=f"validate_{finding.finding_id}",
                    )
                    self.state.record_cost(run_id, "validate", finding.finding_id,
                                         result.input_tokens, result.output_tokens, result.duration_ms)
                    verdict = result.payload
                else:
                    # 规则模式不做对抗验证（直接保留）
                    verdict = {"verdict": "confirmed", "rationale": "规则引擎模式，跳过对抗验证"}

                self.state.set_validation(finding.finding_id, verdict["verdict"], verdict)

            except Exception as e:
                logger.warning("[validate] %s 验证失败: %s", finding.finding_id, e)
                # 验证失败的保留为 needs_more_info
                self.state.set_validation(finding.finding_id, "needs_more_info", {"error": str(e)})

    def _run_feedback(self, run_id: str, events: List[Dict[str, Any]]) -> int:
        """Stage 4: Feedback — 从已确认 findings 中提取模式，生成新任务"""
        confirmed = self.state.get_confirmed_findings(run_id)
        if not confirmed:
            return 0

        logger.info("[feedback] 从 %d 个 confirmed findings 中提取模式", len(confirmed))

        if self.use_llm and self.runner:
            result = self.runner.run(
                stage="feedback",
                system_prompt=get_agent_prompt("feedback"),
                user_input={
                    "confirmed_findings": [f.raw_json for f in confirmed],
                    "total_events": len(events),
                },
                agent_type="feedback",
                task_id="feedback",
            )
            self.state.record_cost(run_id, "feedback", None, result.input_tokens, result.output_tokens, result.duration_ms)
            feedback_result = result.payload
        else:
            # 规则引擎：简单的模式扩散
            feedback_result = self._rule_feedback(confirmed)

        new_tasks = feedback_result.get("new_tasks", [])
        for task in new_tasks:
            self.state.add_task(run_id, task, source="feedback")
            self.state.add_feedback_task(run_id, task.get("seeded_from", ""), task["task_id"],
                                         feedback_result.get("pattern_description", ""))

        logger.info("[feedback] 生成 %d 个新任务", len(new_tasks))
        return len(new_tasks)

    def _rule_feedback(self, confirmed) -> Dict[str, Any]:
        """规则引擎版 Feedback — 按 category 扩散"""
        from collections import Counter
        categories = Counter(f.category for f in confirmed)
        new_tasks = []
        for cat, count in categories.items():
            if count >= 2:
                # 同类问题出现 2+ 次，扩散检测
                agent_map = {
                    "flaky_test": "flaky_detector",
                    "performance_regression": "regression_detector",
                    "coverage_gap": "coverage_analyzer",
                    "assertion_gap": "coverage_analyzer",
                    "race_condition": "performance_analyzer",
                }
                agent_type = agent_map.get(cat, "coverage_analyzer")
                new_tasks.append({
                    "task_id": f"fb_{cat}_{uuid.uuid4().hex[:6]}",
                    "agent_type": agent_type,
                    "scope_hint": f"扩散检测: {cat} 已出现 {count} 次，检查同类问题",
                    "seeded_from": next(f.finding_id for f in confirmed if f.category == cat),
                    "priority": 2,
                })
        return {
            "new_tasks": new_tasks,
            "pattern_description": f"发现 {len(categories)} 类问题模式",
        }

    def _run_report(self, run_id: str, events: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Stage 5: Report — 汇总报告"""
        confirmed = self.state.get_confirmed_findings(run_id)
        all_findings = self.state.get_all_findings(run_id)

        if self.use_llm and self.runner:
            result = self.runner.run(
                stage="report",
                system_prompt=get_agent_prompt("report"),
                user_input={
                    "confirmed_findings": [f.raw_json for f in confirmed],
                    "total_findings": len(all_findings),
                    "total_events": len(events),
                },
                agent_type="report",
                task_id="report",
            )
            self.state.record_cost(run_id, "report", None, result.input_tokens, result.output_tokens, result.duration_ms)
            return result.payload

        # 规则引擎版报告
        return self._rule_report(confirmed, all_findings, events)

    def _rule_report(self, confirmed, all_findings, events) -> Dict[str, Any]:
        """规则引擎版 Report"""
        severity_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
        sorted_findings = sorted(confirmed, key=lambda f: severity_order.get(f.severity, 99))

        # 质量分：100 - 每个 confirmed finding 扣分
        score = 100.0
        for f in confirmed:
            if f.severity == "critical":
                score -= 20
            elif f.severity == "high":
                score -= 10
            elif f.severity == "medium":
                score -= 5
            elif f.severity == "low":
                score -= 2
        score = max(0, score)

        recs = []
        categories = {f.category for f in confirmed}
        if "flaky_test" in categories:
            recs.append("存在 flaky test，建议优先修复以提高 CI 稳定性")
        if "performance_regression" in categories:
            recs.append("存在性能回归，建议检查最近的代码变更")
        if "coverage_gap" in categories:
            recs.append("存在覆盖盲区，建议补充测试用例")
        if "assertion_gap" in categories:
            recs.append("存在无断言的测试，建议添加验证逻辑")

        return {
            "title": "测试质量分析报告",
            "executive_summary": f"分析了 {len(events)} 个事件，发现 {len(confirmed)} 个已确认问题，质量分 {score:.0f}/100",
            "confirmed_findings": [
                {
                    "finding_id": f.finding_id,
                    "category": f.category,
                    "severity": f.severity,
                    "description": f.description,
                    "impact": f.description,
                    "affected_tests": f.affected_tests,
                    "affected_projects": f.affected_projects,
                    "suggested_fix": f.raw_json.get("suggested_fix", ""),
                    "validation_rationale": f.validation_json.get("rationale", "") if f.validation_json else "",
                }
                for f in sorted_findings
            ],
            "rejected_count": len([f for f in all_findings if f.validation_status == "rejected"]),
            "metrics": {
                "total_analyzed": len(all_findings),
                "confirmed": len(confirmed),
                "rejected": len([f for f in all_findings if f.validation_status == "rejected"]),
                "needs_more_info": len([f for f in all_findings if f.validation_status == "needs_more_info"]),
                "quality_score": score,
            },
            "recommendations": recs,
        }
