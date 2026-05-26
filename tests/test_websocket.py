"""WebSocket 实时事件流测试"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


# ─── ConnectionManager 单元测试 ─────────────────────────────


class TestConnectionManager:
    """测试 ConnectionManager 连接管理"""

    @pytest.fixture
    def manager(self):
        from gateway.main import ConnectionManager

        return ConnectionManager()

    @pytest.mark.asyncio
    async def test_connect_accepts_websocket(self, manager):
        from unittest.mock import AsyncMock

        ws = AsyncMock()
        await manager.connect(ws, "sess-1")
        assert manager.get_connection_count("sess-1") == 1
        ws.accept.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_connect_with_event_type_filter(self, manager):
        from unittest.mock import AsyncMock

        ws = AsyncMock()
        await manager.connect(ws, "sess-2", ["test.end", "test.fail"])
        assert manager.get_connection_count("sess-2") == 1

    @pytest.mark.asyncio
    async def test_disconnect_removes_connection(self, manager):
        from unittest.mock import AsyncMock

        ws = AsyncMock()
        await manager.connect(ws, "sess-3")
        manager.disconnect(ws, "sess-3")
        assert manager.get_connection_count("sess-3") == 0

    @pytest.mark.asyncio
    async def test_disconnect_cleans_empty_session(self, manager):
        from unittest.mock import AsyncMock

        ws = AsyncMock()
        await manager.connect(ws, "sess-4")
        manager.disconnect(ws, "sess-4")
        assert "sess-4" not in manager.active_connections

    @pytest.mark.asyncio
    async def test_broadcast_sends_to_all(self, manager):
        from unittest.mock import AsyncMock

        ws1 = AsyncMock()
        ws2 = AsyncMock()
        await manager.connect(ws1, "sess-5")
        await manager.connect(ws2, "sess-5")

        event = {"type": "test.start", "session_id": "sess-5", "data": {}}
        await manager.broadcast("sess-5", event)

        ws1.send_json.assert_awaited_once_with(event)
        ws2.send_json.assert_awaited_once_with(event)

    @pytest.mark.asyncio
    async def test_broadcast_filters_by_event_type(self, manager):
        from unittest.mock import AsyncMock

        ws_all = AsyncMock()
        ws_filtered = AsyncMock()
        await manager.connect(ws_all, "sess-6")  # 接收全部
        await manager.connect(ws_filtered, "sess-6", ["test.fail"])  # 只接收 test.fail

        # test.start 应只发给 ws_all
        await manager.broadcast("sess-6", {"type": "test.start"})
        ws_all.send_json.assert_awaited_once()
        ws_filtered.send_json.assert_not_awaited()

        # test.fail 应发给两者
        ws_all.reset_mock()
        ws_filtered.reset_mock()
        await manager.broadcast("sess-6", {"type": "test.fail"})
        ws_all.send_json.assert_awaited_once()
        ws_filtered.send_json.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_broadcast_cleans_stale_connections(self, manager):
        from unittest.mock import AsyncMock

        ws_good = AsyncMock()
        ws_bad = AsyncMock()
        ws_bad.send_json.side_effect = Exception("connection closed")

        await manager.connect(ws_good, "sess-7")
        await manager.connect(ws_bad, "sess-7")

        await manager.broadcast("sess-7", {"type": "test.end"})
        # 坏连接应被清理
        assert manager.get_connection_count("sess-7") == 1

    @pytest.mark.asyncio
    async def test_broadcast_noop_for_unknown_session(self, manager):
        # 不应抛出异常
        await manager.broadcast("nonexistent", {"type": "test.end"})

    def test_global_connection_count(self, manager):
        from unittest.mock import AsyncMock

        async def _test():
            ws1 = AsyncMock()
            ws2 = AsyncMock()
            await manager.connect(ws1, "a")
            await manager.connect(ws2, "b")
            assert manager.get_connection_count() == 2

        import asyncio

        asyncio.get_event_loop().run_until_complete(_test())


# ─── WebSocket 集成测试 ─────────────────────────────────────


class TestWebSocketIntegration:
    """WebSocket 端点集成测试"""

    @pytest.fixture
    def client(self, tmp_path):
        from fastapi.testclient import TestClient

        from gateway.main import app, storage

        storage.db_path = str(tmp_path / "test_ws.db")
        storage._init_db()
        return TestClient(app)

    def test_websocket_connect_and_receive_event(self, client):
        """连接 WebSocket 后通过 HTTP POST 事件，验证 WebSocket 收到推送"""
        session_id = "ws-test-1"

        with client.websocket_connect(f"/ws/events/{session_id}") as ws:
            # 收到连接确认
            connected = ws.receive_json()
            assert connected["type"] == "connected"
            assert connected["session_id"] == session_id

            # 通过 HTTP POST 事件
            resp = client.post(
                "/events",
                json={
                    "session_id": session_id,
                    "source": {"framework": "vitest", "project": "test"},
                    "type": "test.start",
                    "data": {"test_name": "ws_test"},
                },
            )
            assert resp.status_code == 200

            # WebSocket 应收到该事件
            event = ws.receive_json()
            assert event["type"] == "test.start"
            assert event["session_id"] == session_id
            assert event["data"]["test_name"] == "ws_test"

    def test_websocket_event_type_filtering(self, client):
        """测试按事件类型过滤"""
        session_id = "ws-filter-1"

        with client.websocket_connect(f"/ws/events/{session_id}?event_types=test.fail,test.end") as ws:
            # 收到连接确认
            connected = ws.receive_json()
            assert connected["type"] == "connected"

            # POST 一个 test.start（应被过滤）
            client.post(
                "/events",
                json={
                    "session_id": session_id,
                    "source": {"framework": "vitest", "project": "test"},
                    "type": "test.start",
                    "data": {"test_name": "t1"},
                },
            )

            # POST 一个 test.end（应收到）
            client.post(
                "/events",
                json={
                    "session_id": session_id,
                    "source": {"framework": "vitest", "project": "test"},
                    "type": "test.end",
                    "data": {"test_name": "t1", "passed": True},
                },
            )

            # 应只收到 test.end，不应收到 test.start
            event = ws.receive_json()
            assert event["type"] == "test.end"

    def test_websocket_dynamic_subscribe(self, client):
        """测试动态更新过滤条件"""
        session_id = "ws-sub-1"

        with client.websocket_connect(f"/ws/events/{session_id}") as ws:
            ws.receive_json()  # connected

            # 动态订阅 test.fail
            ws.send_json({"action": "subscribe", "event_types": ["test.fail"]})
            sub_resp = ws.receive_json()
            assert sub_resp["type"] == "subscribed"
            assert sub_resp["event_types_filter"] == ["test.fail"]

            # POST test.start（被过滤）
            client.post(
                "/events",
                json={
                    "session_id": session_id,
                    "source": {"framework": "vitest", "project": "test"},
                    "type": "test.start",
                    "data": {},
                },
            )

            # POST test.fail（应收到）
            client.post(
                "/events",
                json={
                    "session_id": session_id,
                    "source": {"framework": "vitest", "project": "test"},
                    "type": "test.fail",
                    "data": {"test_name": "fail_test"},
                },
            )

            event = ws.receive_json()
            assert event["type"] == "test.fail"

    def test_websocket_unsubscribe(self, client):
        """测试取消过滤"""
        session_id = "ws-unsub-1"

        with client.websocket_connect(f"/ws/events/{session_id}?event_types=test.fail") as ws:
            ws.receive_json()  # connected

            # 取消过滤
            ws.send_json({"action": "unsubscribe"})
            unsub_resp = ws.receive_json()
            assert unsub_resp["type"] == "unsubscribed"

            # 现在应能收到 test.start
            client.post(
                "/events",
                json={
                    "session_id": session_id,
                    "source": {"framework": "vitest", "project": "test"},
                    "type": "test.start",
                    "data": {},
                },
            )

            event = ws.receive_json()
            assert event["type"] == "test.start"

    def test_websocket_ping_pong(self, client):
        """测试心跳 ping/pong"""
        session_id = "ws-ping-1"

        with client.websocket_connect(f"/ws/events/{session_id}") as ws:
            ws.receive_json()  # connected

            ws.send_json({"action": "ping"})
            pong = ws.receive_json()
            assert pong["type"] == "pong"

    def test_websocket_multiple_clients(self, client):
        """测试多个客户端连接同一 session"""
        session_id = "ws-multi-1"

        with (
            client.websocket_connect(f"/ws/events/{session_id}") as ws1,
            client.websocket_connect(f"/ws/events/{session_id}") as ws2,
        ):
            ws1.receive_json()  # connected
            ws2.receive_json()  # connected

            # POST 事件
            client.post(
                "/events",
                json={
                    "session_id": session_id,
                    "source": {"framework": "vitest", "project": "test"},
                    "type": "test.end",
                    "data": {"test_name": "shared_test", "passed": True},
                },
            )

            # 两个客户端都应收到
            event1 = ws1.receive_json()
            event2 = ws2.receive_json()
            assert event1["type"] == "test.end"
            assert event2["type"] == "test.end"
            assert event1["data"]["test_name"] == "shared_test"

    def test_websocket_different_sessions_isolated(self, client):
        """测试不同 session 之间的事件隔离"""
        with (
            client.websocket_connect("/ws/events/session-A") as ws_a,
            client.websocket_connect("/ws/events/session-B") as ws_b,
        ):
            ws_a.receive_json()  # connected
            ws_b.receive_json()  # connected

            # POST 到 session-A
            client.post(
                "/events",
                json={
                    "session_id": "session-A",
                    "source": {"framework": "vitest", "project": "test"},
                    "type": "test.start",
                    "data": {"test_name": "only_a"},
                },
            )

            # 只有 ws_a 应收到
            event_a = ws_a.receive_json()
            assert event_a["session_id"] == "session-A"

            # ws_b 不应收到（无事件）

    def test_websocket_batch_broadcast(self, client):
        """测试批量事件广播"""
        session_id = "ws-batch-1"

        with client.websocket_connect(f"/ws/events/{session_id}") as ws:
            ws.receive_json()  # connected

            # 批量 POST
            client.post(
                "/events/batch",
                json={
                    "events": [
                        {
                            "session_id": session_id,
                            "source": {"framework": "vitest", "project": "test"},
                            "type": "test.start",
                            "data": {"test_name": f"batch_{i}"},
                        }
                        for i in range(3)
                    ]
                },
            )

            # 批量端点目前不广播，但应验证不崩溃
            # （如需支持批量广播可后续扩展）


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
