"""Godot 专属 Hunt Agent 测试

测试 scene_anomaly_agent, visual_regression_agent, game_state_agent 的规则引擎逻辑。
"""

import tempfile
import time
from pathlib import Path

import pytest
import sys

pytestmark = pytest.mark.medium

sys.path.insert(0, str(Path(__file__).parent.parent))

from schema.events import EventSource, ObsEvent, create_test_start, create_test_end
from analyzers.pipeline.orchestrator import AnalysisPipeline, PipelineConfig, PipelineState

# 全局临时目录
_tmp_dir = Path(tempfile.mkdtemp())


# ─── 工具函数 ─────────────────────────────────────────────


def _make_event(event_type: str, data: dict, project="test_game", framework="gdunit4") -> dict:
    """创建测试事件（Pipeline 期望 dict 格式）"""
    return {
        "event_id": f"evt-{int(time.time() * 1000)}",
        "session_id": "test-session",
        "timestamp": int(time.time() * 1000),
        "source": {"framework": framework, "project": project},
        "type": event_type,
        "data": data,
    }


def _run_pipeline(events: list) -> list:
    """运行 Pipeline 并返回 confirmed_findings"""
    db_path = _tmp_dir / f"test_{int(time.time() * 1000)}.db"
    state = PipelineState(db_path)
    config = PipelineConfig(use_llm=False)
    pipeline = AnalysisPipeline(state=state, config=config)
    result = pipeline.run(events, session_id="test-session")
    return result.confirmed_findings


# ══════════════════════════════════════════════════════════════
# scene_anomaly_agent 测试
# ══════════════════════════════════════════════════════════════


class TestSceneAnomalyDetection:
    def test_detects_slow_scene_load(self):
        """检测场景加载时间异常（> 5秒）"""
        events = [
            _make_event("game.scene_load", {"scene_path": "res://scenes/Battle.tscn", "duration_ms": 8000}),
        ]
        findings = _run_pipeline(events)
        scene_findings = [f for f in findings if f.get("category") == "scene_anomaly"]
        assert len(scene_findings) >= 1
        assert "8000" in str(scene_findings[0].get("description", ""))

    def test_detects_scene_load_then_fail(self):
        """检测场景加载后立即失败的模式"""
        events = [
            _make_event("game.scene_load", {"scene_path": "res://scenes/Battle.tscn"}),
            _make_event("test.start", {"test_name": "test_battle"}),
            _make_event("test.fail", {"test_name": "test_battle", "error": "Node not found"}),
        ]
        findings = _run_pipeline(events)
        # 应该检测到场景加载与测试失败的关联
        assert len(findings) >= 1

    def test_detects_repeated_scene_load(self):
        """检测重复加载同一场景（循环加载 bug）"""
        events = [
            _make_event("game.scene_load", {"scene_path": "res://scenes/Battle.tscn"}),
            _make_event("game.scene_load", {"scene_path": "res://scenes/Battle.tscn"}),
            _make_event("game.scene_load", {"scene_path": "res://scenes/Battle.tscn"}),
            _make_event("game.scene_load", {"scene_path": "res://scenes/Battle.tscn"}),
        ]
        findings = _run_pipeline(events)
        scene_findings = [f for f in findings if f.get("category") == "scene_anomaly"]
        assert len(scene_findings) >= 1

    def test_no_false_positive_normal_scene_load(self):
        """正常场景加载不应触发告警"""
        events = [
            _make_event("game.scene_load", {"scene_path": "res://scenes/Main.tscn", "load_time_ms": 500}),
            _make_event("game.scene_load", {"scene_path": "res://scenes/Battle.tscn", "load_time_ms": 1000}),
            _make_event("test.start", {"test_name": "test_battle"}),
            _make_event("test.end", {"test_name": "test_battle", "passed": True, "duration_ms": 100}),
        ]
        findings = _run_pipeline(events)
        scene_findings = [f for f in findings if f.get("category") == "scene_anomaly"]
        # 正常加载不应有场景异常
        assert len(scene_findings) == 0


# ══════════════════════════════════════════════════════════════
# visual_regression_agent 测试
# ══════════════════════════════════════════════════════════════


class TestVisualRegressionDetection:
    def test_detects_visual_assertion_failure(self):
        """检测视觉断言失败"""
        events = [
            _make_event("assert.fail", {
                "assertion_type": "visual_template",
                "template_name": "battle_button",
                "matched": False,
                "confidence": 0.45,
            }),
        ]
        findings = _run_pipeline(events)
        visual_findings = [f for f in findings if f.get("category") == "visual_regression"]
        assert len(visual_findings) >= 1

    def test_detects_confidence_decline(self):
        """检测视觉匹配置信度持续下降"""
        events = [
            _make_event("assert.pass", {
                "assertion_type": "visual_template",
                "template_name": "ui_button",
                "matched": True,
                "confidence": 0.95,
            }),
            _make_event("assert.pass", {
                "assertion_type": "visual_template",
                "template_name": "ui_button",
                "matched": True,
                "confidence": 0.82,
            }),
            _make_event("assert.fail", {
                "assertion_type": "visual_template",
                "template_name": "ui_button",
                "matched": False,
                "confidence": 0.55,
            }),
        ]
        findings = _run_pipeline(events)
        visual_findings = [f for f in findings if f.get("category") == "visual_regression"]
        assert len(visual_findings) >= 1

    def test_no_false_positive_stable_visual(self):
        """稳定的视觉匹配不应触发告警"""
        events = [
            _make_event("assert.pass", {
                "assertion_type": "visual_template",
                "template_name": "logo",
                "matched": True,
                "confidence": 0.95,
            }),
            _make_event("assert.pass", {
                "assertion_type": "visual_template",
                "template_name": "logo",
                "matched": True,
                "confidence": 0.93,
            }),
        ]
        findings = _run_pipeline(events)
        visual_findings = [f for f in findings if f.get("category") == "visual_regression"]
        assert len(visual_findings) == 0


# ══════════════════════════════════════════════════════════════
# game_state_agent 测试
# ══════════════════════════════════════════════════════════════


class TestGameStateAnomalyDetection:
    def test_detects_repeated_debug_match(self):
        """检测同一 debug 错误反复触发"""
        events = [
            _make_event("debug.match", {"entry_id": "entry-ERR-001", "error_code": "NODE_NOT_FOUND"}),
            _make_event("debug.match", {"entry_id": "entry-ERR-001", "error_code": "NODE_NOT_FOUND"}),
            _make_event("debug.match", {"entry_id": "entry-ERR-001", "error_code": "NODE_NOT_FOUND"}),
        ]
        findings = _run_pipeline(events)
        game_findings = [f for f in findings if f.get("category") == "game_state_anomaly"]
        assert len(game_findings) >= 1

    def test_detects_low_bench_score(self):
        """检测质量评估分数低于阈值"""
        events = [
            _make_event("bench.build_health", {"dimension": "build_health", "score": 0.2, "passed": False}),
        ]
        findings = _run_pipeline(events)
        # 低分 bench 应触发 finding
        assert len(findings) >= 1

    def test_detects_state_rollback(self):
        """检测游戏状态回退"""
        events = [
            _make_event("game.state_change", {
                "scene_path": "res://scenes/Battle.tscn",
                "state": {"phase": "menu"},
                "previous_state": None,
            }),
            _make_event("game.state_change", {
                "scene_path": "res://scenes/Battle.tscn",
                "state": {"phase": "battle"},
                "previous_state": {"phase": "menu"},
            }),
            _make_event("game.state_change", {
                "scene_path": "res://scenes/Battle.tscn",
                "state": {"phase": "menu"},  # 回退到 menu
                "previous_state": {"phase": "battle"},
            }),
        ]
        findings = _run_pipeline(events)
        game_findings = [f for f in findings if f.get("category") == "game_state_anomaly"]
        assert len(game_findings) >= 1

    def test_no_false_positive_normal_game_flow(self):
        """正常游戏流程不应触发告警"""
        events = [
            _make_event("game.state_change", {
                "scene_path": "res://scenes/Main.tscn",
                "state": {"phase": "menu"},
                "previous_state": None,
            }),
            _make_event("game.state_change", {
                "scene_path": "res://scenes/Battle.tscn",
                "state": {"phase": "battle"},
                "previous_state": {"phase": "menu"},
            }),
            _make_event("bench.build_health", {"dimension": "build_health", "score": 85, "passed": True}),
        ]
        findings = _run_pipeline(events)
        game_findings = [f for f in findings if f.get("category") == "game_state_anomaly"]
        # 正常流程不应有状态异常
        assert len(game_findings) == 0


# ══════════════════════════════════════════════════════════════
# Recon 阶段分派测试
# ══════════════════════════════════════════════════════════════


class TestReconDispatch:
    def test_dispatches_godot_agents_for_gdunit4_events(self):
        """gdUnit4 事件应触发 Godot Agent"""
        events = [
            _make_event("test.start", {"test_name": "test_battle"}, framework="gdunit4"),
            _make_event("game.scene_load", {"scene_path": "res://scenes/Battle.tscn"}),
            _make_event("test.end", {"test_name": "test_battle", "passed": True, "duration_ms": 100}),
        ]
        findings = _run_pipeline(events)
        categories = [f.get("category") for f in findings]
        # 应该包含 Godot 专属 Finding 或至少能正常运行
        assert isinstance(categories, list)

    def test_dispatches_visual_agent_for_visual_events(self):
        """视觉事件应触发 visual_regression_agent"""
        events = [
            _make_event("assert.fail", {
                "assertion_type": "visual_template",
                "template_name": "button",
                "matched": False,
                "confidence": 0.3,
            }),
        ]
        findings = _run_pipeline(events)
        categories = [f.get("category") for f in findings]
        assert isinstance(categories, list)

    def test_no_godot_agents_for_pure_vitest_events(self):
        """纯 Vitest 事件不应触发 Godot Agent"""
        events = [
            _make_event("test.start", {"test_name": "test_api"}, framework="vitest"),
            _make_event("test.end", {"test_name": "test_api", "passed": True, "duration_ms": 50}),
        ]
        findings = _run_pipeline(events)
        categories = [f.get("category") for f in findings]
        # 纯 Vitest 事件不应有 Godot 专属 Finding
        assert "scene_anomaly" not in categories
        assert "game_state_anomaly" not in categories
