"""测试观测平台自身的测试"""

import json
import time
import uuid
from pathlib import Path

import pytest
import sys

# 将项目根目录加入 path
sys.path.insert(0, str(Path(__file__).parent.parent))

from schema.events import (
    EventSource,
    TestEvent,
    TestSession,
    create_agent_tool_call,
    create_assertion,
    create_bug_candidate,
    create_game_state_change,
    create_test_end,
    create_test_start,
)
from gateway.storage import Storage
from analyzers.bug_discovery import BugDiscoveryAnalyzer
from analyzers.quality_guard import QualityGuard
from analyzers.anomaly_detector import AnomalyDetector


# ─── Schema 测试 ────────────────────────────────────────────


class TestEventSchema:
    """测试事件模型"""

    def test_create_test_start(self):
        source = EventSource(framework="vitest", project="test-proj")
        event = create_test_start(
            session_id="sess-1",
            source=source,
            test_name="test_example",
            full_name="suite > test_example",
        )
        assert event.type == "test.start"
        assert event.session_id == "sess-1"
        assert event.source.framework == "vitest"
        assert event.data["test_name"] == "test_example"
        assert event.event_id  # 自动生成

    def test_create_test_end(self):
        source = EventSource(framework="gdunit4", project="godot-game")
        event = create_test_end(
            session_id="sess-2",
            source=source,
            test_name="test_battle",
            passed=True,
            duration_ms=150,
        )
        assert event.type == "test.end"
        assert event.data["passed"] is True
        assert event.data["duration_ms"] == 150

    def test_create_test_fail(self):
        source = EventSource(framework="vitest", project="travel-agent")
        event = create_test_end(
            session_id="sess-3",
            source=source,
            test_name="test_weather",
            passed=False,
            duration_ms=50,
            errors=[{"message": "expected sun, got rain"}],
        )
        assert event.type == "test.fail"
        assert len(event.data["errors"]) == 1

    def test_create_assertion(self):
        source = EventSource(framework="custom", project="loopexpedition")
        event = create_assertion(
            session_id="sess-4",
            source=source,
            assertion_name="hp_should_be_positive",
            passed=True,
            expected=">0",
            actual="100",
        )
        assert event.type == "assert.pass"
        assert event.data["assertion_name"] == "hp_should_be_positive"

    def test_create_game_state(self):
        source = EventSource(framework="gdunit4", project="pogongshichongzou")
        event = create_game_state_change(
            session_id="sess-5",
            source=source,
            scene_path="/root/BattleScene",
            state={"hp": 100, "mp": 50},
            previous_state={"hp": 100, "mp": 60},
        )
        assert event.type == "game.state_change"
        assert event.data["state"]["hp"] == 100

    def test_create_bug_candidate(self):
        source = EventSource(framework="custom", project="loopexpedition")
        event = create_bug_candidate(
            session_id="sess-6",
            source=source,
            severity="high",
            category="stuck_state",
            description="玩家卡在墙里",
            evidence={"position": [100, 200], "velocity": [0, 0]},
        )
        assert event.type == "report.bug_candidate"
        assert event.data["severity"] == "high"

    def test_to_dict_roundtrip(self):
        source = EventSource(framework="vitest", project="travel-agent")
        event = create_test_start(
            session_id="sess-7",
            source=source,
            test_name="test_roundtrip",
            full_name="test_roundtrip",
        )
        d = event.to_dict()
        assert d["event_id"] == event.event_id
        assert d["type"] == "test.start"
        assert d["source"]["framework"] == "vitest"

    def test_trace_id_propagation(self):
        source = EventSource(framework="vitest", project="travel-agent")
        trace_id = f"trace_{uuid.uuid4().hex[:12]}"
        event = create_test_start(
            session_id="sess-8",
            source=source,
            test_name="test_trace",
            full_name="test_trace",
            trace_id=trace_id,
        )
        assert event.trace_id == trace_id


# ─── Storage 测试 ───────────────────────────────────────────


class TestStorage:
    """测试 SQLite 存储层"""

    @pytest.fixture
    def storage(self, tmp_path):
        db_path = str(tmp_path / "test.db")
        return Storage(db_path)

    @pytest.fixture
    def sample_event(self):
        source = EventSource(framework="vitest", project="test-proj")
        return create_test_start(
            session_id="sess-test",
            source=source,
            test_name="test_sample",
            full_name="test_sample",
        )

    def test_store_and_retrieve_event(self, storage, sample_event):
        storage.store_event(sample_event)
        events = storage.get_session_events("sess-test")
        assert len(events) == 1
        assert events[0]["event_id"] == sample_event.event_id
        assert events[0]["type"] == "test.start"

    def test_store_batch(self, storage):
        source = EventSource(framework="vitest", project="batch-test")
        events = [
            create_test_start(
                session_id="sess-batch",
                source=source,
                test_name=f"test_{i}",
                full_name=f"test_{i}",
            )
            for i in range(10)
        ]
        count = storage.store_events_batch(events)
        assert count == 10
        retrieved = storage.get_session_events("sess-batch")
        assert len(retrieved) == 10

    def test_filter_by_event_type(self, storage):
        source = EventSource(framework="vitest", project="filter-test")
        session_id = "sess-filter"
        storage.store_event(create_test_start(session_id, source, "t1", "t1"))
        storage.store_event(create_test_end(session_id, source, "t1", True, 100))
        storage.store_event(create_assertion(session_id, source, "a1", True))

        test_events = storage.get_session_events(session_id, event_type="test.start")
        assert len(test_events) == 1
        assert test_events[0]["type"] == "test.start"

    def test_store_session(self, storage):
        session = TestSession(
            session_id="sess-1",
            project="travel-agent",
            framework="vitest",
            started_at=int(time.time() * 1000),
        )
        storage.store_session(session)
        retrieved = storage.get_session("sess-1")
        assert retrieved is not None
        assert retrieved["project"] == "travel-agent"

    def test_update_session(self, storage):
        session = TestSession(
            session_id="sess-update",
            project="test",
            framework="vitest",
            started_at=int(time.time() * 1000),
        )
        storage.store_session(session)

        session.ended_at = int(time.time() * 1000) + 5000
        session.total_tests = 10
        session.passed_tests = 9
        session.failed_tests = 1
        storage.store_session(session)

        retrieved = storage.get_session("sess-update")
        assert retrieved["total_tests"] == 10
        assert retrieved["passed_tests"] == 9

    def test_get_recent_sessions(self, storage):
        for i in range(5):
            storage.store_session(TestSession(
                session_id=f"sess-{i}",
                project="test",
                framework="vitest",
                started_at=int(time.time() * 1000) + i * 1000,
            ))
        sessions = storage.get_recent_sessions(limit=3)
        assert len(sessions) == 3

    def test_project_stats(self, storage):
        source = EventSource(framework="vitest", project="stats-test")
        session_id = "sess-stats"
        storage.store_event(create_test_start(session_id, source, "t1", "t1"))
        storage.store_event(create_test_end(session_id, source, "t1", True, 100))
        storage.store_event(create_test_start(session_id, source, "t2", "t2"))
        storage.store_event(create_test_end(session_id, source, "t2", False, 50))

        stats = storage.get_project_stats("stats-test")
        assert stats["project"] == "stats-test"
        assert stats["events"]["test.end"] == 1
        assert stats["events"]["test.fail"] == 1


# ─── Analyzer 测试 ──────────────────────────────────────────


class TestBugDiscovery:
    """测试 Bug 发现分析器"""

    def _make_events(self, types):
        source = EventSource(framework="vitest", project="test")
        events = []
        for i, t in enumerate(types):
            # 确保 start/end 使用相同的 test_name
            test_idx = i // 2  # 每两个事件一个测试
            events.append(TestEvent(
                event_id=f"evt-{i}",
                session_id="sess",
                timestamp=1000 + i * 100,
                source=source,
                type=t,
                data={"test_name": f"test_{test_idx}"},
            ))
        return [e.to_dict() for e in events]

    def test_detect_failure_streak(self):
        events = self._make_events([
            "test.end", "test.fail", "test.fail", "test.fail", "test.end"
        ])
        analyzer = BugDiscoveryAnalyzer()
        result = analyzer.analyze(events)
        assert any(f["category"] == "failure_streak" for f in result.findings)

    def test_detect_incomplete_tests(self):
        events = self._make_events(["test.start", "test.start"])
        # 只有 start 没有 end
        analyzer = BugDiscoveryAnalyzer()
        result = analyzer.analyze(events)
        assert any(f["category"] == "incomplete_test" for f in result.findings)

    def test_no_issues_on_healthy_run(self):
        events = self._make_events(["test.start", "test.end", "test.start", "test.end"])
        analyzer = BugDiscoveryAnalyzer()
        result = analyzer.analyze(events)
        assert len(result.findings) == 0

    def test_analyzer_name(self):
        assert BugDiscoveryAnalyzer().name == "bug_discovery"


class TestQualityGuard:
    """测试质量守卫"""

    def test_detect_no_assertion(self):
        source = EventSource(framework="vitest", project="test")
        events = [
            create_test_start("sess", source, "test_no_assert", "test_no_assert"),
            create_test_end("sess", source, "test_no_assert", True, 100),
        ]
        events_dict = [e.to_dict() for e in events]

        guard = QualityGuard()
        result = guard.analyze(events_dict)
        assert any(f["category"] == "no_assertion" for f in result.findings)

    def test_quality_score_perfect(self):
        source = EventSource(framework="vitest", project="test")
        events = [
            create_test_start("sess", source, "t1", "t1"),
            create_assertion("sess", source, "a1", True),
            create_test_end("sess", source, "t1", True, 100),
        ]
        events_dict = [e.to_dict() for e in events]

        guard = QualityGuard()
        result = guard.analyze(events_dict)
        # 没有 findings = 100 分
        assert result.confidence > 0


class TestAnomalyDetector:
    """测试异常检测器"""

    def test_detect_low_pass_rate(self):
        source = EventSource(framework="vitest", project="failing-proj")
        events = []
        for i in range(10):
            events.append(create_test_start("sess", source, f"t{i}", f"t{i}").to_dict())
            if i < 8:
                events.append(create_test_end("sess", source, f"t{i}", False, 100).to_dict())
            else:
                events.append(create_test_end("sess", source, f"t{i}", True, 100).to_dict())

        detector = AnomalyDetector()
        result = detector.analyze(events)
        assert any(f["category"] == "low_pass_rate" for f in result.findings)

    def test_no_anomaly_on_good_data(self):
        source = EventSource(framework="vitest", project="good-proj")
        events = []
        for i in range(10):
            events.append(create_test_start("sess", source, f"t{i}", f"t{i}").to_dict())
            events.append(create_test_end("sess", source, f"t{i}", True, 100).to_dict())

        detector = AnomalyDetector()
        result = detector.analyze(events)
        assert len(result.findings) == 0


class TestSemanticEvaluator:
    """测试语义评估器"""

    def test_analyzer_name(self):
        from analyzers.semantic_eval import SemanticEvaluator
        assert SemanticEvaluator().name == "semantic_eval"

    def test_evaluate_tool_failure(self):
        from analyzers.semantic_eval import SemanticEvaluator
        source = EventSource(framework="vitest", project="test")
        events = [
            create_agent_tool_call("sess", source, "search_weather", {"city": "杭州"}).to_dict(),
            TestEvent(
                event_id="evt-result",
                session_id="sess",
                timestamp=1000,
                source=source,
                type="agent.tool_result",
                data={"tool_name": "search_weather", "input": {}, "output": {}, "success": False, "error": "timeout"},
            ).to_dict(),
        ]
        evaluator = SemanticEvaluator()
        result = evaluator.analyze(events)
        assert any(f["category"] == "tool_failure" for f in result.findings)

    def test_evaluate_empty_output(self):
        from analyzers.semantic_eval import SemanticEvaluator
        source = EventSource(framework="vitest", project="test")
        events = [
            TestEvent(
                event_id="evt-result",
                session_id="sess",
                timestamp=1000,
                source=source,
                type="agent.tool_result",
                data={"tool_name": "search_hotels", "input": {}, "output": "", "success": True},
            ).to_dict(),
        ]
        evaluator = SemanticEvaluator()
        result = evaluator.analyze(events)
        assert any(f["category"] == "empty_output" for f in result.findings)

    def test_evaluate_custom_eval_fn(self):
        from analyzers.semantic_eval import SemanticEvaluator
        source = EventSource(framework="vitest", project="test")
        events = [
            TestEvent(
                event_id="evt-result",
                session_id="sess",
                timestamp=1000,
                source=source,
                type="agent.tool_result",
                data={"tool_name": "test_tool", "input": "query", "output": "bad", "success": True},
            ).to_dict(),
        ]
        # 自定义评估函数：输出长度 < 10 则分数低
        evaluator = SemanticEvaluator(eval_fn=lambda inp, out: len(out) / 100)
        result = evaluator.analyze(events)
        assert any(f["category"] == "low_quality" for f in result.findings)

    def test_assert_trip_plan_structure(self):
        from analyzers.semantic_eval import assert_trip_plan_structure
        output = "目的地：杭州，第一天游览西湖，第二天去灵隐寺，住宿推荐西湖边酒店，预算约2000元"
        results = assert_trip_plan_structure(output)
        passed = [r for r in results if r["passed"]]
        assert len(passed) >= 4  # 至少通过 4 项检查

    def test_evaluate_good_output(self):
        from analyzers.semantic_eval import SemanticEvaluator
        source = EventSource(framework="vitest", project="test")
        events = [
            TestEvent(
                event_id="evt-result",
                session_id="sess",
                timestamp=1000,
                source=source,
                type="agent.tool_result",
                data={"tool_name": "search_weather", "input": {"city": "杭州"}, "output": {"temp": 28, "weather": "晴"}, "success": True},
            ).to_dict(),
        ]
        evaluator = SemanticEvaluator()
        result = evaluator.analyze(events)
        assert len(result.findings) == 0  # 好的输出不应有 findings


class TestAPIIntegration:
    """API 集成测试"""

    @pytest.fixture
    def client(self, tmp_path):
        from fastapi.testclient import TestClient
        from gateway.main import app, storage
        # 使用临时数据库
        storage.db_path = str(tmp_path / "test_api.db")
        storage._init_db()
        return TestClient(app)

    def test_health(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    def test_ingest_event(self, client):
        resp = client.post("/events", json={
            "session_id": "api-sess-1",
            "source": {"framework": "vitest", "project": "test"},
            "type": "test.start",
            "data": {"test_name": "api_test"},
        })
        assert resp.status_code == 200
        assert resp.json()["status"] == "accepted"

    def test_ingest_batch(self, client):
        events = [
            {"session_id": "batch-sess", "source": {"framework": "vitest", "project": "test"}, "type": "test.start", "data": {"test_name": f"t{i}"}}
            for i in range(5)
        ]
        resp = client.post("/events/batch", json={"events": events})
        assert resp.status_code == 200
        assert resp.json()["count"] == 5

    def test_create_session(self, client):
        resp = client.post("/sessions", json={
            "project": "travel-agent",
            "framework": "vitest",
        })
        assert resp.status_code == 200
        sid = resp.json()["session_id"]
        assert sid

    def test_get_session_timeline(self, client):
        # 先创建会话和事件
        client.post("/sessions", json={"session_id": "tl-sess", "project": "test", "framework": "vitest"})
        client.post("/events", json={
            "session_id": "tl-sess",
            "source": {"framework": "vitest", "project": "test"},
            "type": "test.start",
            "data": {"test_name": "tl_test"},
        })
        resp = client.get("/sessions/tl-sess/timeline")
        assert resp.status_code == 200
        assert resp.json()["count"] == 1

    def test_gate_result(self, client):
        client.post("/sessions", json={
            "session_id": "gate-sess",
            "project": "test",
            "framework": "custom",
        })
        client.post("/events", json={
            "session_id": "gate-sess",
            "source": {"framework": "custom", "project": "test"},
            "type": "report.gate_result",
            "data": {"verdict": "PASS", "rules": {}},
        })
        resp = client.get("/sessions/gate-sess/gate")
        assert resp.status_code == 200
        assert resp.json()["verdict"] == "PASS"

    def test_project_summary(self, client):
        resp = client.get("/projects/test/summary")
        assert resp.status_code == 200
        assert "project" in resp.json()

    def test_list_sessions(self, client):
        client.post("/sessions", json={"project": "list-test", "framework": "vitest"})
        resp = client.get("/sessions?project=list-test")
        assert resp.status_code == 200
        assert resp.json()["count"] >= 1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
