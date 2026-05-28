"""EventBridge ↔ Gateway 端到端事件流测试

使用 FastAPI TestClient (httpx.ASGITransport) 测试完整生命周期，
不启动真实 HTTP 服务器，Storage 使用 tmp_path 隔离。
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.slow

from drivers.godot.event_bridge import EventBridge

# ─── Fixtures ──────────────────────────────────────────────


@pytest.fixture
def app(tmp_path):
    """创建隔离的 FastAPI app（使用 tmp_path SQLite）"""
    from gateway.main import app as fastapi_app
    from gateway.storage import Storage

    storage = Storage(str(tmp_path / "e2e.db"))
    fastapi_app.state.storage = storage
    return fastapi_app


@pytest.fixture
def client(app):
    """同步 TestClient"""
    return TestClient(app)


@pytest.fixture
def transport(app):
    """httpx ASGI Transport（让 EventBridge 的 AsyncClient 直连 app）"""
    return httpx.ASGITransport(app=app)


@pytest.fixture
def mock_driver():
    """Mock GodotDriver"""
    driver = MagicMock()
    driver.screenshot = AsyncMock(return_value=Path("/tmp/e2e_screenshot.png"))
    return driver


@pytest.fixture
def bridge_factory(transport, mock_driver):
    """创建连接到 TestClient app 的 EventBridge 工厂"""

    def _create(project: str = "e2e-project", **kwargs):
        b = EventBridge(
            driver=mock_driver,
            gateway_url="http://testserver",
            project=project,
            flush_interval=0,  # 禁用自动刷新
            **kwargs,
        )
        # 替换 HTTP 客户端，使用 ASGI transport 直连 app
        b._client = httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
        )
        return b

    return _create


# ─── 完整生命周期端到端测试 ────────────────────────────────


class TestFullLifecycleE2E:
    """EventBridge → Gateway 完整生命周期"""

    @pytest.mark.asyncio
    async def test_complete_session_lifecycle(self, bridge_factory, client):
        """完整生命周期: start → report_test_start → report_test_end → end_session"""
        bridge = bridge_factory("lifecycle-game")

        async with bridge as b:
            # 1. 启动 session
            session_id = await b.start_session("lifecycle-game", "godot_driver")
            assert session_id.startswith("driver-lifecycle-game-")

            # 2. 测试开始
            await b.report_test_start("move_test", "suite.move_test")

            # 3. 测试结束（通过）
            await b.report_test_end("move_test", True, 150)

            # 4. 另一个测试（失败）
            await b.report_test_start("attack_test")
            await b.report_test_end("attack_test", False, 80, errors=[{"message": "timeout"}])

            # 5. 结束 session
            await b.end_session()

        # 验证 session 存在于 /sessions 端点
        resp = client.get(f"/sessions/{session_id}")
        assert resp.status_code == 200
        session = resp.json()
        assert session["session_id"] == session_id
        assert session["project"] == "lifecycle-game"
        assert session["total_tests"] == 2
        assert session["passed_tests"] == 1
        assert session["failed_tests"] == 1

        # 验证事件已通过存储写入
        resp = client.get(f"/sessions/{session_id}/timeline")
        assert resp.status_code == 200
        timeline = resp.json()
        assert timeline["count"] >= 4  # 2 * test.start + 2 * test.end

        # 验证时间线包含正确的事件类型
        event_types = {e["type"] for e in timeline["events"]}
        assert "test.start" in event_types
        assert "test.end" in event_types
        assert "test.fail" in event_types

    @pytest.mark.asyncio
    async def test_screenshot_event_e2e(self, bridge_factory, client, mock_driver):
        """截图事件端到端: screenshot → action.screenshot 事件"""
        bridge = bridge_factory("screenshot-game")

        async with bridge as b:
            await b.start_session("screenshot-game")
            await b.screenshot_and_report(context="battle_start")
            await b.end_session()

        # 验证 session
        # session 已被 end_session 置为 None，从 sent_count 或 API 查询
        sessions_resp = client.get("/sessions?project=screenshot-game")
        assert sessions_resp.status_code == 200
        sessions = sessions_resp.json()["sessions"]
        assert len(sessions) >= 1

        # 获取 session_id 并验证事件
        sid = sessions[0]["session_id"]
        resp = client.get(f"/sessions/{sid}/timeline")
        assert resp.status_code == 200
        event_types = {e["type"] for e in resp.json()["events"]}
        assert "action.screenshot" in event_types

    @pytest.mark.asyncio
    async def test_bench_result_e2e(self, bridge_factory, client):
        """评估结果端到端: report_bench_result → bench.* 事件"""
        bridge = bridge_factory("bench-game")

        async with bridge as b:
            await b.start_session("bench-game")
            await b.report_bench_result(
                dimension="build_health",
                score=85.0,
                passed=True,
                checks=[{"name": "compiles", "passed": True}],
            )
            await b.report_bench_result(
                dimension="visual_usability",
                score=60.0,
                passed=False,
                checks=[{"name": "ui_render", "passed": False}],
            )
            await b.end_session()

        # 获取 session
        sessions_resp = client.get("/sessions?project=bench-game")
        sid = sessions_resp.json()["sessions"][0]["session_id"]

        # 验证事件
        resp = client.get(f"/sessions/{sid}/timeline")
        events = resp.json()["events"]
        bench_events = [e for e in events if e["type"].startswith("bench.")]
        assert len(bench_events) == 2

        build_health = next(e for e in bench_events if e["data"]["dimension"] == "build_health")
        assert build_health["data"]["score"] == 85.0
        assert build_health["data"]["passed"] is True

    @pytest.mark.asyncio
    async def test_debug_events_e2e(self, bridge_factory, client):
        """调试事件端到端: debug.match + debug.repair"""
        bridge = bridge_factory("debug-game")

        async with bridge as b:
            await b.start_session("debug-game")
            await b.report_debug_match(
                entry_id="entry-ERR-001",
                error_code="NODE_NOT_FOUND",
                error_message="Node not found: UI/Panel",
            )
            await b.report_debug_repair(
                entry_id="entry-ERR-001",
                fix_description="Fixed node path to UI/Control/Panel",
                error_code="NODE_NOT_FOUND",
            )
            await b.end_session()

        sessions_resp = client.get("/sessions?project=debug-game")
        sid = sessions_resp.json()["sessions"][0]["session_id"]

        resp = client.get(f"/sessions/{sid}/timeline")
        events = resp.json()["events"]
        debug_events = [e for e in events if e["type"].startswith("debug.")]
        assert len(debug_events) == 2

        match_event = next(e for e in debug_events if e["type"] == "debug.match")
        assert match_event["data"]["error_code"] == "NODE_NOT_FOUND"

        repair_event = next(e for e in debug_events if e["type"] == "debug.repair")
        assert "Fixed node path" in repair_event["data"]["fix_description"]

    @pytest.mark.asyncio
    async def test_game_event_e2e(self, bridge_factory, client):
        """游戏事件端到端: game.state_change"""
        bridge = bridge_factory("state-game")

        async with bridge as b:
            await b.start_session("state-game")
            await b.report_game_event(
                "game.state_change",
                {"scene_path": "res://main.tscn", "state": {"hp": 100, "gold": 50}},
            )
            await b.report_game_event(
                "game.state_change",
                {"scene_path": "res://battle.tscn", "state": {"hp": 80, "gold": 75}},
            )
            await b.end_session()

        sessions_resp = client.get("/sessions?project=state-game")
        sid = sessions_resp.json()["sessions"][0]["session_id"]

        resp = client.get(f"/sessions/{sid}/timeline")
        events = resp.json()["events"]
        game_events = [e for e in events if e["type"] == "game.state_change"]
        assert len(game_events) == 2
        assert game_events[0]["data"]["scene_path"] == "res://main.tscn"


# ─── API 端点验证测试 ──────────────────────────────────────


class TestAPIEndpoints:
    """验证各 API 端点返回正确数据"""

    @pytest.mark.asyncio
    async def test_sessions_list_endpoint(self, bridge_factory, client):
        """GET /sessions 返回创建的 session"""
        bridge = bridge_factory("list-game")

        async with bridge as b:
            await b.start_session("list-game")
            await b.report_test_start("t1")
            await b.report_test_end("t1", True, 100)
            await b.end_session()

        resp = client.get("/sessions")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] >= 1
        projects = [s["project"] for s in data["sessions"]]
        assert "list-game" in projects

    @pytest.mark.asyncio
    async def test_sessions_list_filter_by_project(self, bridge_factory, client):
        """GET /sessions?project=X 按项目过滤"""
        bridge_a = bridge_factory("filter-a")
        bridge_b = bridge_factory("filter-b")

        async with bridge_a as b:
            await b.start_session("filter-a")
            await b.end_session()
        async with bridge_b as b:
            await b.start_session("filter-b")
            await b.end_session()

        resp = client.get("/sessions?project=filter-a")
        sessions = resp.json()["sessions"]
        assert all(s["project"] == "filter-a" for s in sessions)

    @pytest.mark.asyncio
    async def test_timeline_endpoint_with_event_type_filter(self, bridge_factory, client):
        """GET /sessions/{id}/timeline?event_type=X 按类型过滤"""
        bridge = bridge_factory("filter-timeline")

        async with bridge as b:
            await b.start_session("filter-timeline")
            await b.report_test_start("t1")
            await b.report_test_end("t1", True, 100)
            await b.end_session()

        sessions_resp = client.get("/sessions?project=filter-timeline")
        sid = sessions_resp.json()["sessions"][0]["session_id"]

        # 过滤 test.start
        resp = client.get(f"/sessions/{sid}/timeline?event_type=test.start")
        assert resp.status_code == 200
        events = resp.json()["events"]
        assert all(e["type"] == "test.start" for e in events)

    @pytest.mark.asyncio
    async def test_analysis_endpoint_empty(self, bridge_factory, client):
        """GET /sessions/{id}/analysis 无分析时返回空列表"""
        bridge = bridge_factory("analysis-game")

        async with bridge as b:
            await b.start_session("analysis-game")
            await b.end_session()

        sessions_resp = client.get("/sessions?project=analysis-game")
        sid = sessions_resp.json()["sessions"][0]["session_id"]

        resp = client.get(f"/sessions/{sid}/analysis")
        assert resp.status_code == 200
        data = resp.json()
        assert data["session_id"] == sid
        assert data["analyses"] == []
        assert data["count"] == 0

    @pytest.mark.asyncio
    async def test_project_summary_endpoint(self, bridge_factory, client):
        """GET /projects/{name}/summary 返回项目统计"""
        bridge = bridge_factory("summary-game")

        async with bridge as b:
            await b.start_session("summary-game")
            await b.report_test_start("t1")
            await b.report_test_end("t1", True, 100)
            await b.report_test_start("t2")
            await b.report_test_end("t2", False, 50, errors=[{"message": "fail"}])
            await b.end_session()

        resp = client.get("/projects/summary-game/summary")
        assert resp.status_code == 200
        data = resp.json()
        assert data["project"] == "summary-game"
        assert "sessions" in data
        assert "events" in data
        assert "pass_rate" in data
        # 1 pass / 2 total = 0.5
        assert data["pass_rate"] == 0.5

    @pytest.mark.asyncio
    async def test_session_event_stats(self, bridge_factory, client):
        """GET /sessions/{id} 包含 event_stats 统计"""
        bridge = bridge_factory("stats-game")

        async with bridge as b:
            await b.start_session("stats-game")
            await b.report_test_start("t1")
            await b.report_test_end("t1", True, 100)
            await b.report_bench_result("build_health", 90, True)
            await b.end_session()

        sessions_resp = client.get("/sessions?project=stats-game")
        sid = sessions_resp.json()["sessions"][0]["session_id"]

        resp = client.get(f"/sessions/{sid}")
        data = resp.json()
        assert "event_stats" in data
        assert "total_events" in data
        assert data["total_events"] >= 3
        assert data["event_stats"].get("test.start", 0) >= 1
        assert data["event_stats"].get("bench.build_health", 0) >= 1


# ─── WebSocket 实时推送测试 ────────────────────────────────


class TestWebSocketRealTimePush:
    """WebSocket 实时事件推送端到端"""

    def test_ws_connect_and_receive_via_event_bridge(self, client):
        """EventBridge 发送事件 → WebSocket 实时收到"""
        session_id = "ws-bridge-1"

        with client.websocket_connect(f"/ws/events/{session_id}") as ws:
            # 收到连接确认
            connected = ws.receive_json()
            assert connected["type"] == "connected"
            assert connected["session_id"] == session_id

            # 通过 HTTP 直接 POST 事件（模拟 EventBridge 行为）
            resp = client.post(
                "/events",
                json={
                    "session_id": session_id,
                    "source": {"framework": "godot_driver", "project": "ws-game"},
                    "type": "test.start",
                    "data": {"test_name": "ws_test", "full_name": "ws_test"},
                },
            )
            assert resp.status_code == 200

            # WebSocket 应收到事件
            event = ws.receive_json()
            assert event["type"] == "test.start"
            assert event["session_id"] == session_id
            assert event["data"]["test_name"] == "ws_test"

    def test_ws_full_lifecycle_events(self, client):
        """完整生命周期中 WebSocket 收到所有事件"""
        session_id = "ws-lifecycle"

        with client.websocket_connect(f"/ws/events/{session_id}") as ws:
            ws.receive_json()  # connected

            events_to_send = [
                ("test.start", {"test_name": "move_test"}),
                ("test.end", {"test_name": "move_test", "passed": True, "duration_ms": 150}),
                ("action.screenshot", {"context": "battle", "filepath": "/tmp/battle.png"}),
                ("bench.build_health", {"dimension": "build_health", "score": 85.0, "passed": True}),
            ]

            for event_type, data in events_to_send:
                resp = client.post(
                    "/events",
                    json={
                        "session_id": session_id,
                        "source": {"framework": "godot_driver", "project": "ws-lifecycle-game"},
                        "type": event_type,
                        "data": data,
                    },
                )
                assert resp.status_code == 200

            # 验证收到所有事件
            received_types = []
            for _ in events_to_send:
                event = ws.receive_json()
                received_types.append(event["type"])

            assert received_types == [e[0] for e in events_to_send]

    def test_ws_event_type_filtering(self, client):
        """WebSocket 按事件类型过滤"""
        session_id = "ws-filter"

        with client.websocket_connect(f"/ws/events/{session_id}?event_types=test.fail") as ws:
            ws.receive_json()  # connected

            # test.start（应被过滤）
            client.post(
                "/events",
                json={
                    "session_id": session_id,
                    "source": {"framework": "godot_driver", "project": "filter-game"},
                    "type": "test.start",
                    "data": {},
                },
            )

            # test.fail（应收到）
            client.post(
                "/events",
                json={
                    "session_id": session_id,
                    "source": {"framework": "godot_driver", "project": "filter-game"},
                    "type": "test.fail",
                    "data": {"test_name": "fail_test"},
                },
            )

            event = ws.receive_json()
            assert event["type"] == "test.fail"

    def test_ws_multiple_clients_same_session(self, client):
        """多个 WebSocket 客户端连接同一 session"""
        session_id = "ws-multi"

        with (
            client.websocket_connect(f"/ws/events/{session_id}") as ws1,
            client.websocket_connect(f"/ws/events/{session_id}") as ws2,
        ):
            ws1.receive_json()  # connected
            ws2.receive_json()  # connected

            client.post(
                "/events",
                json={
                    "session_id": session_id,
                    "source": {"framework": "godot_driver", "project": "multi-game"},
                    "type": "test.end",
                    "data": {"test_name": "shared", "passed": True},
                },
            )

            event1 = ws1.receive_json()
            event2 = ws2.receive_json()
            assert event1["type"] == "test.end"
            assert event2["type"] == "test.end"
            assert event1["data"]["test_name"] == "shared"

    def test_ws_session_isolation(self, client):
        """不同 session 的事件互不干扰"""
        with (
            client.websocket_connect("/ws/events/ws-iso-a") as ws_a,
            client.websocket_connect("/ws/events/ws-iso-b") as ws_b,
        ):
            ws_a.receive_json()  # connected
            ws_b.receive_json()  # connected

            # 发送到 session A
            client.post(
                "/events",
                json={
                    "session_id": "ws-iso-a",
                    "source": {"framework": "godot_driver", "project": "iso-game"},
                    "type": "test.start",
                    "data": {"test_name": "only_a"},
                },
            )

            event_a = ws_a.receive_json()
            assert event_a["session_id"] == "ws-iso-a"
            assert event_a["data"]["test_name"] == "only_a"

            # ws-b 不应收到该事件（没有新事件推送给它）


# ─── 批量事件测试 ──────────────────────────────────────────


class TestBatchEvents:
    """批量事件端到端测试"""

    @pytest.mark.asyncio
    async def test_batch_20_plus_events(self, bridge_factory, client):
        """发送 20+ 事件，验证批量写入正确"""
        bridge = bridge_factory("batch-game", batch_size=100)

        async with bridge as b:
            await b.start_session("batch-game")

            # 发送 25 组事件（每组 test.start + test.end = 50 事件）
            for i in range(25):
                await b.report_test_start(f"test_{i}", f"suite.test_{i}")
                await b.report_test_end(f"test_{i}", i % 5 != 0, 100 + i * 10)

            await b.end_session()

        sessions_resp = client.get("/sessions?project=batch-game")
        sid = sessions_resp.json()["sessions"][0]["session_id"]

        # 验证事件数量
        resp = client.get(f"/sessions/{sid}/timeline")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 50  # 25 * (test.start + test.end)

        # 验证事件类型分布
        event_types = {}
        for e in data["events"]:
            event_types[e["type"]] = event_types.get(e["type"], 0) + 1
        assert event_types.get("test.start", 0) == 25
        assert event_types.get("test.end", 0) >= 20  # 大部分通过
        assert event_types.get("test.fail", 0) >= 5  # 每 5 个失败 1 个

        # 验证 session 统计
        session_resp = client.get(f"/sessions/{sid}")
        session = session_resp.json()
        assert session["total_tests"] == 25
        assert session["passed_tests"] == 20
        assert session["failed_tests"] == 5

    @pytest.mark.asyncio
    async def test_batch_events_with_mixed_types(self, bridge_factory, client):
        """混合类型批量事件"""
        bridge = bridge_factory("mixed-game", batch_size=100)

        async with bridge as b:
            await b.start_session("mixed-game")

            # 混合各种事件类型
            await b.report_test_start("test_mixed")
            await b.report_bench_result("build_health", 90, True)
            await b.report_bench_result("visual_usability", 75, True)
            await b.report_debug_match("entry-1", "ERR_001", "Something broke")
            await b.report_debug_repair("entry-1", "Fixed it")
            await b.report_game_event("game.state_change", {"scene": "main", "hp": 100})
            await b.report_test_end("test_mixed", True, 500)

            await b.end_session()

        sessions_resp = client.get("/sessions?project=mixed-game")
        sid = sessions_resp.json()["sessions"][0]["session_id"]

        resp = client.get(f"/sessions/{sid}/timeline")
        events = resp.json()["events"]

        event_types = {e["type"] for e in events}
        expected_types = {
            "test.start",
            "test.end",
            "bench.build_health",
            "bench.visual_usability",
            "debug.match",
            "debug.repair",
            "game.state_change",
        }
        assert expected_types.issubset(event_types)

    @pytest.mark.asyncio
    async def test_batch_auto_flush_on_threshold(self, bridge_factory, client):
        """batch_size 阈值触发自动发送"""
        bridge = bridge_factory("autoflush-game", batch_size=5)

        async with bridge as b:
            await b.start_session("autoflush-game")

            # 发送 5 个事件（触发自动 flush）
            for i in range(5):
                await b.report_test_start(f"t_{i}")

            # 等待 create_task 执行
            await asyncio.sleep(0.05)

            # 缓冲区应已清空
            assert b.buffer_size == 0

            await b.end_session()

        sessions_resp = client.get("/sessions?project=autoflush-game")
        sid = sessions_resp.json()["sessions"][0]["session_id"]

        resp = client.get(f"/sessions/{sid}/timeline")
        assert resp.json()["count"] == 5

    @pytest.mark.asyncio
    async def test_batch_end_session_flushes_remaining(self, bridge_factory, client):
        """end_session 刷新剩余缓冲事件"""
        bridge = bridge_factory("flush-game", batch_size=100)

        async with bridge as b:
            await b.start_session("flush-game")
            # 发送少量事件（不触发自动 flush）
            await b.report_test_start("t1")
            await b.report_test_end("t1", True, 50)
            assert b.buffer_size == 2  # 还在缓冲区

            # end_session 应 flush
            await b.end_session()

        sessions_resp = client.get("/sessions?project=flush-game")
        sid = sessions_resp.json()["sessions"][0]["session_id"]

        resp = client.get(f"/sessions/{sid}/timeline")
        assert resp.json()["count"] == 2


# ─── Trace 传播测试 ────────────────────────────────────────


class TestTracePropagation:
    """trace_id 跨事件传播验证"""

    @pytest.mark.asyncio
    async def test_trace_id_propagation_e2e(self, bridge_factory, client):
        """trace_id 在端到端中正确传递和查询"""
        bridge = bridge_factory("trace-game")

        trace_id = "trace-e2e-abc-123"
        async with bridge as b:
            await b.start_session("trace-game")
            await b.report_test_start("t1", trace_id=trace_id)
            await b.report_test_end("t1", True, 100, trace_id=trace_id)
            await b.report_bench_result("build_health", 90, True, trace_id=trace_id)
            await b.end_session()

        sessions_resp = client.get("/sessions?project=trace-game")
        sid = sessions_resp.json()["sessions"][0]["session_id"]

        resp = client.get(f"/sessions/{sid}/timeline")
        events = resp.json()["events"]

        # 所有事件应有相同的 trace_id
        for e in events:
            assert e["trace_id"] == trace_id

        # 通过 trace 端点查询
        trace_resp = client.get(f"/trace/{trace_id}")
        assert trace_resp.status_code == 200
        trace_data = trace_resp.json()
        assert trace_data["count"] >= 3
        assert all(e["trace_id"] == trace_id for e in trace_data["events"])


# ─── 错误场景测试 ──────────────────────────────────────────


class TestErrorScenarios:
    """边界和错误场景"""

    def test_get_nonexistent_session(self, client):
        """获取不存在的 session 返回 404"""
        resp = client.get("/sessions/nonexistent-session")
        assert resp.status_code == 404

    def test_get_timeline_nonexistent_session(self, client):
        """获取不存在 session 的时间线返回 404"""
        resp = client.get("/sessions/nonexistent/timeline")
        assert resp.status_code == 404

    def test_get_analysis_nonexistent_session(self, client):
        """获取不存在 session 的分析返回空列表（不报错）"""
        resp = client.get("/sessions/nonexistent/analysis")
        assert resp.status_code == 200
        assert resp.json()["count"] == 0

    def test_get_project_summary_no_data(self, client):
        """获取无数据项目的统计"""
        resp = client.get("/projects/empty-project/summary")
        assert resp.status_code == 200
        data = resp.json()
        assert data["project"] == "empty-project"
        assert data["pass_rate"] == 1.0  # 无测试默认通过

    @pytest.mark.asyncio
    async def test_session_not_ended_still_queryable(self, bridge_factory, client):
        """未结束的 session 仍然可查询"""
        bridge = bridge_factory("noend-game")

        async with bridge as b:
            sid = await b.start_session("noend-game")
            await b.report_test_start("t1")
            # 不调用 end_session

        resp = client.get(f"/sessions/{sid}")
        assert resp.status_code == 200
        session = resp.json()
        assert session["session_id"] == sid
        assert "ended_at" not in session  # 未结束


# ─── 健康检查 ──────────────────────────────────────────────


class TestHealthCheck:
    """基础健康检查"""

    def test_health_endpoint(self, client):
        """GET /health 返回正常"""
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert "version" in data


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
