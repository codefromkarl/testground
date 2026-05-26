"""Pipeline Orchestrator — 分析流水线驱动器

受 audit 的 orchestrator.py 启发，实现测试分析的多阶段流水线：

  Recon → Hunt(并行) → Validate(对抗) → Feedback(扩散) → Report

每个阶段之间有成本检查，支持断点续跑。
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from ..llm_client import LLMClient, LLMConfig
from .agents import get_agent_prompt
from .runner import AgentRunner
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
            tasks.append(
                {
                    "task_id": f"t_flaky_{task_idx}",
                    "agent_type": "flaky_detector",
                    "scope_hint": f"全量事件中检测 flaky test（{total} 事件，{len(projects)} 项目）",
                    "target_events": [],
                    "priority": 1,
                }
            )
            task_idx += 1
            tasks.append(
                {
                    "task_id": f"t_coverage_{task_idx}",
                    "agent_type": "coverage_analyzer",
                    "scope_hint": "检测覆盖盲区和无断言测试",
                    "target_events": [],
                    "priority": 2,
                }
            )
            task_idx += 1

        # 通过率低时加高优先级
        if pass_rate < 0.8:
            tasks.append(
                {
                    "task_id": f"t_regression_{task_idx}",
                    "agent_type": "regression_detector",
                    "scope_hint": f"通过率 {pass_rate:.1%}，检测性能回归",
                    "target_events": [],
                    "priority": 1,
                }
            )

        # 有 agent 事件时加语义评估
        if type_counts.get("agent.tool_result", 0) > 0:
            tasks.append(
                {
                    "task_id": f"t_semantic_{task_idx}",
                    "agent_type": "semantic_evaluator",
                    "scope_hint": "评估 Agent 工具输出的语义质量",
                    "target_events": [],
                    "priority": 3,
                }
            )
            task_idx += 1

        # 事件间隔异常时加性能分析
        if total > 5:
            tasks.append(
                {
                    "task_id": f"t_perf_{task_idx}",
                    "agent_type": "performance_analyzer",
                    "scope_hint": "检测执行时间异常和阻塞",
                    "target_events": [],
                    "priority": 3,
                }
            )
            task_idx += 1

        # ─── Godot 专属 Agent 分派 ───────────────────────
        has_godot = "godot_e2e" in frameworks or "godot_driver" in frameworks or "gdunit4" in frameworks
        has_game_events = any(t.startswith("game.") for t in type_counts)
        has_visual_events = (
            type_counts.get("assert.fail", 0) > 0
            and any(
                e.get("data", {}).get("assertion_type") == "visual_template"
                for e in events
                if e["type"] == "assert.fail"
            )
        )
        has_debug_events = any(t.startswith("debug.") for t in type_counts)
        has_bench_events = any(t.startswith("bench.") for t in type_counts)

        if has_godot or has_game_events:
            tasks.append(
                {
                    "task_id": f"t_scene_{task_idx}",
                    "agent_type": "scene_anomaly_agent",
                    "scope_hint": "检测 Godot 场景加载异常（慢加载、循环加载、加载导致失败）",
                    "target_events": [],
                    "priority": 2,
                }
            )
            task_idx += 1

        if has_godot or has_game_events or has_debug_events or has_bench_events:
            tasks.append(
                {
                    "task_id": f"t_game_state_{task_idx}",
                    "agent_type": "game_state_agent",
                    "scope_hint": "检测 Godot 游戏状态异常（状态回退、debug 重复、bench 低分）",
                    "target_events": [],
                    "priority": 2,
                }
            )
            task_idx += 1

        if has_visual_events:
            tasks.append(
                {
                    "task_id": f"t_visual_{task_idx}",
                    "agent_type": "visual_regression_agent",
                    "scope_hint": "检测 Godot 视觉回归（模板匹配失败、confidence 下降）",
                    "target_events": [],
                    "priority": 2,
                }
            )
            task_idx += 1

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
            "anomalies_detected": ([f"通过率过低: {pass_rate:.1%}"] if pass_rate < 0.5 else []),
        }

    def run_hunt(self, agent_type: str, events: List[Dict[str, Any]], task: Dict[str, Any]) -> Dict[str, Any]:
        """规则引擎版 Hunt — 复用原有分析器逻辑"""

        findings = []

        if agent_type == "flaky_detector":
            findings.extend(self._detect_flaky(events))
        elif agent_type == "coverage_analyzer":
            findings.extend(self._detect_coverage_gaps(events))
        elif agent_type == "regression_detector":
            findings.extend(self._detect_regressions(events))
        elif agent_type == "performance_analyzer":
            findings.extend(self._detect_perf_issues(events))
        elif agent_type == "scene_anomaly_agent":
            findings.extend(self._detect_scene_anomalies(events))
        elif agent_type == "visual_regression_agent":
            findings.extend(self._detect_visual_regressions(events))
        elif agent_type == "game_state_agent":
            findings.extend(self._detect_game_state_anomalies(events))
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
                findings.append(
                    {
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
                    }
                )

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
            findings.append(
                {
                    "finding_id": f"incomplete_{hash(name) % 10000:04d}",
                    "category": "coverage_gap",
                    "severity": "high",
                    "description": f"测试 {name} 开始但未结束（可能崩溃或超时）",
                    "evidence": {"event_ids": [], "snippet": "test.start without test.end/test.fail"},
                    "affected_tests": [name],
                    "confidence": 0.95,
                }
            )

        # 无断言的测试
        for name in started:
            if not has_assertions.get(name):
                findings.append(
                    {
                        "finding_id": f"no_assert_{hash(name) % 10000:04d}",
                        "category": "assertion_gap",
                        "severity": "medium",
                        "description": f"测试 {name} 没有任何断言",
                        "evidence": {"event_ids": [], "snippet": "no assert.pass or assert.fail events"},
                        "affected_tests": [name],
                        "confidence": 0.85,
                    }
                )

        # 测试粒度过粗（超过 10 秒）
        for name, duration in test_durations.items():
            if duration > 10000:
                findings.append(
                    {
                        "finding_id": f"too_long_{hash(name) % 10000:04d}",
                        "category": "assertion_gap",
                        "severity": "low",
                        "description": f"测试 {name} 耗时 {duration:.0f}ms，建议拆分",
                        "evidence": {"event_ids": [], "snippet": f"duration={duration:.0f}ms"},
                        "affected_tests": [name],
                        "confidence": 0.8,
                    }
                )

        # 所有测试都通过 = 可能缺少错误路径测试
        has_any_fail = any(e["type"] == "test.fail" for e in events)
        if not has_any_fail and len(events) > 20:
            findings.append(
                {
                    "finding_id": "no_failure_tests_0000",
                    "category": "coverage_gap",
                    "severity": "low",
                    "description": "所有测试都通过了，可能缺少边界条件和错误路径测试",
                    "evidence": {"event_ids": [], "snippet": "no test.fail events in session"},
                    "affected_tests": [],
                    "confidence": 0.7,
                }
            )

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
                findings.append(
                    {
                        "finding_id": f"reg_{hash(name) % 10000:04d}",
                        "category": "performance_regression",
                        "severity": "high" if dur > 30000 else "medium",
                        "description": f"测试 {name} 耗时 {dur:.0f}ms（平均 {avg:.0f}ms）",
                        "evidence": {"event_ids": [], "snippet": f"duration={dur:.0f}ms, avg={avg:.0f}ms"},
                        "affected_tests": [name],
                        "confidence": 0.8,
                    }
                )

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
                    findings.append(
                        {
                            "finding_id": f"gap_{hash(str(max_gap)) % 10000:04d}",
                            "category": "race_condition",
                            "severity": "medium",
                            "description": f"检测到异常长间隔: {max_gap / 1000:.1f}秒（平均 {avg_gap / 1000:.1f}秒）",
                            "evidence": {
                                "event_ids": [],
                                "snippet": f"max_gap={max_gap:.0f}ms, avg_gap={avg_gap:.0f}ms",
                            },
                            "confidence": 0.7,
                        }
                    )

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
                findings.append(
                    {
                        "finding_id": f"slow_{hash(name) % 10000:04d}",
                        "category": "race_condition",
                        "severity": "medium" if dur < 30000 else "high",
                        "description": f"测试 {name} 耗时 {dur:.0f}ms，建议检查是否有不必要的等待",
                        "evidence": {"event_ids": [], "snippet": f"duration={dur:.0f}ms"},
                        "affected_tests": [name],
                        "confidence": 0.8,
                    }
                )

        return findings

    def _detect_scene_anomalies(self, events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """检测 Godot 场景加载异常"""
        findings = []
        # 收集 game.scene_load 事件，按 scene_path 索引
        scene_loads: Dict[str, List[Dict[str, Any]]] = {}  # scene_path -> [event]
        # 收集 test.fail 事件的 test_name 集合
        failed_tests: set = set()
        # 事件索引：event_id -> index
        event_idx_map: Dict[str, int] = {}

        for i, e in enumerate(events):
            event_idx_map[e.get("event_id", "")] = i
            if e["type"] == "game.scene_load":
                scene_path = e.get("data", {}).get("scene_path", e.get("data", {}).get("path", ""))
                if scene_path:
                    scene_loads.setdefault(scene_path, []).append(e)
            elif e["type"] == "test.fail":
                name = e.get("data", {}).get("test_name", "")
                if name:
                    failed_tests.add(name)

        for scene_path, loads in scene_loads.items():
            path_hash = hash(scene_path) % 10000

            # 1. 检测加载时间异常（> 5 秒）
            for load_event in loads:
                dur = load_event.get("data", {}).get("duration_ms", 0)
                if dur > 5000:
                    findings.append(
                        {
                            "finding_id": f"scene_slow_{path_hash:04d}",
                            "category": "scene_anomaly",
                            "severity": "medium" if dur < 15000 else "high",
                            "description": f"场景 {scene_path} 加载耗时 {dur:.0f}ms（阈值 5000ms）",
                            "evidence": {
                                "event_ids": [load_event.get("event_id", "")],
                                "snippet": f"scene_path={scene_path}, duration_ms={dur:.0f}",
                            },
                            "confidence": 0.9,
                        }
                    )

            # 2. 检测场景加载后立即 test.fail（加载导致失败）
            for load_event in loads:
                load_idx = event_idx_map.get(load_event.get("event_id", ""))
                if load_idx is None:
                    continue
                # 检查后续 3 个事件内是否有 test.fail
                for j in range(load_idx + 1, min(load_idx + 4, len(events))):
                    if events[j]["type"] == "test.fail":
                        fail_test = events[j].get("data", {}).get("test_name", "")
                        findings.append(
                            {
                                "finding_id": f"scene_fail_{path_hash:04d}",
                                "category": "scene_anomaly",
                                "severity": "high",
                                "description": f"场景 {scene_path} 加载后立即触发测试失败: {fail_test}",
                                "evidence": {
                                    "event_ids": [
                                        load_event.get("event_id", ""),
                                        events[j].get("event_id", ""),
                                    ],
                                    "snippet": f"scene_path={scene_path}, test={fail_test}, load_then_fail",
                                },
                                "affected_tests": [fail_test] if fail_test else [],
                                "confidence": 0.85,
                            }
                        )
                        break  # 只记录最近的一个失败

            # 3. 检测重复加载同一场景（短时间 3+ 次 = 循环加载）
            if len(loads) >= 3:
                timestamps = [e.get("timestamp", 0) for e in loads]
                timestamps.sort()
                # 检查是否有 3 次加载在 10 秒内
                for i in range(len(timestamps) - 2):
                    if timestamps[i + 2] - timestamps[i] < 10000:
                        findings.append(
                            {
                                "finding_id": f"scene_loop_{path_hash:04d}",
                                "category": "scene_anomaly",
                                "severity": "critical",
                                "description": f"场景 {scene_path} 短时间内重复加载 {len(loads)} 次（可能的循环加载 bug）",
                                "evidence": {
                                    "event_ids": [e.get("event_id", "") for e in loads[:5]],
                                    "snippet": f"scene_path={scene_path}, load_count={len(loads)}, time_span={timestamps[-1] - timestamps[0]}ms",
                                },
                                "confidence": 0.95,
                            }
                        )
                        break

        return findings

    def _detect_visual_regressions(self, events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """检测 Godot 视觉回归"""
        findings = []
        # 收集所有视觉断言事件
        visual_events: List[Dict[str, Any]] = []
        for e in events:
            if e["type"] in ("assert.fail", "assert.pass"):
                if e.get("data", {}).get("assertion_type") == "visual_template":
                    visual_events.append(e)

        if not visual_events:
            return findings

        # 按 template_name 分组
        template_events: Dict[str, List[Dict[str, Any]]] = {}
        for e in visual_events:
            template = e.get("data", {}).get("template_name", "unknown")
            template_events.setdefault(template, []).append(e)

        for template_name, tevents in template_events.items():
            template_hash = hash(template_name) % 10000

            # 1. 检测 assert.fail + visual_template
            failed = [e for e in tevents if e["type"] == "assert.fail"]
            if failed:
                findings.append(
                    {
                        "finding_id": f"visual_fail_{template_hash:04d}",
                        "category": "visual_regression",
                        "severity": "high",
                        "description": f"视觉模板 {template_name} 匹配失败 {len(failed)} 次",
                        "evidence": {
                            "event_ids": [e.get("event_id", "") for e in failed[:5]],
                            "snippet": f"template={template_name}, fail_count={len(failed)}, total={len(tevents)}",
                        },
                        "confidence": 0.9,
                    }
                )

            # 2. 检测 confidence 持续下降趋势
            confidences = []
            for e in tevents:
                c = e.get("data", {}).get("confidence", None)
                if c is not None:
                    confidences.append((e.get("timestamp", 0), c, e.get("event_id", "")))

            if len(confidences) >= 3:
                confidences.sort(key=lambda x: x[0])
                # 检查是否有连续 3 次下降
                declining = all(
                    confidences[i][1] > confidences[i + 1][1]
                    for i in range(len(confidences) - 1)
                )
                # 或者总体下降超过 30%
                first_c = confidences[0][1]
                last_c = confidences[-1][1]
                if declining and len(confidences) >= 3:
                    findings.append(
                        {
                            "finding_id": f"visual_decline_{template_hash:04d}",
                            "category": "visual_regression",
                            "severity": "high",
                            "description": f"视觉模板 {template_name} 的 confidence 连续下降: {first_c:.2f} -> {last_c:.2f}",
                            "evidence": {
                                "event_ids": [c[2] for c in confidences[:5]],
                                "snippet": f"template={template_name}, confidence: {first_c:.2f} -> {last_c:.2f}, trend=declining",
                            },
                            "confidence": 0.85,
                        }
                    )
                elif first_c - last_c > 0.3:
                    findings.append(
                        {
                            "finding_id": f"visual_drop_{template_hash:04d}",
                            "category": "visual_regression",
                            "severity": "medium",
                            "description": f"视觉模板 {template_name} 的 confidence 大幅下降: {first_c:.2f} -> {last_c:.2f}",
                            "evidence": {
                                "event_ids": [c[2] for c in confidences[:5]],
                                "snippet": f"template={template_name}, confidence: {first_c:.2f} -> {last_c:.2f}, drop={first_c - last_c:.2f}",
                            },
                            "confidence": 0.8,
                        }
                    )

            # 3. 检测同一 template 在不同 session 中匹配率变化
            sessions: Dict[str, Dict[str, int]] = {}  # session_id -> {pass: n, fail: n}
            for e in tevents:
                sid = e.get("session_id", "unknown")
                sessions.setdefault(sid, {"pass": 0, "fail": 0})
                if e["type"] == "assert.pass":
                    sessions[sid]["pass"] += 1
                elif e["type"] == "assert.fail":
                    sessions[sid]["fail"] += 1

            if len(sessions) >= 2:
                rates = []
                for sid, counts in sessions.items():
                    total = counts["pass"] + counts["fail"]
                    if total > 0:
                        rates.append((sid, counts["pass"] / total, total))

                if len(rates) >= 2:
                    rates.sort(key=lambda x: x[1])
                    min_rate = rates[0][1]
                    max_rate = rates[-1][1]
                    if max_rate - min_rate > 0.3:
                        findings.append(
                            {
                                "finding_id": f"visual_session_{template_hash:04d}",
                                "category": "visual_regression",
                                "severity": "medium",
                                "description": f"视觉模板 {template_name} 在不同 session 中匹配率变化大: {min_rate:.0%} ~ {max_rate:.0%}",
                                "evidence": {
                                    "event_ids": [],
                                    "snippet": f"template={template_name}, match_rate_range=[{min_rate:.2f}, {max_rate:.2f}], sessions={len(sessions)}",
                                },
                                "confidence": 0.75,
                            }
                        )

        return findings

    def _detect_game_state_anomalies(self, events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """检测 Godot 游戏状态异常"""
        findings = []

        # 1. 检测 game.state_change 中的状态回退和跳跃
        state_changes: List[Dict[str, Any]] = []
        for e in events:
            if e["type"] == "game.state_change":
                state_changes.append(e)

        if len(state_changes) >= 2:
            state_changes.sort(key=lambda x: x.get("timestamp", 0))
            # 检测状态回退：新状态 == 之前的 previous_state
            for i in range(1, len(state_changes)):
                prev_event = state_changes[i - 1]
                curr_event = state_changes[i]
                curr_state = curr_event.get("data", {}).get("state", {})
                prev_prev_state = prev_event.get("data", {}).get("previous_state")
                prev_state = prev_event.get("data", {}).get("state", {})

                # 状态回退：当前状态 == 上上个状态
                if prev_prev_state and curr_state == prev_prev_state:
                    scene = curr_event.get("data", {}).get("scene_path", "unknown")
                    findings.append(
                        {
                            "finding_id": f"state_rollback_{hash(str(curr_state)) % 10000:04d}",
                            "category": "game_state_anomaly",
                            "severity": "high",
                            "description": f"游戏状态回退: 状态从 {prev_state} 回退到 {curr_state}",
                            "evidence": {
                                "event_ids": [
                                    prev_event.get("event_id", ""),
                                    curr_event.get("event_id", ""),
                                ],
                                "snippet": f"scene={scene}, state={curr_state}, previous={prev_state}, rollback_detected",
                            },
                            "confidence": 0.85,
                        }
                    )

        # 2. 检测 debug.match 事件重复出现（同一 error_code 多次触发）
        debug_matches: Dict[str, List[Dict[str, Any]]] = {}  # error_code -> [event]
        for e in events:
            if e["type"] == "debug.match":
                error_code = e.get("data", {}).get("error_code", "unknown")
                debug_matches.setdefault(error_code, []).append(e)

        for error_code, match_events in debug_matches.items():
            if len(match_events) >= 2:
                findings.append(
                    {
                        "finding_id": f"debug_repeat_{hash(error_code) % 10000:04d}",
                        "category": "game_state_anomaly",
                        "severity": "high" if len(match_events) >= 3 else "medium",
                        "description": f"调试匹配重复出现: error_code={error_code} 触发 {len(match_events)} 次",
                        "evidence": {
                            "event_ids": [e.get("event_id", "") for e in match_events[:5]],
                            "snippet": f"error_code={error_code}, match_count={len(match_events)}",
                        },
                        "confidence": 0.9,
                    }
                )

        # 3. 检测 bench.* 维度分数低于阈值
        for e in events:
            if e["type"].startswith("bench."):
                score = e.get("data", {}).get("score", 1.0)
                dimension = e.get("data", {}).get("dimension", e["type"].replace("bench.", ""))

                if score < 0.3:
                    findings.append(
                        {
                            "finding_id": f"bench_critical_{hash(dimension) % 10000:04d}",
                            "category": "game_state_anomaly",
                            "severity": "critical",
                            "description": f"Bench 维度 {dimension} 分数极低: {score:.2f}（阈值 0.3）",
                            "evidence": {
                                "event_ids": [e.get("event_id", "")],
                                "snippet": f"dimension={dimension}, score={score:.2f}, threshold=0.3",
                            },
                            "confidence": 0.95,
                        }
                    )
                elif score < 0.6:
                    findings.append(
                        {
                            "finding_id": f"bench_low_{hash(dimension) % 10000:04d}",
                            "category": "game_state_anomaly",
                            "severity": "high",
                            "description": f"Bench 维度 {dimension} 分数偏低: {score:.2f}（阈值 0.6）",
                            "evidence": {
                                "event_ids": [e.get("event_id", "")],
                                "snippet": f"dimension={dimension}, score={score:.2f}, threshold=0.6",
                            },
                            "confidence": 0.9,
                        }
                    )

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
        self.state.create_run(
            session_id,
            run_id,
            {
                "use_llm": self.use_llm,
                "max_tokens": self.config.max_tokens,
            },
        )

        logger.info(
            "[%s] 开始分析流水线 (%s 模式, %d 事件)", run_id, "LLM" if self.use_llm else "规则引擎", len(events)
        )

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
                    self.state.set_validation(
                        f.finding_id,
                        "confirmed",
                        {
                            "verdict": "confirmed",
                            "rationale": "规则引擎模式，跳过对抗验证",
                        },
                    )

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
            raise CostExceeded(f"预算耗尽于 {stage}: {total} >= {self.config.max_tokens} tokens")

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

    def _run_hunt(self, run_id: str, events: List[Dict[str, Any]], tasks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Stage 2: Hunt — 多个窄 Agent 并行分析"""
        all_findings: List[Dict[str, Any]] = []

        for task in tasks[: self.config.max_hunt_agents]:
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
                    self.state.record_cost(
                        run_id, "hunt", task_id, result.input_tokens, result.output_tokens, result.duration_ms
                    )
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

    def _run_validate(self, run_id: str, events: List[Dict[str, Any]], findings: List[Dict[str, Any]]) -> None:
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
                    self.state.record_cost(
                        run_id,
                        "validate",
                        finding.finding_id,
                        result.input_tokens,
                        result.output_tokens,
                        result.duration_ms,
                    )
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
            self.state.record_cost(
                run_id, "feedback", None, result.input_tokens, result.output_tokens, result.duration_ms
            )
            feedback_result = result.payload
        else:
            # 规则引擎：简单的模式扩散
            feedback_result = self._rule_feedback(confirmed)

        new_tasks = feedback_result.get("new_tasks", [])
        for task in new_tasks:
            self.state.add_task(run_id, task, source="feedback")
            self.state.add_feedback_task(
                run_id, task.get("seeded_from", ""), task["task_id"], feedback_result.get("pattern_description", "")
            )

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
                    "scene_anomaly": "scene_anomaly_agent",
                    "visual_regression": "visual_regression_agent",
                    "game_state_anomaly": "game_state_agent",
                }
                agent_type = agent_map.get(cat, "coverage_analyzer")
                new_tasks.append(
                    {
                        "task_id": f"fb_{cat}_{uuid.uuid4().hex[:6]}",
                        "agent_type": agent_type,
                        "scope_hint": f"扩散检测: {cat} 已出现 {count} 次，检查同类问题",
                        "seeded_from": next(f.finding_id for f in confirmed if f.category == cat),
                        "priority": 2,
                    }
                )
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
            self.state.record_cost(
                run_id, "report", None, result.input_tokens, result.output_tokens, result.duration_ms
            )
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
        if "scene_anomaly" in categories:
            recs.append("存在场景加载异常，建议检查场景资源大小和加载逻辑")
        if "visual_regression" in categories:
            recs.append("存在视觉回归，建议检查 UI 资源变更和模板更新")
        if "game_state_anomaly" in categories:
            recs.append("存在游戏状态异常，建议检查状态机转换逻辑和 bench 指标")

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
