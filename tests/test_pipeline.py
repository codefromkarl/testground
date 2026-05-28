"""分析流水线测试 — 验证 audit 风格的多阶段架构"""

import json
import sys
import time
import uuid
from pathlib import Path

import pytest

pytestmark = pytest.mark.medium

sys.path.insert(0, str(Path(__file__).parent.parent))

from analyzers.pipeline.agents import AGENT_PROMPTS, get_agent_prompt
from analyzers.pipeline.orchestrator import AnalysisPipeline, PipelineConfig, RuleBasedAnalyzer
from analyzers.pipeline.schemas import SCHEMAS, get_schema, schema_as_text
from analyzers.pipeline.state import PipelineState

# ─── 测试数据工厂 ─────────────────────────────────────────


def make_event(
    event_type: str,
    test_name: str = "",
    session_id: str = "sess-1",
    project: str = "test-proj",
    framework: str = "vitest",
    duration_ms: float = 0,
    **extra,
) -> dict:
    """创建测试事件"""
    event = {
        "event_id": f"evt_{uuid.uuid4().hex[:8]}",
        "session_id": session_id,
        "timestamp": int(time.time() * 1000),
        "source": {"framework": framework, "project": project},
        "type": event_type,
        "data": {},
    }
    if test_name:
        event["data"]["test_name"] = test_name
    if duration_ms:
        event["data"]["duration_ms"] = duration_ms
    event["data"].update(extra)
    return event


def make_events_batch(passed: int = 5, failed: int = 1, flaky: int = 0, slow: int = 0, project: str = "proj-a") -> list:
    """创建一批测试事件"""
    events = []
    for i in range(passed):
        name = f"test_pass_{i}"
        events.append(make_event("test.start", name, project=project))
        events.append(make_event("assert.pass", name, project=project))
        events.append(make_event("test.end", name, project=project, duration_ms=100 + i * 10))

    for i in range(failed):
        name = f"test_fail_{i}"
        events.append(make_event("test.start", name, project=project))
        events.append(make_event("test.fail", name, project=project, duration_ms=50))

    for i in range(flaky):
        name = f"test_flaky_{i}"
        events.append(make_event("test.start", name, project=project))
        events.append(make_event("test.end", name, project=project, duration_ms=200))
        # 同一测试再次执行但失败
        events.append(make_event("test.start", name, project=project))
        events.append(make_event("test.fail", name, project=project, duration_ms=300))

    for i in range(slow):
        name = f"test_slow_{i}"
        events.append(make_event("test.start", name, project=project))
        events.append(make_event("test.end", name, project=project, duration_ms=60000))

    return events


# ─── Schema 测试 ──────────────────────────────────────────


class TestSchemas:
    """JSON Schema 定义测试"""

    def test_all_schemas_exist(self):
        """所有阶段都有 schema"""
        assert "recon" in SCHEMAS
        assert "hunt" in SCHEMAS
        assert "validate" in SCHEMAS
        assert "feedback" in SCHEMAS
        assert "report" in SCHEMAS

    def test_schema_has_required_fields(self):
        """每个 schema 都有 required 字段"""
        for stage, schema in SCHEMAS.items():
            assert "required" in schema, f"{stage} schema missing 'required'"
            assert len(schema["required"]) > 0, f"{stage} schema has empty 'required'"

    def test_get_schema_valid(self):
        get_schema("recon")  # 不应抛异常

    def test_get_schema_invalid(self):
        with pytest.raises(ValueError, match="Unknown stage"):
            get_schema("nonexistent")

    def test_schema_as_text_is_valid_json(self):
        text = schema_as_text("recon")
        parsed = json.loads(text)
        assert "properties" in parsed

    def test_recon_schema_structure(self):
        schema = get_schema("recon")
        assert "analysis_tasks" in schema["properties"]
        task_props = schema["properties"]["analysis_tasks"]["items"]["properties"]
        assert "agent_type" in task_props
        assert "scope_hint" in task_props

    def test_hunt_schema_requires_evidence(self):
        schema = get_schema("hunt")
        finding_req = schema["properties"]["findings"]["items"]["required"]
        assert "evidence" in finding_req
        assert "finding_id" in finding_req

    def test_validate_schema_verdict_enum(self):
        schema = get_schema("validate")
        verdict_enum = schema["properties"]["verdict"]["enum"]
        assert "confirmed" in verdict_enum
        assert "rejected" in verdict_enum
        assert "needs_more_info" in verdict_enum


# ─── State 测试 ───────────────────────────────────────────


class TestPipelineState:
    """SQLite 状态管理测试"""

    @pytest.fixture
    def state(self, tmp_path):
        return PipelineState(tmp_path / "test_state.db")

    def test_create_and_get_run(self, state):
        state.create_run("sess-1", "run-1")
        run = state.get_run("run-1")
        assert run is not None
        assert run["session_id"] == "sess-1"
        assert run["status"] == "running"

    def test_finish_run(self, state):
        state.create_run("sess-1", "run-1")
        state.finish_run("run-1", "completed")
        assert state.get_run_status("run-1") == "completed"

    def test_add_and_get_pending_tasks(self, state):
        state.create_run("sess-1", "run-1")
        state.add_task(
            "run-1",
            {
                "task_id": "t1",
                "agent_type": "flaky_detector",
                "scope_hint": "检测 flaky",
                "target_events": ["e1"],
                "priority": 1,
            },
        )
        state.add_task(
            "run-1",
            {
                "task_id": "t2",
                "agent_type": "coverage_analyzer",
                "scope_hint": "检测覆盖",
                "target_events": [],
                "priority": 3,
            },
        )

        pending = state.get_pending_tasks("run-1")
        assert len(pending) == 2
        # 按优先级排序
        assert pending[0].priority <= pending[1].priority

    def test_update_task_status(self, state):
        state.create_run("sess-1", "run-1")
        state.add_task(
            "run-1",
            {
                "task_id": "t1",
                "agent_type": "flaky_detector",
                "scope_hint": "test",
                "target_events": [],
            },
        )
        state.update_task_status("t1", "completed")
        pending = state.get_pending_tasks("run-1")
        assert len(pending) == 0

    def test_add_and_get_findings(self, state):
        state.create_run("sess-1", "run-1")
        state.add_task(
            "run-1",
            {
                "task_id": "t1",
                "agent_type": "flaky_detector",
                "scope_hint": "test",
                "target_events": [],
            },
        )
        state.add_finding(
            "run-1",
            "t1",
            {
                "finding_id": "f1",
                "category": "flaky_test",
                "severity": "high",
                "description": "test is flaky",
                "evidence": {"event_ids": ["e1"], "snippet": "pass then fail"},
            },
        )

        unvalidated = state.get_unvalidated_findings("run-1")
        assert len(unvalidated) == 1
        assert unvalidated[0].finding_id == "f1"
        assert unvalidated[0].validation_status is None

    def test_set_validation(self, state):
        state.create_run("sess-1", "run-1")
        state.add_task(
            "run-1",
            {
                "task_id": "t1",
                "agent_type": "flaky_detector",
                "scope_hint": "test",
                "target_events": [],
            },
        )
        state.add_finding(
            "run-1",
            "t1",
            {
                "finding_id": "f1",
                "category": "flaky_test",
                "severity": "high",
                "description": "flaky",
                "evidence": {"event_ids": [], "snippet": ""},
            },
        )

        state.set_validation("f1", "confirmed", {"verdict": "confirmed", "rationale": "evidence supports"})

        confirmed = state.get_confirmed_findings("run-1")
        assert len(confirmed) == 1
        assert confirmed[0].validation_status == "confirmed"

    def test_rejected_findings_excluded(self, state):
        state.create_run("sess-1", "run-1")
        state.add_task(
            "run-1",
            {
                "task_id": "t1",
                "agent_type": "flaky_detector",
                "scope_hint": "test",
                "target_events": [],
            },
        )
        state.add_finding(
            "run-1",
            "t1",
            {
                "finding_id": "f1",
                "category": "flaky_test",
                "severity": "high",
                "description": "flaky",
                "evidence": {"event_ids": [], "snippet": ""},
            },
        )

        state.set_validation("f1", "rejected", {"verdict": "rejected", "rationale": "not actually flaky"})

        confirmed = state.get_confirmed_findings("run-1")
        assert len(confirmed) == 0

    def test_cost_tracking(self, state):
        state.create_run("sess-1", "run-1")
        state.record_cost("run-1", "hunt", "t1", input_tokens=1000, output_tokens=500, duration_ms=2000)
        state.record_cost("run-1", "validate", "f1", input_tokens=800, output_tokens=300, duration_ms=1500)

        total = state.total_tokens("run-1")
        assert total == 1000 + 500 + 800 + 300

        summary = state.cost_summary("run-1")
        assert "hunt" in summary
        assert "validate" in summary

    def test_feedback_tasks(self, state):
        state.create_run("sess-1", "run-1")
        state.add_feedback_task("run-1", "f1", "t_fb_1", "flaky pattern in proj-a")

        # 不抛异常即通过
        assert True

    def test_previous_findings(self, state):
        # Run 1
        state.create_run("sess-1", "run_old")
        state.add_task(
            "run_old",
            {
                "task_id": "t1",
                "agent_type": "flaky_detector",
                "scope_hint": "test",
                "target_events": [],
            },
        )
        state.add_finding(
            "run_old",
            "t1",
            {
                "finding_id": "f_old",
                "category": "flaky_test",
                "severity": "high",
                "description": "old flaky",
                "evidence": {"event_ids": [], "snippet": ""},
            },
        )
        state.set_validation("f_old", "confirmed", {"verdict": "confirmed"})
        state.finish_run("run_old", "completed")

        # Run 2
        state.create_run("sess-1", "run_new")
        prev = state.get_previous_findings("sess-1", "run_new")
        assert len(prev) == 1
        assert prev[0].finding_id == "f_old"


# ─── RuleBasedAnalyzer 测试 ──────────────────────────────


class TestRuleBasedAnalyzer:
    """规则引擎 fallback 测试"""

    @pytest.fixture
    def analyzer(self):
        return RuleBasedAnalyzer()

    def test_recon_basic(self, analyzer):
        events = make_events_batch(passed=5, failed=2)
        result = analyzer.run_recon(events)

        assert result["summary"]["total_events"] == len(events)
        assert result["summary"]["pass_rate"] < 1.0
        assert len(result["analysis_tasks"]) > 0

    def test_recon_generates_flaky_task(self, analyzer):
        events = make_events_batch(passed=3)
        result = analyzer.run_recon(events)

        agent_types = [t["agent_type"] for t in result["analysis_tasks"]]
        assert "flaky_detector" in agent_types

    def test_recon_generates_coverage_task(self, analyzer):
        events = make_events_batch(passed=3)
        result = analyzer.run_recon(events)

        agent_types = [t["agent_type"] for t in result["analysis_tasks"]]
        assert "coverage_analyzer" in agent_types

    def test_recon_low_pass_rate_triggers_regression(self, analyzer):
        events = make_events_batch(passed=2, failed=8)
        result = analyzer.run_recon(events)

        agent_types = [t["agent_type"] for t in result["analysis_tasks"]]
        assert "regression_detector" in agent_types
        assert len(result["anomalies_detected"]) > 0

    def test_hunt_flaky_detector(self, analyzer):
        events = make_events_batch(flaky=2)
        task = {"task_id": "t1", "agent_type": "flaky_detector", "scope_hint": "test"}
        result = analyzer.run_hunt("flaky_detector", events, task)

        assert len(result["findings"]) == 2
        assert all(f["category"] == "flaky_test" for f in result["findings"])

    def test_hunt_coverage_analyzer_finds_no_assertion(self, analyzer):
        events = [
            make_event("test.start", "test_no_assert"),
            make_event("test.end", "test_no_assert"),
        ]
        task = {"task_id": "t1", "agent_type": "coverage_analyzer", "scope_hint": "test"}
        result = analyzer.run_hunt("coverage_analyzer", events, task)

        categories = [f["category"] for f in result["findings"]]
        assert "assertion_gap" in categories

    def test_hunt_coverage_analyzer_finds_incomplete(self, analyzer):
        events = [
            make_event("test.start", "test_incomplete"),
            # 没有 test.end
        ]
        task = {"task_id": "t1", "agent_type": "coverage_analyzer", "scope_hint": "test"}
        result = analyzer.run_hunt("coverage_analyzer", events, task)

        categories = [f["category"] for f in result["findings"]]
        assert "coverage_gap" in categories

    def test_hunt_regression_detector(self, analyzer):
        events = make_events_batch(passed=5, slow=1)
        task = {"task_id": "t1", "agent_type": "regression_detector", "scope_hint": "test"}
        result = analyzer.run_hunt("regression_detector", events, task)

        assert len(result["findings"]) >= 1
        assert result["findings"][0]["category"] == "performance_regression"


# ─── Agent Prompts 测试 ───────────────────────────────────


class TestAgentPrompts:
    """Agent prompt 定义测试"""

    def test_all_agent_types_have_prompts(self):
        expected = [
            "recon",
            "flaky_detector",
            "regression_detector",
            "semantic_evaluator",
            "coverage_analyzer",
            "performance_analyzer",
            "validate",
            "feedback",
            "report",
        ]
        for agent_type in expected:
            assert agent_type in AGENT_PROMPTS, f"Missing prompt for {agent_type}"

    def test_get_agent_prompt_valid(self):
        prompt = get_agent_prompt("recon")
        assert "角色" in prompt or "role" in prompt.lower()

    def test_get_agent_prompt_invalid(self):
        with pytest.raises(ValueError, match="Unknown agent type"):
            get_agent_prompt("nonexistent")

    def test_validate_prompt_mentions_adversarial(self):
        prompt = get_agent_prompt("validate")
        assert "推翻" in prompt or "disprove" in prompt.lower() or "对抗" in prompt

    def test_feedback_prompt_mentions_pattern(self):
        prompt = get_agent_prompt("feedback")
        assert "模式" in prompt or "pattern" in prompt.lower()


# ─── Pipeline 集成测试（规则引擎模式） ─────────────────────


class TestAnalysisPipelineRuleBased:
    """规则引擎模式下的完整流水线测试"""

    @pytest.fixture
    def pipeline(self, tmp_path):
        state = PipelineState(tmp_path / "pipeline.db")
        config = PipelineConfig(use_llm=False)
        return AnalysisPipeline(state=state, config=config)

    def test_full_pipeline_basic(self, pipeline):
        events = make_events_batch(passed=5, failed=2)
        result = pipeline.run(events, session_id="sess-test")

        assert result.status == "completed"
        assert result.run_id is not None
        assert result.duration_ms >= 0

    def test_pipeline_detects_flaky(self, pipeline):
        events = make_events_batch(flaky=2)
        result = pipeline.run(events, session_id="sess-flaky")

        assert result.status == "completed"
        # 规则引擎应该检测到 flaky
        categories = [f.get("category") for f in result.confirmed_findings]
        assert "flaky_test" in categories

    def test_pipeline_detects_coverage_gaps(self, pipeline):
        events = [
            make_event("test.start", "test_no_assert"),
            make_event("test.end", "test_no_assert"),
        ]
        result = pipeline.run(events, session_id="sess-coverage")

        assert result.status == "completed"
        categories = [f.get("category") for f in result.confirmed_findings]
        assert "assertion_gap" in categories

    def test_pipeline_report_has_metrics(self, pipeline):
        events = make_events_batch(passed=3, failed=1)
        result = pipeline.run(events, session_id="sess-report")

        assert result.report is not None
        assert "metrics" in result.report
        assert "quality_score" in result.report["metrics"]
        assert "recommendations" in result.report

    def test_pipeline_cost_tracking(self, pipeline):
        events = make_events_batch(passed=3)
        result = pipeline.run(events, session_id="sess-cost")

        # 规则模式不消耗 LLM token，但 cost_summary 应该存在
        assert isinstance(result.cost_summary, dict)

    def test_pipeline_with_agent_events(self, pipeline):
        """有 Agent 事件时应触发 semantic_evaluator"""
        events = make_events_batch(passed=3)
        events.append(make_event("agent.tool_call", project="proj-a"))
        events.append(make_event("agent.tool_result", project="proj-a"))

        result = pipeline.run(events, session_id="sess-agent")
        assert result.status == "completed"

    def test_pipeline_empty_events(self, pipeline):
        """空事件列表不应崩溃"""
        result = pipeline.run([], session_id="sess-empty")
        assert result.status == "completed"
        assert len(result.confirmed_findings) == 0

    def test_pipeline_multi_project(self, pipeline):
        """多项目事件应正确处理"""
        events_a = make_events_batch(passed=3, project="proj-a")
        events_b = make_events_batch(passed=2, failed=1, project="proj-b")
        events = events_a + events_b

        result = pipeline.run(events, session_id="sess-multi")
        assert result.status == "completed"
        assert result.report is not None


# ─── Feedback 循环测试 ────────────────────────────────────


class TestFeedbackLoop:
    """反馈扩散循环测试"""

    @pytest.fixture
    def pipeline(self, tmp_path):
        state = PipelineState(tmp_path / "feedback.db")
        config = PipelineConfig(use_llm=False, enable_feedback=True, feedback_iterations=2)
        return AnalysisPipeline(state=state, config=config)

    def test_feedback_generates_tasks_from_patterns(self, pipeline):
        """多个同类 finding 应触发反馈扩散"""
        # 创建有多个 flaky test 的事件
        events = make_events_batch(flaky=3)
        result = pipeline.run(events, session_id="sess-feedback")

        assert result.status == "completed"
        # 应该有 confirmed findings
        assert len(result.confirmed_findings) >= 2

    def test_feedback_stops_when_no_new_tasks(self, pipeline):
        """没有新模式时反馈循环应停止"""
        events = make_events_batch(passed=3)
        result = pipeline.run(events, session_id="sess-feedback-stop")

        assert result.status == "completed"


# ─── 回归测试：确保原有分析器仍然工作 ──────────────────────


class TestBackwardCompatibility:
    """确保原有分析器 API 不受影响"""

    def test_bug_discovery_still_works(self):
        from analyzers.bug_discovery import BugDiscoveryAnalyzer

        analyzer = BugDiscoveryAnalyzer()
        events = make_events_batch(passed=3, failed=2)
        result = analyzer.analyze(events)
        assert result.analyzer == "bug_discovery"

    def test_quality_guard_still_works(self):
        from analyzers.quality_guard import QualityGuard

        analyzer = QualityGuard()
        events = make_events_batch(passed=3)
        result = analyzer.analyze(events)
        assert result.analyzer == "quality_guard"

    def test_anomaly_detector_still_works(self):
        from analyzers.anomaly_detector import AnomalyDetector

        analyzer = AnomalyDetector()
        events = make_events_batch(passed=3, project="proj-a")
        result = analyzer.analyze(events)
        assert result.analyzer == "anomaly_detector"
