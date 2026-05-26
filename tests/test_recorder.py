"""GameRecorder / GameReplayer 全面测试

覆盖录制 → 保存 → 加载 → 回放的完整流程。
用 mock driver 测试，不需要真实 Godot 进程。
"""

import asyncio
import json
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import sys

sys.path.insert(0, str(Path(__file__).parent.parent))


# ══════════════════════════════════════════════════════════════
# GameRecorder 测试
# ══════════════════════════════════════════════════════════════


from drivers.godot.recorder import GameRecorder, RecordedAction, RecordingResult


class TestRecordedAction:
    def test_creation(self):
        action = RecordedAction(
            timestamp=1716700001000,
            type="click_node",
            params={"path": "UI/Button"},
            screenshot="test.png",
            index=0,
        )
        assert action.timestamp == 1716700001000
        assert action.type == "click_node"
        assert action.params == {"path": "UI/Button"}
        assert action.screenshot == "test.png"
        assert action.index == 0

    def test_to_dict(self):
        action = RecordedAction(
            timestamp=1716700001000,
            type="input_key",
            params={"keycode": 4194305},
            index=1,
        )
        d = action.to_dict()
        assert d == {
            "timestamp": 1716700001000,
            "type": "input_key",
            "params": {"keycode": 4194305},
            "index": 1,
        }
        assert "screenshot" not in d  # None 时不包含

    def test_to_dict_with_screenshot(self):
        action = RecordedAction(
            timestamp=1716700001000,
            type="click_node",
            params={"path": "UI/Button"},
            screenshot="shot.png",
            index=0,
        )
        d = action.to_dict()
        assert d["screenshot"] == "shot.png"


class TestRecordingResult:
    def test_properties(self):
        result = RecordingResult(
            session_id="test-1",
            json_path=Path("test.json"),
            action_count=5,
            duration_ms=10000,
            screenshots=["a.png", "b.png"],
            metadata={"project": "game"},
        )
        assert result.duration_s == 10.0
        assert len(result.screenshots) == 2


class TestGameRecorder:
    @pytest.fixture
    def mock_driver(self):
        driver = AsyncMock()
        driver.get_scene = AsyncMock(return_value="res://main.tscn")
        driver.screenshot = AsyncMock(return_value=Path("screenshot.png"))
        return driver

    @pytest.fixture
    def tmp_output(self, tmp_path):
        return str(tmp_path / "recordings")

    @pytest.mark.asyncio
    async def test_start_stop_recording(self, mock_driver, tmp_output):
        recorder = GameRecorder(mock_driver, output_dir=tmp_output)

        session_id = await recorder.start_recording("test-1")
        assert session_id == "test-1"
        assert recorder.is_recording

        result = await recorder.stop_recording()
        assert result.session_id == "test-1"
        assert result.action_count == 0
        assert not recorder.is_recording

    @pytest.mark.asyncio
    async def test_auto_session_id(self, mock_driver, tmp_output):
        recorder = GameRecorder(mock_driver, output_dir=tmp_output)

        session_id = await recorder.start_recording()
        assert session_id.startswith("rec-")
        await recorder.stop_recording()

    @pytest.mark.asyncio
    async def test_record_actions(self, mock_driver, tmp_output):
        recorder = GameRecorder(mock_driver, output_dir=tmp_output)

        await recorder.start_recording("test-2")

        a1 = await recorder.record_action("click_node", {"path": "UI/Button"})
        assert a1.index == 0
        assert a1.type == "click_node"

        a2 = await recorder.record_action("input_key", {"keycode": 4194305})
        assert a2.index == 1

        a3 = await recorder.record_action("input_action", {"action": "move_up"})
        assert a3.index == 2

        assert recorder.action_count == 3

        result = await recorder.stop_recording()
        assert result.action_count == 3

    @pytest.mark.asyncio
    async def test_auto_screenshot(self, mock_driver, tmp_output):
        recorder = GameRecorder(
            mock_driver,
            output_dir=tmp_output,
            auto_screenshot=True,
        )

        await recorder.start_recording("test-3")

        action = await recorder.record_action("click_node", {"path": "UI/Button"})
        assert action.screenshot is not None  # 自动截图

        result = await recorder.stop_recording()
        assert len(result.screenshots) == 1
        mock_driver.screenshot.assert_called()

    @pytest.mark.asyncio
    async def test_manual_screenshot(self, mock_driver, tmp_output):
        recorder = GameRecorder(mock_driver, output_dir=tmp_output)

        await recorder.start_recording("test-4")

        action = await recorder.record_action(
            "click_node",
            {"path": "UI/Button"},
            screenshot_path="manual.png",
        )
        assert action.screenshot == "manual.png"

        await recorder.stop_recording()

    @pytest.mark.asyncio
    async def test_metadata(self, mock_driver, tmp_output):
        recorder = GameRecorder(mock_driver, output_dir=tmp_output)

        await recorder.start_recording(
            "test-5",
            metadata={"project": "my_game", "scene": "res://main.tscn"},
        )

        result = await recorder.stop_recording()
        assert result.metadata["project"] == "my_game"
        assert result.metadata["start_scene"] == "res://main.tscn"  # 从 driver 获取

    @pytest.mark.asyncio
    async def test_save_json(self, mock_driver, tmp_output):
        recorder = GameRecorder(mock_driver, output_dir=tmp_output)

        await recorder.start_recording("test-6")

        await recorder.record_action("click_node", {"path": "UI/Button"})
        await recorder.record_action("input_key", {"keycode": 4194305})
        await recorder.record_action(
            "input_action",
            {"action": "move_up"},
            screenshot_path="move.png",
        )

        result = await recorder.stop_recording()

        # 验证 JSON 文件
        assert result.json_path.exists()
        data = json.loads(result.json_path.read_text(encoding="utf-8"))

        assert data["session_id"] == "test-6"
        assert "started_at" in data
        assert "ended_at" in data
        assert len(data["actions"]) == 3

        # 验证操作内容
        assert data["actions"][0]["type"] == "click_node"
        assert data["actions"][0]["params"] == {"path": "UI/Button"}
        assert data["actions"][1]["type"] == "input_key"
        assert data["actions"][2]["screenshot"] == "move.png"

    @pytest.mark.asyncio
    async def test_error_not_recording(self, mock_driver, tmp_output):
        recorder = GameRecorder(mock_driver, output_dir=tmp_output)

        with pytest.raises(RuntimeError, match="未在录制中"):
            await recorder.record_action("click_node", {"path": "UI/Button"})

    @pytest.mark.asyncio
    async def test_error_already_recording(self, mock_driver, tmp_output):
        recorder = GameRecorder(mock_driver, output_dir=tmp_output)
        await recorder.start_recording("test-err")

        with pytest.raises(RuntimeError, match="录制已在进行中"):
            await recorder.start_recording("test-err-2")

        await recorder.stop_recording()

    @pytest.mark.asyncio
    async def test_error_stop_not_recording(self, mock_driver, tmp_output):
        recorder = GameRecorder(mock_driver, output_dir=tmp_output)

        with pytest.raises(RuntimeError, match="未在录制中"):
            await recorder.stop_recording()

    @pytest.mark.asyncio
    async def test_get_actions(self, mock_driver, tmp_output):
        recorder = GameRecorder(mock_driver, output_dir=tmp_output)
        await recorder.start_recording("test-7")

        await recorder.record_action("click_node", {"path": "A"})
        await recorder.record_action("click_node", {"path": "B"})

        actions = recorder.get_actions()
        assert len(actions) == 2
        assert actions[0].params["path"] == "A"
        assert actions[1].params["path"] == "B"

        await recorder.stop_recording()


# ══════════════════════════════════════════════════════════════
# GameReplayer 测试
# ══════════════════════════════════════════════════════════════


from drivers.godot.replayer import GameReplayer, ReplayResult, ReplayStep, VerifyResult


class TestReplayStep:
    def test_creation(self):
        step = ReplayStep(
            index=0,
            action_type="click_node",
            params={"path": "UI/Button"},
            passed=True,
            duration_ms=100,
        )
        assert step.index == 0
        assert step.passed is True
        assert step.visual_match is False  # 默认值


class TestReplayResult:
    def test_passed(self):
        result = ReplayResult(
            session_id="test",
            total_steps=3,
            passed_steps=3,
            failed_steps=0,
            skipped_steps=0,
            duration_ms=1000,
        )
        assert result.passed is True
        assert result.pass_rate == 1.0

    def test_failed(self):
        result = ReplayResult(
            session_id="test",
            total_steps=3,
            passed_steps=2,
            failed_steps=1,
            skipped_steps=0,
            duration_ms=1000,
        )
        assert result.passed is False
        assert abs(result.pass_rate - 2 / 3) < 0.001

    def test_empty(self):
        result = ReplayResult(
            session_id="test",
            total_steps=0,
            passed_steps=0,
            failed_steps=0,
            skipped_steps=0,
            duration_ms=0,
        )
        assert result.pass_rate == 0.0


class TestGameReplayer:
    @pytest.fixture
    def recording_file(self, tmp_path):
        """创建一个测试录制文件"""
        recording = {
            "session_id": "test-rec-1",
            "started_at": 1716700000000,
            "ended_at": 1716700060000,
            "metadata": {
                "project": "test_game",
                "start_scene": "res://main.tscn",
            },
            "actions": [
                {
                    "timestamp": 1716700001000,
                    "type": "click_node",
                    "params": {"path": "UI/Button"},
                    "screenshot": "button_before.png",
                    "index": 0,
                },
                {
                    "timestamp": 1716700002000,
                    "type": "input_key",
                    "params": {"keycode": 4194305},
                    "index": 1,
                },
                {
                    "timestamp": 1716700003000,
                    "type": "input_action",
                    "params": {"action": "move_up", "strength": 1.0},
                    "index": 2,
                },
                {
                    "timestamp": 1716700004000,
                    "type": "change_scene",
                    "params": {"scene_path": "res://battle.tscn"},
                    "index": 3,
                },
            ],
        }
        json_path = tmp_path / "test-recording.json"
        json_path.write_text(json.dumps(recording, indent=2), encoding="utf-8")
        return str(json_path)

    @pytest.fixture
    def mock_driver(self):
        driver = AsyncMock()
        driver.click_node = AsyncMock()
        driver.input_key = AsyncMock()
        driver.input_action = AsyncMock()
        driver.change_scene = AsyncMock()
        driver.get_scene = AsyncMock(return_value="res://battle.tscn")
        driver.screenshot = AsyncMock(return_value=Path("screenshot.png"))
        driver._send = AsyncMock()
        return driver

    def test_load_recording(self, recording_file):
        replayer = GameReplayer()
        replayer.load_recording(recording_file)

        assert replayer.is_loaded
        assert replayer.action_count == 4
        assert replayer.session_id == "test-rec-1"

    def test_load_missing_file(self):
        replayer = GameReplayer()
        with pytest.raises(FileNotFoundError):
            replayer.load_recording("/nonexistent/file.json")

    def test_load_invalid_json(self, tmp_path):
        bad_file = tmp_path / "bad.json"
        bad_file.write_text("not json", encoding="utf-8")

        replayer = GameReplayer()
        with pytest.raises(ValueError, match="JSON 解析错误"):
            replayer.load_recording(str(bad_file))

    def test_load_invalid_format(self, tmp_path):
        bad_file = tmp_path / "bad_format.json"
        bad_file.write_text(json.dumps({"no_actions": True}), encoding="utf-8")

        replayer = GameReplayer()
        with pytest.raises(ValueError, match="缺少 'actions' 字段"):
            replayer.load_recording(str(bad_file))

    @pytest.mark.asyncio
    async def test_replay_basic(self, recording_file, mock_driver):
        replayer = GameReplayer()
        replayer.load_recording(recording_file)

        result = await replayer.replay(mock_driver, speed=0, verify=False)

        assert result.session_id == "test-rec-1"
        assert result.total_steps == 4
        assert result.passed_steps == 4
        assert result.failed_steps == 0
        assert result.passed is True

        # 验证 driver 方法被调用
        mock_driver.click_node.assert_called_once_with("UI/Button")
        mock_driver.input_key.assert_called_once_with(4194305, True)
        mock_driver.input_action.assert_called_once_with("move_up", 1.0)
        mock_driver.change_scene.assert_called_once_with("res://battle.tscn")

    @pytest.mark.asyncio
    async def test_replay_with_delay(self, recording_file, mock_driver):
        replayer = GameReplayer()
        replayer.load_recording(recording_file)

        # speed=0 应该很快完成
        start = time.time()
        result = await replayer.replay(mock_driver, speed=0, verify=False)
        elapsed = time.time() - start

        assert elapsed < 1.0  # 不应有明显延迟
        assert result.passed

    @pytest.mark.asyncio
    async def test_replay_with_speed(self, recording_file, mock_driver):
        replayer = GameReplayer()
        replayer.load_recording(recording_file)

        # speed=10 应该很快（10倍速）
        start = time.time()
        result = await replayer.replay(mock_driver, speed=10, verify=False)
        elapsed = time.time() - start

        assert elapsed < 2.0
        assert result.passed

    @pytest.mark.asyncio
    async def test_replay_invalid_speed(self, recording_file, mock_driver):
        replayer = GameReplayer()
        replayer.load_recording(recording_file)

        with pytest.raises(ValueError, match="speed 不能为负数"):
            await replayer.replay(mock_driver, speed=-1, verify=False)

    @pytest.mark.asyncio
    async def test_replay_not_loaded(self, mock_driver):
        replayer = GameReplayer()

        with pytest.raises(RuntimeError, match="未加载录制文件"):
            await replayer.replay(mock_driver)

    @pytest.mark.asyncio
    async def test_replay_action_failure(self, recording_file):
        # 创建一个会失败的 driver
        driver = AsyncMock()
        driver.click_node = AsyncMock(side_effect=Exception("节点不存在"))
        driver.input_key = AsyncMock()
        driver.input_action = AsyncMock()
        driver.change_scene = AsyncMock()

        replayer = GameReplayer()
        replayer.load_recording(recording_file)

        result = await replayer.replay(driver, speed=0, verify=False)

        assert result.passed is False
        assert result.failed_steps == 1
        assert result.passed_steps == 3

        # 找到失败的步骤
        failed_step = next(s for s in result.steps if not s.passed)
        assert failed_step.index == 0
        assert "节点不存在" in failed_step.error

    @pytest.mark.asyncio
    async def test_replay_unknown_action(self, mock_driver):
        """测试未知操作类型"""
        recording = {
            "session_id": "test-unknown",
            "actions": [
                {"timestamp": 0, "type": "custom_action", "params": {"data": "test"}, "index": 0}
            ],
        }
        json_path = Path(mock_driver.screenshot.return_value).parent / "test.json"
        # 直接使用 replayer 内部方法测试
        replayer = GameReplayer()

        # 创建临时文件
        import tempfile

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(recording, f)
            tmp_path = f.name

        replayer.load_recording(tmp_path)

        # _send 会被调用作为 fallback
        mock_driver._send = AsyncMock()
        result = await replayer.replay(mock_driver, speed=0, verify=False)
        assert result.passed  # 通过 _send fallback 成功

        import os
        os.unlink(tmp_path)

    @pytest.mark.asyncio
    async def test_replay_on_step_callback(self, recording_file, mock_driver):
        """测试步骤回调"""
        steps_received = []

        def on_step(step):
            steps_received.append(step)

        replayer = GameReplayer(on_step=on_step)
        replayer.load_recording(recording_file)

        await replayer.replay(mock_driver, speed=0, verify=False)

        assert len(steps_received) == 4
        assert all(isinstance(s, ReplayStep) for s in steps_received)

    @pytest.mark.asyncio
    async def test_replay_verify_indices(self, recording_file, mock_driver):
        """测试指定索引验证"""
        replayer = GameReplayer(verify_screenshots=False)
        replayer.load_recording(recording_file)

        # 只验证第 0 和第 2 步
        result = await replayer.replay(
            mock_driver,
            speed=0,
            verify=True,
            verify_indices=[0, 2],
        )

        assert result.passed

    @pytest.mark.asyncio
    async def test_verify_recording(self, recording_file, mock_driver):
        """测试录制验证"""
        replayer = GameReplayer()
        replayer.load_recording(recording_file)

        # mock_driver.get_scene 返回 res://battle.tscn，与 metadata.start_scene (res://main.tscn) 不同
        result = await replayer.verify_recording(mock_driver, check_screenshots=False)

        assert result.checks_total == 1  # 只有 scene check
        assert result.checks_failed == 1  # 场景不匹配
        assert result.passed is False

    @pytest.mark.asyncio
    async def test_verify_recording_scene_match(self, recording_file, mock_driver):
        """测试场景匹配验证"""
        mock_driver.get_scene = AsyncMock(return_value="res://main.tscn")

        replayer = GameReplayer()
        replayer.load_recording(recording_file)

        result = await replayer.verify_recording(mock_driver, check_screenshots=False)

        assert result.checks_total == 1
        assert result.checks_passed == 1
        assert result.passed is True

    @pytest.mark.asyncio
    async def test_verify_not_loaded(self, mock_driver):
        replayer = GameReplayer()

        with pytest.raises(RuntimeError, match="未加载录制文件"):
            await replayer.verify_recording(mock_driver)


# ══════════════════════════════════════════════════════════════
# 完整流程测试（录制 → 保存 → 加载 → 回放）
# ══════════════════════════════════════════════════════════════


class TestFullWorkflow:
    """端到端流程测试"""

    @pytest.fixture
    def mock_driver(self):
        driver = AsyncMock()
        driver.get_scene = AsyncMock(return_value="res://main.tscn")
        driver.screenshot = AsyncMock(return_value=Path("screenshot.png"))
        driver.click_node = AsyncMock()
        driver.input_key = AsyncMock()
        driver.input_action = AsyncMock()
        driver.change_scene = AsyncMock()
        driver.reload_scene = AsyncMock()
        driver.wait_seconds = AsyncMock()
        return driver

    @pytest.mark.asyncio
    async def test_record_save_load_replay(self, mock_driver, tmp_path):
        """完整流程：录制 → 保存 → 加载 → 回放"""
        output_dir = str(tmp_path / "recordings")

        # 1. 录制
        recorder = GameRecorder(mock_driver, output_dir=output_dir)
        await recorder.start_recording("e2e-test", metadata={"project": "test"})

        await recorder.record_action("click_node", {"path": "PlayButton"})
        await recorder.record_action("wait_seconds", {"seconds": 1.0})
        await recorder.record_action("input_action", {"action": "move_right", "strength": 0.8})
        await recorder.record_action("input_key", {"keycode": 4194305})
        await recorder.record_action("change_scene", {"scene_path": "res://game.tscn"})

        result = await recorder.stop_recording()

        # 验证录制结果
        assert result.action_count == 5
        assert result.json_path.exists()

        # 2. 加载
        replayer = GameReplayer()
        replayer.load_recording(str(result.json_path))

        assert replayer.is_loaded
        assert replayer.action_count == 5
        assert replayer.session_id == "e2e-test"

        # 3. 回放
        replay_result = await replayer.replay(mock_driver, speed=0, verify=False)

        assert replay_result.passed
        assert replay_result.total_steps == 5
        assert replay_result.passed_steps == 5

        # 验证操作顺序
        mock_driver.click_node.assert_called_once_with("PlayButton")
        mock_driver.wait_seconds.assert_called_once_with(1.0)
        mock_driver.input_action.assert_called_once_with("move_right", 0.8)
        mock_driver.input_key.assert_called_once_with(4194305, True)
        mock_driver.change_scene.assert_called_once_with("res://game.tscn")

    @pytest.mark.asyncio
    async def test_json_readability(self, mock_driver, tmp_path):
        """验证 JSON 格式可读性"""
        output_dir = str(tmp_path / "recordings")
        recorder = GameRecorder(mock_driver, output_dir=output_dir)

        await recorder.start_recording("readable-test")
        await recorder.record_action("click_node", {"path": "UI/StartButton"})
        await recorder.record_action("input_action", {"action": "jump", "strength": 1.0})

        result = await recorder.stop_recording()

        # 读取并验证 JSON 结构
        data = json.loads(result.json_path.read_text(encoding="utf-8"))

        # 顶层字段
        assert "session_id" in data
        assert "started_at" in data
        assert "ended_at" in data
        assert "metadata" in data
        assert "actions" in data

        # 操作字段
        action = data["actions"][0]
        assert "timestamp" in action
        assert "type" in action
        assert "params" in action
        assert "index" in action

        # 确保 JSON 是缩进格式（可读）
        raw_text = result.json_path.read_text(encoding="utf-8")
        assert "\n  " in raw_text  # 有缩进

    @pytest.mark.asyncio
    async def test_screenshot_recording(self, mock_driver, tmp_path):
        """测试截图记录流程"""
        output_dir = str(tmp_path / "recordings")
        recorder = GameRecorder(
            mock_driver,
            output_dir=output_dir,
            auto_screenshot=True,
        )

        await recorder.start_recording("screenshot-test")
        await recorder.record_action("click_node", {"path": "UI/Button"})
        await recorder.record_action("input_key", {"keycode": 4194305})

        result = await recorder.stop_recording()

        # 验证截图被记录
        assert len(result.screenshots) == 2
        assert mock_driver.screenshot.call_count == 2

        # 验证 JSON 中有截图信息
        data = json.loads(result.json_path.read_text(encoding="utf-8"))
        assert data["actions"][0]["screenshot"] is not None
        assert data["actions"][1]["screenshot"] is not None

    @pytest.mark.asyncio
    async def test_partial_replay_failure(self, mock_driver, tmp_path):
        """测试部分回放失败"""
        output_dir = str(tmp_path / "recordings")
        recorder = GameRecorder(mock_driver, output_dir=output_dir)

        await recorder.start_recording("partial-fail")
        await recorder.record_action("click_node", {"path": "UI/Button"})
        await recorder.record_action("click_node", {"path": "UI/MissingButton"})
        await recorder.record_action("input_key", {"keycode": 4194305})

        result = await recorder.stop_recording()

        # 创建会失败的 driver
        fail_driver = AsyncMock()
        fail_driver.click_node = AsyncMock(side_effect=[
            None,  # 第一次成功
            Exception("节点不存在"),  # 第二次失败
        ])
        fail_driver.input_key = AsyncMock()

        replayer = GameReplayer()
        replayer.load_recording(str(result.json_path))

        replay_result = await replayer.replay(fail_driver, speed=0, verify=False)

        assert replay_result.passed is False
        assert replay_result.passed_steps == 2
        assert replay_result.failed_steps == 1


# ══════════════════════════════════════════════════════════════
# EventBridge 集成测试
# ══════════════════════════════════════════════════════════════


class TestEventBridgeIntegration:
    """测试从 EventBridge 自动截获录制"""

    @pytest.fixture
    def mock_driver(self):
        driver = AsyncMock()
        driver.get_scene = AsyncMock(return_value="res://main.tscn")
        driver.screenshot = AsyncMock(return_value=Path("screenshot.png"))
        return driver

    @pytest.mark.asyncio
    async def test_from_bridge(self, mock_driver, tmp_path):
        """测试从 EventBridge 创建录制器"""
        from drivers.godot.event_bridge import EventBridge

        # 创建 mock bridge
        bridge = MagicMock(spec=EventBridge)
        bridge._enqueue = AsyncMock()
        bridge._session_id = "bridge-session"
        bridge._framework = "test"

        output_dir = str(tmp_path / "recordings")
        recorder = GameRecorder.from_bridge(bridge, mock_driver, output_dir)

        # 开始录制
        await recorder.start_recording("bridge-test")

        # 模拟 EventBridge 事件（直接调用录制方法，因为 hook 是异步的）
        await recorder.record_action("click_node", {"path": "UI/Button"})
        await recorder.record_action("input_key", {"keycode": 4194305})

        result = await recorder.stop_recording()

        assert result.action_count == 2
        assert result.json_path.exists()
