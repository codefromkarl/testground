"""EventBridge 测试 — mock httpx 测试事件生成、批量发送、容错"""

import asyncio
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from drivers.godot.event_bridge import EventBridge

pytestmark = pytest.mark.medium

# ─── Fixtures ──────────────────────────────────────────


@pytest.fixture
def mock_driver():
    """Mock GodotDriver"""
    driver = MagicMock()
    driver.screenshot = AsyncMock(return_value=Path("/tmp/test_screenshot.png"))
    return driver


@pytest.fixture
def mock_httpx_response():
    """创建成功的 httpx Response mock"""
    resp = MagicMock()
    resp.status_code = 200
    resp.raise_for_status = MagicMock()
    resp.json.return_value = {"status": "accepted", "count": 1}
    return resp


@pytest.fixture
def mock_client(mock_httpx_response):
    """Mock httpx.AsyncClient"""
    client = AsyncMock()
    client.post = AsyncMock(return_value=mock_httpx_response)
    client.put = AsyncMock(return_value=mock_httpx_response)
    client.aclose = AsyncMock()
    return client


@pytest.fixture
def bridge(mock_driver, mock_client):
    """创建启用 mock 客户端的 EventBridge"""
    b = EventBridge(
        driver=mock_driver,
        gateway_url="http://test-gateway:8900",
        project="test-project",
        flush_interval=0,  # 禁用自动刷新
        batch_size=10,
    )
    b._client = mock_client
    return b


@pytest.fixture
def bridge_no_client(mock_driver):
    """创建没有 HTTP 客户端的 EventBridge（测试无 Gateway 场景）"""
    b = EventBridge(
        driver=mock_driver,
        gateway_url="http://unreachable:8900",
        project="test-project",
        flush_interval=0,
    )
    return b


# ─── Session 管理测试 ──────────────────────────────────


class TestSessionManagement:
    @pytest.mark.anyio
    async def test_start_session_creates_via_api(self, bridge, mock_client):
        """start_session 应通过 API 创建 session"""
        sid = await bridge.start_session("my-game", "godot_driver")

        assert sid is not None
        assert sid.startswith("driver-my-game-")
        assert bridge.session_id == sid
        assert bridge.is_active

        mock_client.post.assert_called_once()
        call_args = mock_client.post.call_args
        assert call_args[0][0] == "/sessions"
        body = call_args[1]["json"]
        assert body["project"] == "my-game"
        assert body["framework"] == "godot_driver"

    @pytest.mark.anyio
    async def test_start_session_with_metadata(self, bridge, mock_client):
        """start_session 支持 metadata"""
        await bridge.start_session("game", metadata={"godot_version": "4.3"})

        body = mock_client.post.call_args[1]["json"]
        assert body["metadata"] == {"godot_version": "4.3"}

    @pytest.mark.anyio
    async def test_end_session_updates_stats(self, bridge, mock_client):
        """end_session 应更新统计数据"""
        await bridge.start_session("game")
        bridge._test_count = 5
        bridge._passed_count = 4
        bridge._failed_count = 1

        await bridge.end_session()

        # 应调用 PUT /sessions/{id}
        mock_client.put.assert_called_once()
        put_args = mock_client.put.call_args
        body = put_args[1]["json"]
        assert body["total_tests"] == 5
        assert body["passed_tests"] == 4
        assert body["failed_tests"] == 1
        assert body["ended_at"] is not None
        assert body["duration_ms"] >= 0
        assert not bridge.is_active

    @pytest.mark.anyio
    async def test_end_session_with_gate_result(self, bridge, mock_client):
        """end_session 支持 gate_result"""
        await bridge.start_session("game")
        gate = {"verdict": "PASS", "rules": {"pass_rate": True}}
        await bridge.end_session(gate_result=gate)

        body = mock_client.put.call_args[1]["json"]
        assert body["gate_result"] == gate

    @pytest.mark.anyio
    async def test_end_session_no_active(self, bridge, mock_client):
        """没有活跃 session 时 end_session 不调用 API"""
        await bridge.end_session()
        mock_client.put.assert_not_called()

    @pytest.mark.anyio
    async def test_session_id_format(self, bridge):
        """session_id 格式: driver-{project}-{timestamp_ms}"""
        sid = await bridge.start_session("my-proj")
        assert sid.startswith("driver-my-proj-")
        # 后缀应该是毫秒时间戳（最后一个 - 之后的部分）
        ts_part = sid.split("-")[-1]
        ts_value = int(ts_part)
        now = int(time.time() * 1000)
        assert abs(ts_value - now) < 5000  # 5 秒容差


# ─── 事件报告测试 ──────────────────────────────────────


class TestEventReporting:
    @pytest.mark.anyio
    async def test_report_test_start(self, bridge, mock_client):
        """report_test_start 生成 test.start 事件"""
        await bridge.start_session("game")
        await bridge.report_test_start("move_test", "suite.move_test")

        # 事件在缓冲区中
        assert bridge.buffer_size == 1

        # 手动刷新
        await bridge.flush()

        # 验证发送的事件
        post_calls = [c for c in mock_client.post.call_args_list if c[0][0] == "/events/batch"]
        assert len(post_calls) == 1
        events = post_calls[0][1]["json"]["events"]
        assert len(events) == 1
        assert events[0]["type"] == "test.start"
        assert events[0]["data"]["test_name"] == "move_test"
        assert events[0]["data"]["full_name"] == "suite.move_test"

    @pytest.mark.anyio
    async def test_report_test_end_passed(self, bridge, mock_client):
        """report_test_end passed=True 生成 test.end 事件"""
        await bridge.start_session("game")
        await bridge.report_test_end("move_test", True, 150)

        await bridge.flush()

        post_calls = [c for c in mock_client.post.call_args_list if c[0][0] == "/events/batch"]
        events = post_calls[0][1]["json"]["events"]
        assert events[0]["type"] == "test.end"
        assert events[0]["data"]["passed"] is True
        assert events[0]["data"]["duration_ms"] == 150
        assert bridge._passed_count == 1
        assert bridge._test_count == 1

    @pytest.mark.anyio
    async def test_report_test_end_failed(self, bridge, mock_client):
        """report_test_end passed=False 生成 test.fail 事件"""
        await bridge.start_session("game")
        await bridge.report_test_end("move_test", False, 80, errors=[{"message": "timeout"}])

        await bridge.flush()

        post_calls = [c for c in mock_client.post.call_args_list if c[0][0] == "/events/batch"]
        events = post_calls[0][1]["json"]["events"]
        assert events[0]["type"] == "test.fail"
        assert events[0]["data"]["errors"] == [{"message": "timeout"}]
        assert bridge._failed_count == 1

    @pytest.mark.anyio
    async def test_report_test_end_tracks_counts(self, bridge):
        """多次 report_test_end 正确累计计数"""
        await bridge.start_session("game")
        await bridge.report_test_end("t1", True, 100)
        await bridge.report_test_end("t2", True, 50)
        await bridge.report_test_end("t3", False, 200)

        assert bridge._test_count == 3
        assert bridge._passed_count == 2
        assert bridge._failed_count == 1

    @pytest.mark.anyio
    async def test_report_bench_result(self, bridge, mock_client):
        """report_bench_result 生成 bench.* 事件"""
        await bridge.start_session("game")
        await bridge.report_bench_result(
            dimension="build_health",
            score=85.0,
            passed=True,
            checks=[{"name": "compiles", "passed": True}],
        )

        await bridge.flush()

        post_calls = [c for c in mock_client.post.call_args_list if c[0][0] == "/events/batch"]
        events = post_calls[0][1]["json"]["events"]
        assert events[0]["type"] == "bench.build_health"
        assert events[0]["data"]["score"] == 85.0
        assert events[0]["data"]["passed"] is True

    @pytest.mark.anyio
    async def test_report_debug_match(self, bridge, mock_client):
        """report_debug_match 生成 debug.match 事件"""
        await bridge.start_session("game")
        await bridge.report_debug_match(
            entry_id="entry-ERR-001",
            error_code="NODE_NOT_FOUND",
            error_message="Node not found: UI/Panel",
        )

        await bridge.flush()

        post_calls = [c for c in mock_client.post.call_args_list if c[0][0] == "/events/batch"]
        events = post_calls[0][1]["json"]["events"]
        assert events[0]["type"] == "debug.match"
        assert events[0]["data"]["entry_id"] == "entry-ERR-001"
        assert events[0]["data"]["error_code"] == "NODE_NOT_FOUND"

    @pytest.mark.anyio
    async def test_report_debug_repair(self, bridge, mock_client):
        """report_debug_repair 生成 debug.repair 事件"""
        await bridge.start_session("game")
        await bridge.report_debug_repair(
            entry_id="entry-ERR-001",
            fix_description="Fixed node path",
            error_code="NODE_NOT_FOUND",
        )

        await bridge.flush()

        post_calls = [c for c in mock_client.post.call_args_list if c[0][0] == "/events/batch"]
        events = post_calls[0][1]["json"]["events"]
        assert events[0]["type"] == "debug.repair"
        assert events[0]["data"]["fix_description"] == "Fixed node path"

    @pytest.mark.anyio
    async def test_report_game_event(self, bridge, mock_client):
        """report_game_event 生成 game.* 事件"""
        await bridge.start_session("game")
        await bridge.report_game_event(
            "game.state_change",
            {"scene_path": "res://main.tscn", "state": {"hp": 100}},
        )

        await bridge.flush()

        post_calls = [c for c in mock_client.post.call_args_list if c[0][0] == "/events/batch"]
        events = post_calls[0][1]["json"]["events"]
        assert events[0]["type"] == "game.state_change"
        assert events[0]["data"]["scene_path"] == "res://main.tscn"


# ─── 截图与视觉断言测试 ────────────────────────────────


class TestScreenshotAndVisual:
    @pytest.mark.anyio
    async def test_screenshot_and_report(self, bridge, mock_client, mock_driver):
        """screenshot_and_report 调用 driver.screenshot 并报告事件"""
        await bridge.start_session("game")
        result = await bridge.screenshot_and_report(context="battle_start")

        assert result == Path("/tmp/test_screenshot.png")
        mock_driver.screenshot.assert_called_once_with(None)

        await bridge.flush()

        post_calls = [c for c in mock_client.post.call_args_list if c[0][0] == "/events/batch"]
        events = post_calls[0][1]["json"]["events"]
        assert len(events) == 1
        assert events[0]["type"] == "action.screenshot"
        assert events[0]["data"]["context"] == "battle_start"

    @pytest.mark.anyio
    async def test_screenshot_with_filename(self, bridge, mock_driver):
        """screenshot_and_report 传递 filename"""
        await bridge.start_session("game")
        await bridge.screenshot_and_report(filename="battle.png")
        mock_driver.screenshot.assert_called_once_with("battle.png")

    @pytest.mark.anyio
    async def test_visual_assert(self, bridge, mock_client):
        """visual_assert 调用 VisualAsserter 并报告事件"""
        with patch("drivers.godot.visual.TemplateMatch") as MockTemplate, patch(
            "drivers.godot.visual.VisualAsserter"
        ) as MockAsserter:
            # Mock TemplateMatch
            mock_template = MagicMock()
            MockTemplate.return_value = mock_template

            # Mock VisualAsserter
            mock_asserter = MagicMock()
            match_result = MagicMock()
            match_result.matched = True
            match_result.confidence = 0.95
            match_result.position = (100, 200)
            mock_asserter.match_template.return_value = match_result
            MockAsserter.return_value = mock_asserter

            await bridge.start_session("game")
            # Mock Path 在 event_bridge 模块中
            with patch.object(Path, "stem", "button"):
                result = await bridge.visual_assert("button.png", threshold=0.85)

        assert result["matched"] is True
        assert result["confidence"] == 0.95
        assert result["position"] == (100, 200)

        await bridge.flush()

        post_calls = [c for c in mock_client.post.call_args_list if c[0][0] == "/events/batch"]
        events = post_calls[0][1]["json"]["events"]
        assert len(events) == 1
        assert events[0]["type"] == "assert.pass"
        assert events[0]["data"]["matched"] is True

    @pytest.mark.anyio
    async def test_visual_assert_not_matched(self, bridge, mock_client):
        """visual_assert 匹配失败时报告 assert.fail"""
        with patch("drivers.godot.visual.TemplateMatch"), patch(
            "drivers.godot.visual.VisualAsserter"
        ) as MockAsserter:
            mock_asserter = MagicMock()
            match_result = MagicMock()
            match_result.matched = False
            match_result.confidence = 0.3
            match_result.position = None
            mock_asserter.match_template.return_value = match_result
            MockAsserter.return_value = mock_asserter

            await bridge.start_session("game")
            result = await bridge.visual_assert("missing.png")

        assert result["matched"] is False

        await bridge.flush()

        post_calls = [c for c in mock_client.post.call_args_list if c[0][0] == "/events/batch"]
        events = post_calls[0][1]["json"]["events"]
        assert events[0]["type"] == "assert.fail"


# ─── 批量发送测试 ──────────────────────────────────────


class TestBatchSending:
    @pytest.mark.anyio
    async def test_flush_sends_all_buffered(self, bridge, mock_client):
        """flush 发送所有缓冲事件"""
        await bridge.start_session("game")
        await bridge.report_test_start("t1")
        await bridge.report_test_start("t2")
        await bridge.report_test_start("t3")
        assert bridge.buffer_size == 3

        sent = await bridge.flush()
        assert sent == 3
        assert bridge.buffer_size == 0
        assert bridge.sent_count == 3

    @pytest.mark.anyio
    async def test_auto_flush_on_batch_size(self, bridge, mock_client):
        """达到 batch_size 时自动发送"""
        bridge._batch_size = 3
        await bridge.start_session("game")

        await bridge.report_test_start("t1")
        await bridge.report_test_start("t2")
        assert bridge.buffer_size == 2

        # 第三个事件触发自动刷新
        await bridge.report_test_start("t3")
        # 等待 asyncio.create_task 执行
        await asyncio.sleep(0.05)

        # 缓冲区应该已清空（自动发送后）
        assert bridge.buffer_size == 0

    @pytest.mark.anyio
    async def test_end_session_flushes(self, bridge, mock_client):
        """end_session 刷新剩余事件"""
        await bridge.start_session("game")
        await bridge.report_test_start("t1")
        await bridge.report_test_start("t2")
        assert bridge.buffer_size == 2

        await bridge.end_session()

        # 验证事件已发送
        batch_calls = [c for c in mock_client.post.call_args_list if c[0][0] == "/events/batch"]
        assert len(batch_calls) >= 1
        total_events = sum(len(c[1]["json"]["events"]) for c in batch_calls)
        assert total_events == 2

    @pytest.mark.anyio
    async def test_buffer_overflow_evicts_oldest(self, bridge):
        """缓冲区溢出时丢弃最旧事件"""
        bridge._max_buffer = 3
        await bridge.start_session("game")

        for i in range(5):
            await bridge.report_test_start(f"t{i}")

        assert bridge.buffer_size == 3  # max_buffer

    @pytest.mark.anyio
    async def test_events_have_session_id(self, bridge, mock_client):
        """所有事件包含正确的 session_id"""
        sid = await bridge.start_session("game")
        await bridge.report_test_start("t1")
        await bridge.flush()

        post_calls = [c for c in mock_client.post.call_args_list if c[0][0] == "/events/batch"]
        events = post_calls[0][1]["json"]["events"]
        assert events[0]["session_id"] == sid

    @pytest.mark.anyio
    async def test_events_have_source(self, bridge, mock_client):
        """所有事件包含正确的 source"""
        await bridge.start_session("game", "godot_driver")
        await bridge.report_test_start("t1")
        await bridge.flush()

        post_calls = [c for c in mock_client.post.call_args_list if c[0][0] == "/events/batch"]
        events = post_calls[0][1]["json"]["events"]
        assert events[0]["source"]["framework"] == "godot_driver"
        assert events[0]["source"]["project"] == "game"


# ─── 容错测试 ──────────────────────────────────────────


class TestFaultTolerance:
    @pytest.mark.anyio
    async def test_start_session_tolerates_api_failure(self, mock_driver, mock_client):
        """Gateway 不可用时 start_session 不抛异常"""
        mock_client.post.side_effect = Exception("Connection refused")
        bridge = EventBridge(
            driver=mock_driver,
            gateway_url="http://unreachable:8900",
            project="game",
            flush_interval=0,
        )
        bridge._client = mock_client

        sid = await bridge.start_session("game")
        assert sid is not None
        assert bridge.is_active
        assert bridge.error_count == 1

    @pytest.mark.anyio
    async def test_end_session_tolerates_api_failure(self, mock_driver, mock_client):
        """Gateway 不可用时 end_session 不抛异常"""
        bridge = EventBridge(
            driver=mock_driver,
            gateway_url="http://test:8900",
            project="game",
            flush_interval=0,
        )
        bridge._client = mock_client
        await bridge.start_session("game")

        mock_client.put.side_effect = Exception("Timeout")
        mock_client.post.side_effect = Exception("Timeout")

        await bridge.end_session()  # 不应抛异常
        assert not bridge.is_active

    @pytest.mark.anyio
    async def test_flush_tolerates_send_failure(self, bridge, mock_client):
        """发送失败时事件保留在缓冲区"""
        await bridge.start_session("game")
        await bridge.report_test_start("t1")
        await bridge.report_test_start("t2")

        mock_client.post.side_effect = Exception("Server error")
        sent = await bridge.flush()
        assert sent == 0
        # 事件应被放回缓冲区
        assert bridge.buffer_size == 2

    @pytest.mark.anyio
    async def test_no_client_no_crash(self, bridge_no_client):
        """没有 HTTP 客户端时操作不崩溃"""
        await bridge_no_client.start_session("game")
        await bridge_no_client.report_test_start("t1")
        await bridge_no_client.report_test_end("t1", True, 100)
        await bridge_no_client.report_bench_result("build_health", 80, True)
        await bridge_no_client.report_game_event("game.state_change", {"state": "play"})
        # 所有操作不应抛异常
        assert bridge_no_client.buffer_size > 0

    @pytest.mark.anyio
    async def test_auto_flush_loop_cancellation(self, mock_driver, mock_client):
        """auto_flush_loop 在 __aexit__ 时正确取消"""
        bridge = EventBridge(
            driver=mock_driver,
            gateway_url="http://test:8900",
            flush_interval=0.1,
        )
        # 直接设置 mock 客户端，避免 httpx 创建
        bridge._client = mock_client

        async with bridge as b:
            await b.start_session("game")
            await b.report_test_start("t1")
            # 显式结束 session（__aexit__ 不自动结束）
            await b.end_session()

        # 退出后不应有活跃 session
        assert not bridge.is_active
        # 确认 flush task 已结束（被取消或完成）
        assert bridge._flush_task is not None
        assert bridge._flush_task.done()

    @pytest.mark.anyio
    async def test_trace_id_propagation(self, bridge, mock_client):
        """trace_id 正确传递到事件"""
        await bridge.start_session("game")
        await bridge.report_test_start("t1", trace_id="trace-abc")
        await bridge.report_test_end("t1", True, 100, trace_id="trace-abc")
        await bridge.flush()

        post_calls = [c for c in mock_client.post.call_args_list if c[0][0] == "/events/batch"]
        for event in post_calls[0][1]["json"]["events"]:
            assert event["trace_id"] == "trace-abc"


# ─── 属性测试 ──────────────────────────────────────────


class TestProperties:
    @pytest.mark.anyio
    async def test_initial_state(self, bridge):
        """初始状态正确"""
        assert bridge.session_id is None
        assert not bridge.is_active
        assert bridge.sent_count == 0
        assert bridge.error_count == 0
        assert bridge.buffer_size == 0

    @pytest.mark.anyio
    async def test_sent_count_increments(self, bridge, mock_client):
        """sent_count 在成功发送后递增"""
        await bridge.start_session("game")
        await bridge.report_test_start("t1")
        await bridge.flush()
        assert bridge.sent_count == 1

        await bridge.report_test_start("t2")
        await bridge.flush()
        assert bridge.sent_count == 2

    @pytest.mark.anyio
    async def test_error_count_on_failure(self, bridge, mock_client):
        """error_count 在发送失败后递增"""
        await bridge.start_session("game")
        await bridge.report_test_start("t1")

        mock_client.post.side_effect = Exception("fail")
        await bridge.flush()
        assert bridge.error_count >= 1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
