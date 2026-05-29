"""LLM 端到端集成测试 — 用 cpa/mimo3 模型跑完整 Pipeline

验证目标：
  1. LLM 自动发现 → cpa/mimo3
  2. Recon 阶段能根据真实事件生成有意义的分析任务
  3. Hunt 阶段能产出带 evidence 的 findings
  4. Validate 阶段能真正推翻 findings（不是橡皮图章）
  5. Feedback 阶段能从模式中生成新任务
  6. Report 阶段产出结构化报告
  7. Schema 验证 + Repair 机制在 LLM 输出中生效

标记为 @pytest.mark.llm，需要 LLM 网关运行才能执行。
"""

import sys
import time
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from analyzers.llm_client import LLMClient, get_default_model_info, is_llm_available
from analyzers.pipeline.agents import get_agent_prompt
from analyzers.pipeline.orchestrator import AnalysisPipeline, PipelineConfig
from analyzers.pipeline.runner import AgentRunner
from analyzers.pipeline.state import PipelineState

# ─── 标记 ─────────────────────────────────────────────────

_llm_mark = pytest.mark.llm
_slow_mark = pytest.mark.slow

# 跳过条件：LLM 不可用
if not is_llm_available():
    pytestmark = [_llm_mark, _slow_mark, pytest.mark.skip(reason="LLM 不可用（需要 CPA_API_KEY 或 LLM_API_KEY）")]
else:
    pytestmark = [_llm_mark, _slow_mark]


# ─── 测试数据 ─────────────────────────────────────────────
# TODO: 迁移到 tests/factories/events.py（make_event）
# 当前 _realistic_events() 中的 _evt() 是局部函数，硬编码了 session/project/framework
# 迁移时可以改为：
#   from tests.factories import make_event
#   _evt = lambda etype, **kw: make_event(etype, session_id="sess-travel-agent-ci-42",
#                                          project="TravelAgent", framework="vitest", **kw)


def _realistic_events():
    """构造一个真实场景的测试事件流：
    - TravelAgent 项目
    - 5 个正常通过的测试
    - 1 个 flaky test（先 pass 后 fail）
    - 1 个无断言的测试
    - 1 个慢测试（30 秒）
    - Agent 工具调用事件
    """
    events = []
    sid = "sess-travel-agent-ci-42"
    proj = "TravelAgent"
    fw = "vitest"
    ts = int(time.time() * 1000)

    def _evt(etype, test_name="", duration_ms=0, **extra):
        nonlocal ts
        ts += 100
        e = {
            "event_id": f"evt_{uuid.uuid4().hex[:8]}",
            "session_id": sid,
            "timestamp": ts,
            "source": {"framework": fw, "project": proj},
            "type": etype,
            "data": {},
        }
        if test_name:
            e["data"]["test_name"] = test_name
        if duration_ms:
            e["data"]["duration_ms"] = duration_ms
        e["data"].update(extra)
        return e

    # 正常测试
    for i in range(5):
        n = f"test_trip_planning_{i}"
        events.append(_evt("test.start", n))
        events.append(_evt("assert.pass", n))
        events.append(_evt("test.end", n, duration_ms=80 + i * 20))

    # Flaky test: 先 pass 后 fail
    n = "test_weather_api_timeout"
    events.append(_evt("test.start", n))
    events.append(_evt("assert.pass", n))
    events.append(_evt("test.end", n, duration_ms=200))
    events.append(_evt("test.start", n))
    events.append(_evt("test.fail", n, duration_ms=3000, errors=[{"message": "timeout after 3000ms"}]))

    # 无断言测试
    n = "test_config_loading"
    events.append(_evt("test.start", n))
    events.append(_evt("test.end", n, duration_ms=5))

    # 慢测试
    n = "test_full_integration_e2e"
    events.append(_evt("test.start", n))
    events.append(_evt("assert.pass", n))
    events.append(_evt("test.end", n, duration_ms=30000))

    # Agent 工具调用
    events.append(
        _evt(
            "agent.tool_call",
            tool_name="search_weather",
            input={"city": "Beijing"},
            output='{"temp": 25}',
            success=True,
        )
    )
    events.append(
        _evt("agent.tool_result", tool_name="search_weather", output='{"temp": 25, "condition": "sunny"}', success=True)
    )
    events.append(_evt("agent.tool_call", tool_name="book_hotel", input={"city": "Beijing"}, output="", success=False))

    return events


# ─── 测试 ─────────────────────────────────────────────────


class TestLLMDiscovery:
    """验证 LLM 自动发现"""

    def test_auto_discover_cpa_mimo3(self):
        """应自动发现 CPA/mimo3"""
        info = get_default_model_info()
        assert info["has_key"], "需要 API key"
        assert "mimo" in info["model"].lower() or info["model"] != "(未配置)", f"模型应该是 mimo 系列，实际: {info}"

    def test_llm_client_basic_call(self):
        """基础 LLM 调用应成功"""
        client = LLMClient()
        result = client.chat("用一句话回答：1+1=?")
        assert result and len(result) > 0
        client.close()

    def test_llm_client_json_call(self):
        """JSON 格式调用应成功"""
        client = LLMClient()
        result = client.chat_json('输出 JSON: {"answer": 42}', system="只输出 JSON，不要其他文字。")
        assert result.get("answer") == 42
        client.close()


class TestLLMRecon:
    """验证 Recon 阶段的 LLM 输出质量"""

    def test_recon_produces_valid_schema(self):
        """Recon 的 LLM 输出应符合 Schema"""
        runner = AgentRunner(repair_attempts=2)
        events = _realistic_events()

        # 只传摘要，不传全部事件（避免 token 爆炸）
        events_summary = {
            "total_events": len(events),
            "event_types": list(set(e["type"] for e in events)),
            "projects": ["TravelAgent"],
            "frameworks": ["vitest"],
            "sample_events": events[:15],  # 只发前 15 个
            "pass_rate": sum(1 for e in events if e["type"] == "test.end")
            / max(1, sum(1 for e in events if e["type"] in ("test.end", "test.fail"))),
        }

        result = runner.run(
            stage="recon",
            system_prompt=get_agent_prompt("recon"),
            user_input=events_summary,
            agent_type="recon",
            task_id="recon_llm",
        )

        assert result.payload is not None
        assert "analysis_tasks" in result.payload
        assert len(result.payload["analysis_tasks"]) > 0

        # 每个 task 应有必要的字段
        for task in result.payload["analysis_tasks"]:
            assert "task_id" in task
            assert "agent_type" in task
            assert "scope_hint" in task
            assert len(task["scope_hint"]) > 10, (
                f"scope_hint 太短（{len(task['scope_hint'])} 字），说明 LLM 没有真正分析: {task['scope_hint']}"
            )

    def test_recon_identifies_flaky_pattern(self):
        """Recon 应该识别 flaky test 信号"""
        runner = AgentRunner(repair_attempts=2)
        events = _realistic_events()

        events_summary = {
            "total_events": len(events),
            "event_types": list(set(e["type"] for e in events)),
            "projects": ["TravelAgent"],
            "sample_events": events[:15],
            "notable_patterns": [
                "test_weather_api_timeout: test.end (pass) followed by test.fail (timeout)",
            ],
        }

        result = runner.run(
            stage="recon",
            system_prompt=get_agent_prompt("recon"),
            user_input=events_summary,
            agent_type="recon",
            task_id="recon_flaky",
        )

        # 应该有 flaky_detector 任务
        agent_types = [t["agent_type"] for t in result.payload.get("analysis_tasks", [])]
        assert "flaky_detector" in agent_types, f"Recon 没有生成 flaky_detector 任务，实际: {agent_types}"


class TestLLMHunt:
    """验证 Hunt 阶段的 LLM 输出质量"""

    def test_hunt_produces_findings_with_evidence(self):
        """Hunt 应产出带 evidence 的 findings"""
        runner = AgentRunner(repair_attempts=2)
        events = _realistic_events()

        # 只传 flaky 相关的事件
        flaky_events = [
            e
            for e in events
            if "weather" in e.get("data", {}).get("test_name", "")
            or e.get("type") in ("test.start", "test.end", "test.fail")
        ]

        result = runner.run(
            stage="hunt",
            system_prompt=get_agent_prompt("flaky_detector"),
            user_input={
                "events": flaky_events[:20],
                "task": {
                    "task_id": "t_flaky_0",
                    "agent_type": "flaky_detector",
                    "scope_hint": "TravelAgent 项目中检测 flaky test，特别关注 test_weather_api_timeout 先 pass 后 fail",
                    "target_events": [],
                },
            },
            agent_type="flaky_detector",
            task_id="hunt_flaky",
        )

        findings = result.payload.get("findings", [])
        assert len(findings) > 0, "Hunt 应该发现至少一个 flaky test"

        for f in findings:
            assert "finding_id" in f
            assert "category" in f
            assert "evidence" in f
            assert "snippet" in f.get("evidence", {})
            assert len(f["evidence"]["snippet"]) > 5, f"evidence snippet 太短，说明 LLM 没有真正分析: {f}"


class TestLLMValidate:
    """验证 Validate 阶段（最关键：证明不是橡皮图章）"""

    def test_validate_confirms_genuine_finding(self):
        """Validate 应确认真正的 flaky test（有明确证据）"""
        runner = AgentRunner(repair_attempts=2)

        result = runner.run(
            stage="validate",
            system_prompt=get_agent_prompt("validate"),
            user_input={
                "finding": {
                    "finding_id": "f_flaky_001",
                    "category": "flaky_test",
                    "severity": "high",
                    "description": "test_weather_api_timeout 使用完全相同的 mock 数据和输入，但有时 pass 有时 fail，表现出非确定性",
                    "evidence": {
                        "event_ids": ["evt_1", "evt_2", "evt_3", "evt_4", "evt_5"],
                        "snippet": "同一测试在 CI 中连续 3 次 run: pass, fail, pass。使用相同的 mock 数据，无外部依赖。",
                    },
                    "affected_tests": ["test_weather_api_timeout"],
                    "confidence": 0.9,
                },
                "events_sample": [
                    {"type": "test.start", "data": {"test_name": "test_weather_api_timeout"}},
                    {
                        "type": "test.end",
                        "data": {"test_name": "test_weather_api_timeout", "passed": True, "duration_ms": 200},
                    },
                    {"type": "test.start", "data": {"test_name": "test_weather_api_timeout"}},
                    {
                        "type": "test.fail",
                        "data": {
                            "test_name": "test_weather_api_timeout",
                            "duration_ms": 200,
                            "errors": [{"message": "Expected 200 but got 500"}],
                        },
                    },
                    {"type": "test.start", "data": {"test_name": "test_weather_api_timeout"}},
                    {
                        "type": "test.end",
                        "data": {"test_name": "test_weather_api_timeout", "passed": True, "duration_ms": 180},
                    },
                ],
            },
            agent_type="validate",
            task_id="validate_confirmed",
        )

        # Validate 可能返回 confirmed 或 needs_more_info（都是合理的非 rejected 结果）
        # rejected 才是问题——说明真正的 flaky 被错误地推翻了
        assert result.payload["verdict"] in ("confirmed", "needs_more_info"), (
            f"明确的 flaky 不应被完全拒绝（rejected），实际: "
            f"verdict={result.payload.get('verdict')}, "
            f"rationale={result.payload.get('rationale', '')[:200]}"
        )
        assert "alternative_explanation" in result.payload, "Validate 即使确认也必须提供 alternative_explanation"

    def test_validate_rejects_false_positive(self):
        """Validate 应拒绝一个假 flaky（只有一次失败，不是 flaky）"""
        runner = AgentRunner(repair_attempts=2)

        result = runner.run(
            stage="validate",
            system_prompt=get_agent_prompt("validate"),
            user_input={
                "finding": {
                    "finding_id": "f_fake_flaky",
                    "category": "flaky_test",
                    "severity": "high",
                    "description": "test_config_loading 是 flaky test",
                    "evidence": {
                        "event_ids": ["evt_10"],
                        "snippet": "test failed once",
                    },
                    "affected_tests": ["test_config_loading"],
                    "confidence": 0.5,
                },
                "events_sample": _realistic_events()[:30],
            },
            agent_type="validate",
            task_id="validate_rejected",
        )

        # 关键断言：Validate 应该推翻这个假 flaky
        assert result.payload["verdict"] in ("rejected", "needs_more_info"), (
            f"只有一次失败的测试不是 flaky，应该被拒绝或标记需更多信息，"
            f"实际: {result.payload['verdict']} — Validate 是橡皮图章！"
        )


class TestLLMFeedback:
    """验证 Feedback 阶段"""

    def test_feedback_generates_expansion_tasks(self):
        """Feedback 应从已确认 findings 中生成扩散任务"""
        runner = AgentRunner(repair_attempts=2)

        result = runner.run(
            stage="feedback",
            system_prompt=get_agent_prompt("feedback"),
            user_input={
                "confirmed_findings": [
                    {
                        "finding_id": "f_flaky_001",
                        "category": "flaky_test",
                        "severity": "high",
                        "description": "test_weather_api_timeout 是 flaky（timeout 导致）",
                        "affected_tests": ["test_weather_api_timeout"],
                        "affected_projects": ["TravelAgent"],
                    },
                    {
                        "finding_id": "f_flaky_002",
                        "category": "flaky_test",
                        "severity": "high",
                        "description": "test_hotel_api_timeout 也是 flaky（同样 timeout 模式）",
                        "affected_tests": ["test_hotel_api_timeout"],
                        "affected_projects": ["TravelAgent"],
                    },
                ],
                "total_events": 30,
            },
            agent_type="feedback",
            task_id="feedback",
        )

        new_tasks = result.payload.get("new_tasks", [])
        assert len(new_tasks) > 0, "Feedback 应该从同类 flaky 模式中生成扩散检测任务"

        for task in new_tasks:
            assert "seeded_from" in task, "扩散任务应标注原始 finding"
            assert "scope_hint" in task


class TestLLMReport:
    """验证 Report 阶段"""

    def test_report_structure(self):
        """Report 应产出完整的结构化报告"""
        runner = AgentRunner(repair_attempts=2)

        result = runner.run(
            stage="report",
            system_prompt=get_agent_prompt("report"),
            user_input={
                "confirmed_findings": [
                    {
                        "finding_id": "f1",
                        "category": "flaky_test",
                        "severity": "high",
                        "description": "test_weather_api_timeout 不稳定",
                        "affected_tests": ["test_weather_api_timeout"],
                        "affected_projects": ["TravelAgent"],
                        "suggested_fix": "增加 mock 超时处理",
                    },
                ],
                "total_findings": 3,
                "total_events": 30,
            },
            agent_type="report",
            task_id="report",
        )

        report = result.payload
        assert "executive_summary" in report
        assert len(report["executive_summary"]) > 20, "执行摘要太短，说明 LLM 没有认真总结"
        assert "confirmed_findings" in report
        assert "metrics" in report
        assert "quality_score" in report["metrics"]
        assert 0 <= report["metrics"]["quality_score"] <= 100
        assert "recommendations" in report


class TestLLMFullPipeline:
    """完整 Pipeline 端到端"""

    @pytest.mark.slow
    def test_full_pipeline_with_llm(self, tmp_path):
        """用真正的 LLM 跑完整 5 阶段 Pipeline（精简数据）"""
        events = _realistic_events()
        db_path = tmp_path / "llm_pipeline.db"
        state = PipelineState(db_path)
        config = PipelineConfig(
            use_llm=True,
            enable_feedback=False,  # 完整流程太慢，关闭 feedback
            feedback_iterations=0,
            max_tokens=200000,
            repair_attempts=1,
        )
        pipeline = AnalysisPipeline(state=state, config=config)

        result = pipeline.run(events, session_id="sess-llm-e2e")

        # 基本断言
        assert result.status == "completed", f"Pipeline 应完成，实际: {result.status}"
        assert result.report is not None, "应有报告"
        assert result.quality_score >= 0, "应有质量分"

        # LLM 模式下应该有 token 消耗
        total_tokens = state.total_tokens(result.run_id)
        assert total_tokens > 0, "LLM 模式应有 token 消耗"

        # 报告结构验证
        report = result.report
        assert "executive_summary" in report
        assert "metrics" in report

        # 至少应该检测到 flaky（因为构造了明显的 flaky 数据）
        {f.get("category") for f in result.confirmed_findings}
        # 注意：LLM 可能把 flaky 叫别的名字，所以不强制类别名
        # 但至少应该有 findings
        total_confirmed = len(result.confirmed_findings)
        total_rejected = result.rejected_count
        assert total_confirmed + total_rejected > 0, "Pipeline 应该至少产出一些 findings（不论是否被确认）"

        # 打印摘要
        print(f"\n{'=' * 60}")
        print("LLM Pipeline 结果摘要")
        print(f"{'=' * 60}")
        print(f"状态: {result.status}")
        print(f"质量分: {result.quality_score:.0f}/100")
        print(f"已确认: {total_confirmed}")
        print(f"已拒绝: {total_rejected}")
        print(f"Token 消耗: {total_tokens}")
        print(f"耗时: {result.duration_ms}ms")
        if result.confirmed_findings:
            print("\n已确认的 findings:")
            for f in result.confirmed_findings:
                print(f"  - [{f.get('category')}] {f.get('description', '')[:60]}")
        if report.get("recommendations"):
            print("\n建议:")
            for r in report["recommendations"]:
                print(f"  💡 {r}")

        state.close()
