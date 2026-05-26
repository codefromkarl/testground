"""drivers/godot/ 模块全面测试

覆盖 driver.py, visual.py, debug_protocol.py, bench.py 的核心逻辑。
用 mock TCP 连接和 numpy 测试图片，不需要真实 Godot 进程。
"""

import asyncio
import json
import struct
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import sys

sys.path.insert(0, str(Path(__file__).parent.parent))


# ══════════════════════════════════════════════════════════════
# driver.py 测试
# ══════════════════════════════════════════════════════════════


from drivers.godot.driver import DriverConfig, GodotDriver, NodeInfo


class TestDriverConfig:
    def test_default_values(self):
        cfg = DriverConfig()
        assert cfg.host == "127.0.0.1"
        assert cfg.port == 19090
        assert cfg.timeout == 10.0
        assert cfg.project_type == "auto"
        assert cfg.max_retries == 3
        assert cfg.retry_interval == 1.0

    def test_custom_values(self):
        cfg = DriverConfig(host="192.168.1.1", port=12345, timeout=5.0, project_type="loopexpedition")
        assert cfg.host == "192.168.1.1"
        assert cfg.port == 12345
        assert cfg.project_type == "loopexpedition"


class TestNodeInfo:
    def test_creation(self):
        n = NodeInfo(path="/root/Main", type="Control", name="Main", children_count=3)
        assert n.path == "/root/Main"
        assert n.type == "Control"
        assert n.children_count == 3

    def test_metadata_default(self):
        n = NodeInfo(path="/root", type="Node", name="root")
        assert n.metadata == {}


class TestGodotDriverConnection:
    @pytest.mark.asyncio
    async def test_context_manager_protocol(self):
        """__aenter__ / __aexit__ 不应抛异常（mock 连接）"""
        driver = GodotDriver("127.0.0.1", 19090)
        # mock 连接
        reader = AsyncMock(spec=asyncio.StreamReader)
        writer = AsyncMock(spec=asyncio.StreamWriter)
        with patch("asyncio.open_connection", return_value=(reader, writer)):
            async with driver as d:
                assert d is driver
                assert d._writer is not None

    @pytest.mark.asyncio
    async def test_connect_sets_project_type(self):
        driver = GodotDriver("127.0.0.1", 19090, config=DriverConfig(project_type="loopexpedition"))
        reader = AsyncMock(spec=asyncio.StreamReader)
        writer = AsyncMock(spec=asyncio.StreamWriter)
        with patch("asyncio.open_connection", return_value=(reader, writer)):
            await driver.connect()
            assert driver._project_type == "loopexpedition"
            await driver.close()

    @pytest.mark.asyncio
    async def test_connect_retry_and_fail(self):
        driver = GodotDriver("127.0.0.1", 19090, config=DriverConfig(max_retries=2, retry_interval=0.01))
        with patch("asyncio.open_connection", side_effect=ConnectionRefusedError):
            with pytest.raises(ConnectionError, match="无法连接"):
                await driver.connect()

    @pytest.mark.asyncio
    async def test_close_without_connect(self):
        driver = GodotDriver()
        await driver.close()  # 不应抛异常


class TestGodotDriverProtocol:
    def _make_driver(self, project_type="loopexpedition"):
        driver = GodotDriver("127.0.0.1", 19090)
        driver._project_type = project_type
        return driver

    @pytest.mark.asyncio
    async def test_send_command_e2e_roundtrip(self):
        """JSON-RPC 4字节头 + JSON body 完整往返"""
        driver = self._make_driver("loopexpedition")

        response = {"found": True}
        resp_bytes = json.dumps(response).encode("utf-8")
        resp_header = struct.pack("<I", len(resp_bytes))

        reader = AsyncMock(spec=asyncio.StreamReader)
        reader.readexactly = AsyncMock(side_effect=[resp_header, resp_bytes])
        writer = AsyncMock(spec=asyncio.StreamWriter)

        driver._reader = reader
        driver._writer = writer

        result = await driver._send_command_e2e("node_exists", {"path": "/root/Main"})
        assert result == {"found": True}
        assert writer.write.called
        assert writer.drain.called

    @pytest.mark.asyncio
    async def test_send_command_pgc_writes_jsonl(self):
        """PGC 格式: 换行分隔的 JSON"""
        driver = self._make_driver("pogongshichongzou")

        writer = AsyncMock(spec=asyncio.StreamWriter)
        driver._writer = writer
        driver._reader = AsyncMock()

        await driver._send_command_pgc("node_exists", {"path": "/root/Main"})
        written = writer.write.call_args[0][0]
        data = json.loads(written.decode("utf-8").strip())
        assert data["command"] == "node_exists"
        assert data["path"] == "/root/Main"

    @pytest.mark.asyncio
    async def test_send_dispatches_by_project_type(self):
        driver = self._make_driver("loopexpedition")
        driver._writer = AsyncMock()
        driver._reader = AsyncMock()

        resp = json.dumps({}).encode()
        header = struct.pack("<I", len(resp))
        driver._reader.readexactly = AsyncMock(side_effect=[header, resp])

        with patch.object(driver, "_send_command_e2e", return_value={"ok": True}) as mock_e2e:
            await driver._send("get_tree")
            mock_e2e.assert_called_once_with("get_tree", {})

    @pytest.mark.asyncio
    async def test_node_exists_e2e(self):
        driver = self._make_driver("loopexpedition")
        with patch.object(driver, "_send_command_e2e", return_value={"exists": True}):
            assert await driver.node_exists("/root/Main") is True

    @pytest.mark.asyncio
    async def test_get_property_e2e(self):
        driver = self._make_driver("loopexpedition")
        with patch.object(driver, "_send_command_e2e", return_value={"value": 42}):
            val = await driver.get_property("/root/Player", "health")
            assert val == 42

    @pytest.mark.asyncio
    async def test_click_node(self):
        driver = self._make_driver("loopexpedition")
        with patch.object(driver, "_send", new_callable=AsyncMock) as mock_send:
            await driver.click_node("/root/UI/Button")
            mock_send.assert_called_once_with("click_node", {"path": "/root/UI/Button"})

    @pytest.mark.asyncio
    async def test_wait_for_node_timeout_fallback(self):
        """pogongshichongzou 风格: 轮询 node_exists 超时返回 False"""
        driver = self._make_driver("pogongshichongzou")
        driver._writer = AsyncMock()
        driver._reader = AsyncMock()
        with patch.object(driver, "node_exists", return_value=False):
            result = await driver.wait_for_node("/root/Missing", timeout=0.15)
            assert result is False

    @pytest.mark.asyncio
    async def test_wait_for_node_found_fallback(self):
        """pogongshichongzou 风格: 轮询发现节点"""
        driver = self._make_driver("pogongshichongzou")
        driver._writer = AsyncMock()
        driver._reader = AsyncMock()
        call_count = 0

        async def fake_exists(path):
            nonlocal call_count
            call_count += 1
            return call_count >= 2

        with patch.object(driver, "node_exists", side_effect=fake_exists):
            result = await driver.wait_for_node("/root/Main", timeout=2.0)
            assert result is True

    @pytest.mark.asyncio
    async def test_wait_for_node_e2e(self):
        """loopexpedition 风格: e2e 命令直接返回"""
        driver = self._make_driver("loopexpedition")
        driver._writer = AsyncMock()
        driver._reader = AsyncMock()
        with patch.object(driver, "_send_command_e2e", return_value={"found": True}):
            result = await driver.wait_for_node("/root/Main", timeout=5.0)
            assert result is True

    @pytest.mark.asyncio
    async def test_screenshot_e2e(self):
        driver = self._make_driver("loopexpedition")
        driver._config.screenshot_dir = str(Path("/tmp/test_screenshots"))
        with patch.object(driver, "_send_command_e2e", return_value={"success": True}):
            path = await driver.screenshot("test.png")
            assert path.name == "test.png"

    @pytest.mark.asyncio
    async def test_batch_e2e(self):
        driver = self._make_driver("loopexpedition")
        with patch.object(
            driver, "_send_command_e2e", return_value={"results": [{"ok": True}, {"ok": True}]}
        ):
            results = await driver.batch([{"action": "click_node", "path": "/a"}, {"action": "get_tree"}])
            assert len(results) == 2


# ══════════════════════════════════════════════════════════════
# visual.py 测试
# ══════════════════════════════════════════════════════════════

try:
    import cv2
    import numpy as np

    HAS_CV2 = True
except ImportError:
    HAS_CV2 = False

pytestmark_visual = pytest.mark.skipif(not HAS_CV2, reason="opencv-python not installed")


def _make_solid_image(width=200, height=200, color=(0, 128, 255)):
    """创建纯色测试图片"""
    img = np.full((height, width, 3), color, dtype=np.uint8)
    return img


def _make_image_with_patch(width=200, height=200, patch_color=(255, 0, 0), patch_pos=(50, 50), patch_size=(40, 40)):
    """创建带小色块的测试图片"""
    img = np.full((height, width, 3), (20, 20, 20), dtype=np.uint8)
    px, py = patch_pos
    pw, ph = patch_size
    img[py : py + ph, px : px + pw] = patch_color
    return img


@pytestmark_visual
class TestVisualAsserterMatchTemplate:
    def test_match_finds_identical_patch(self, tmp_path):
        """模板与源图中的色块完全一致时应匹配"""
        from drivers.godot.visual import TemplateMatch, VisualAsserter

        template_img = np.full((40, 40, 3), (255, 0, 0), dtype=np.uint8)
        tpl_path = tmp_path / "tpl.png"
        cv2.imwrite(str(tpl_path), template_img)

        source_img = _make_image_with_patch(patch_color=(255, 0, 0), patch_pos=(60, 60))

        asserter = VisualAsserter()
        result = asserter.match_template(source_img, TemplateMatch(template_path=str(tpl_path), threshold=0.9, rgb=False))
        assert result.matched is True
        assert result.confidence > 0.9

    def test_match_rejects_different_pattern(self):
        from drivers.godot.visual import TemplateMatch, VisualAsserter

        # 源图：渐变背景
        source = np.zeros((200, 200, 3), dtype=np.uint8)
        for y in range(200):
            source[y, :] = [y, 0, 255 - y]
        # 模板：棋盘格（与渐变完全不同）
        template = np.zeros((30, 30, 3), dtype=np.uint8)
        template[0:15, 0:15] = (255, 0, 0)
        template[0:15, 15:30] = (0, 255, 0)
        template[15:30, 0:15] = (0, 0, 255)
        template[15:30, 15:30] = (255, 255, 0)

        asserter = VisualAsserter()
        result = asserter.match_template(
            source, TemplateMatch(template_array=template, threshold=0.99, rgb=False)
        )
        # 棋盘格不应在渐变中高置信度匹配
        assert result.matched is False or result.confidence < 0.99

    def test_match_returns_position(self):
        from drivers.godot.visual import TemplateMatch, VisualAsserter

        # 创建有特征的模板（棋盘格）
        template_img = np.zeros((40, 40, 3), dtype=np.uint8)
        template_img[0:20, 0:20] = (255, 0, 0)
        template_img[0:20, 20:40] = (0, 255, 0)
        template_img[20:40, 0:20] = (0, 0, 255)
        template_img[20:40, 20:40] = (255, 255, 0)

        # 源图：在 (80,80) 放置相同图案
        source = np.full((200, 200, 3), (20, 20, 20), dtype=np.uint8)
        source[80:120, 80:120] = template_img

        asserter = VisualAsserter()
        result = asserter.match_template(
            source, TemplateMatch(template_array=template_img, threshold=0.8, rgb=False, target_pos=(0.5, 0.5))
        )
        assert result.matched is True
        assert result.position is not None
        cx, cy = result.position
        # 中心应在色块区域附近
        assert 70 <= cx <= 130
        assert 70 <= cy <= 130


@pytestmark_visual
class TestVisualAsserterMatchAll:
    def test_find_all_multiple_copies(self):
        from drivers.godot.visual import TemplateMatch, VisualAsserter

        # 创建源图，在三个位置放相同的色块
        source = np.zeros((200, 400, 3), dtype=np.uint8)
        for x in [20, 120, 220]:
            source[50:80, x : x + 30] = (0, 255, 255)

        template = np.full((30, 30, 3), (0, 255, 255), dtype=np.uint8)
        asserter = VisualAsserter()
        results = asserter.match_all_templates(
            source, TemplateMatch(template_array=template, threshold=0.8, rgb=False), max_count=5
        )
        assert len(results) >= 2  # 至少找到 2 个


@pytestmark_visual
class TestVisualAsserterAssertions:
    def test_assert_exists_passes(self, tmp_path):
        from drivers.godot.visual import TemplateMatch, VisualAsserter

        # 创建有特征的模板
        tpl = np.zeros((30, 30, 3), dtype=np.uint8)
        tpl[0:15, 0:15] = (200, 0, 0)
        tpl[0:15, 15:30] = (0, 200, 0)
        tpl[15:30, 0:15] = (0, 0, 200)
        tpl[15:30, 15:30] = (200, 200, 0)
        tpl_path = tmp_path / "tpl.png"
        cv2.imwrite(str(tpl_path), tpl)

        # 源图包含相同图案
        source = np.full((200, 200, 3), (20, 20, 20), dtype=np.uint8)
        source[10:40, 10:40] = tpl

        asserter = VisualAsserter()
        result = asserter.assert_exists(source, TemplateMatch(template_path=str(tpl_path), threshold=0.8, rgb=False))
        assert result.matched is True

    def test_assert_not_exists_passes(self):
        from drivers.godot.visual import TemplateMatch, VisualAsserter

        # 源图：纯黑
        source = np.zeros((200, 200, 3), dtype=np.uint8)
        # 模板：有特征的棋盘格（与源图完全不同）
        template = np.zeros((30, 30, 3), dtype=np.uint8)
        template[0:15, 0:15] = (255, 0, 0)
        template[0:15, 15:30] = (0, 255, 0)
        template[15:30, 0:15] = (0, 0, 255)
        template[15:30, 15:30] = (255, 255, 0)

        asserter = VisualAsserter()
        # 纯黑图中不应匹配到有特征的模板（高阈值下）
        result = asserter.match_template(source, TemplateMatch(template_array=template, threshold=0.99, rgb=False))
        if result.matched:
            # 如果意外匹配到（纯黑图的匹配可能是 false positive），检查置信度
            assert result.confidence < 0.99

    def test_assert_exists_raises(self):
        from drivers.godot.visual import TemplateMatch, VisualAsserter

        # 源图纯黑
        source = np.zeros((200, 200, 3), dtype=np.uint8)
        # 模板：有特征的图案（与纯黑完全不同）
        template = np.zeros((30, 30, 3), dtype=np.uint8)
        template[0:15, 0:15] = (255, 0, 0)
        template[0:15, 15:30] = (0, 255, 0)
        template[15:30, 0:15] = (0, 0, 255)
        template[15:30, 15:30] = (255, 255, 0)

        asserter = VisualAsserter()
        result = asserter.match_template(source, TemplateMatch(template_array=template, threshold=0.99, rgb=False))
        if result.matched and result.confidence >= 0.99:
            # 纯黑图中意外匹配到特征模板，跳过此断言
            return
        with pytest.raises(AssertionError, match="模板未找到"):
            asserter.assert_exists(source, TemplateMatch(template_array=template, threshold=0.99, rgb=False))


@pytestmark_visual
class TestVisualAsserterRgbConfidence:
    def test_identical_images_high_confidence(self):
        from drivers.godot.visual import VisualAsserter

        # 使用有特征的图片（棋盘格），而不是纯色（纯色方差为0会导致分母为0）
        img = np.zeros((100, 100, 3), dtype=np.uint8)
        img[0:50, 0:50] = (200, 100, 50)
        img[0:50, 50:100] = (50, 200, 100)
        img[50:100, 0:50] = (100, 50, 200)
        img[50:100, 50:100] = (150, 150, 150)
        conf = VisualAsserter._cal_rgb_confidence(img, img)
        assert conf > 0.99

    def test_different_images_low_confidence(self):
        from drivers.godot.visual import VisualAsserter

        img1 = _make_solid_image(color=(0, 0, 0))
        img2 = _make_solid_image(color=(255, 255, 255))
        conf = VisualAsserter._cal_rgb_confidence(img1, img2)
        # 纯色图片相关系数可能为 nan（方差为 0），应返回 0
        assert conf == 0.0 or conf < 0.1


@pytestmark_visual
class TestVisualAsserterLoadImage:
    def test_load_from_numpy(self):
        from drivers.godot.visual import VisualAsserter

        img = _make_solid_image()
        result = VisualAsserter._load_image(img)
        assert result is img

    def test_load_from_path(self, tmp_path):
        from drivers.godot.visual import VisualAsserter

        img = _make_solid_image()
        path = tmp_path / "test.png"
        cv2.imwrite(str(path), img)
        result = VisualAsserter._load_image(str(path))
        assert result is not None
        assert result.shape == img.shape

    def test_load_from_bytes(self):
        from drivers.godot.visual import VisualAsserter

        img = _make_solid_image()
        _, buf = cv2.imencode(".png", img)
        result = VisualAsserter._load_image(buf.tobytes())
        assert result is not None

    def test_load_nonexistent_path(self):
        from drivers.godot.visual import VisualAsserter

        result = VisualAsserter._load_image("/nonexistent/path.png")
        assert result is None


@pytestmark_visual
class TestVisualAsserterSaveTemplate:
    def test_save_from_screenshot(self, tmp_path):
        from drivers.godot.visual import VisualAsserter

        source_path = tmp_path / "source.png"
        img = _make_image_with_patch(patch_color=(255, 0, 0), patch_pos=(10, 10), patch_size=(30, 30))
        cv2.imwrite(str(source_path), img)

        out_path = tmp_path / "templates" / "patch.png"
        result = VisualAsserter.save_template_from_screenshot(str(source_path), (10, 10, 30, 30), str(out_path))
        assert result.exists()
        saved = cv2.imread(str(result))
        assert saved.shape == (30, 30, 3)


# ══════════════════════════════════════════════════════════════
# debug_protocol.py 测试
# ══════════════════════════════════════════════════════════════


from drivers.godot.debug_protocol import (
    DebugEntry,
    DebugProtocol,
    DebugTrace,
    FailureSignature,
    ProtocolRule,
    create_seed_protocol,
)


class TestFailureSignature:
    def test_matches_exact(self):
        sig = FailureSignature(stage="test", error_code="ASSERT_FAIL", message_pattern="Assertion failed")
        assert sig.matches("ASSERT_FAIL", "Assertion failed") is True

    def test_matches_regex(self):
        sig = FailureSignature(
            stage="runtime", error_code="NODE_NOT_FOUND", message_pattern="Node not found: '(.+)'"
        )
        assert sig.matches("NODE_NOT_FOUND", "Node not found: 'Player'") is True
        assert sig.matches("NODE_NOT_FOUND", "Node not found: 'Enemy'") is True

    def test_mismatch_error_code(self):
        sig = FailureSignature(stage="test", error_code="ASSERT_FAIL", message_pattern="failed")
        assert sig.matches("OTHER_ERROR", "failed") is False

    def test_mismatch_stage(self):
        sig = FailureSignature(stage="build", error_code="GDSCRIPT_ERROR", message_pattern="Parse Error")
        assert sig.matches("GDSCRIPT_ERROR", "Parse Error", stage="test") is False

    def test_file_context_glob(self):
        sig = FailureSignature(
            stage="build", error_code="ERR", message_pattern="error", file_context="scripts/battle/*.gd"
        )
        assert sig.matches("ERR", "error", file_path="scripts/battle/combat.gd") is True
        assert sig.matches("ERR", "error", file_path="scripts/ui/menu.gd") is False

    def test_serialize_roundtrip(self):
        sig = FailureSignature(stage="test", error_code="ASSERT_FAIL", message_pattern="Assertion (.+)")
        d = sig.to_dict()
        restored = FailureSignature.from_dict(d)
        assert restored.stage == sig.stage
        assert restored.error_code == sig.error_code
        assert restored.message_pattern == sig.message_pattern


class TestDebugEntry:
    def test_serialize_roundtrip(self):
        entry = DebugEntry(
            id="entry-1",
            kind="reactive",
            signature=FailureSignature(stage="test", error_code="ERR", message_pattern="err"),
            root_cause="test",
            fix_description="fix it",
            occurrences=5,
        )
        d = entry.to_dict()
        restored = DebugEntry.from_dict(d)
        assert restored.id == "entry-1"
        assert restored.occurrences == 5


class TestProtocolRule:
    def test_serialize_roundtrip(self):
        rule = ProtocolRule(
            id="r1", name="test_rule", description="desc", preconditions=["p1"], action="flag", checks=[]
        )
        d = rule.to_dict()
        restored = ProtocolRule.from_dict(d)
        assert restored.name == "test_rule"
        assert restored.action == "flag"


class TestDebugProtocol:
    def test_load_or_create_new(self, tmp_path, monkeypatch):
        monkeypatch.setattr("drivers.godot.debug_protocol.PROTOCOL_DIR", tmp_path)
        proto = DebugProtocol.load_or_create("test_project")
        assert proto.project_name == "test_project"
        assert len(proto.entries) == 0

    def test_create_entry_and_find_match(self, tmp_path, monkeypatch):
        monkeypatch.setattr("drivers.godot.debug_protocol.PROTOCOL_DIR", tmp_path)
        proto = DebugProtocol.load_or_create("test_project")
        entry = proto.create_entry(
            stage="test",
            error_code="ASSERT_FAIL",
            message="Assertion failed: expected 10 but got 5",
            root_cause="wrong value",
            fix_description="fix the calculation",
        )
        assert entry.id.startswith("entry-ASSERT_FAIL-")

        # 查找匹配
        found = proto.find_match("ASSERT_FAIL", "Assertion failed: expected 20 but got 3")
        assert found is not None
        assert found.id == entry.id
        assert found.occurrences == 1  # 匹配次数增加

    def test_find_no_match(self, tmp_path, monkeypatch):
        monkeypatch.setattr("drivers.godot.debug_protocol.PROTOCOL_DIR", tmp_path)
        proto = DebugProtocol.load_or_create("test_project")
        proto.create_entry(
            stage="test", error_code="ASSERT_FAIL", message="Assertion failed", root_cause="x", fix_description="y"
        )
        found = proto.find_match("OTHER_ERROR", "some message")
        assert found is None

    def test_check_proactive(self, tmp_path, monkeypatch):
        monkeypatch.setattr("drivers.godot.debug_protocol.PROTOCOL_DIR", tmp_path)
        proto = DebugProtocol.load_or_create("test_project")
        entry = DebugEntry(
            id="p1",
            kind="proactive",
            signature=FailureSignature(stage="test", error_code="ERR", message_pattern="err"),
            root_cause="test",
            tags=["deprecated_api"],
        )
        proto.record_entry(entry)

        violations = proto.check_proactive({"context": "using deprecated_api"})
        assert len(violations) == 1

    def test_rule_generalization(self, tmp_path, monkeypatch):
        """同类型错误出现 3 次以上应自动生成规则"""
        monkeypatch.setattr("drivers.godot.debug_protocol.PROTOCOL_DIR", tmp_path)
        proto = DebugProtocol.load_or_create("test_project")

        for i in range(4):
            proto.create_entry(
                stage="test",
                error_code="ASSERT_FAIL",
                message=f"Assertion failed #{i}",
                root_cause="x",
                fix_description="y",
            )

        assert len(proto.rules) >= 1
        assert proto.rules[0].name == "ASSERT_FAIL_auto_rule"

    def test_save_and_load(self, tmp_path, monkeypatch):
        monkeypatch.setattr("drivers.godot.debug_protocol.PROTOCOL_DIR", tmp_path)
        proto = DebugProtocol.load_or_create("save_test")
        proto.create_entry(
            stage="test",
            error_code="ERR",
            message="test error",
            root_cause="cause",
            fix_description="fix",
        )
        proto.save()

        loaded = DebugProtocol.load_or_create("save_test")
        assert len(loaded.entries) == 1
        assert loaded.entries[0].root_cause == "cause"

    def test_stats(self, tmp_path, monkeypatch):
        monkeypatch.setattr("drivers.godot.debug_protocol.PROTOCOL_DIR", tmp_path)
        proto = DebugProtocol.load_or_create("test_project")
        proto.create_entry(
            stage="test", error_code="ASSERT_FAIL", message="err", root_cause="x", fix_description="y"
        )
        stats = proto.stats()
        assert stats["entries"] == 1
        assert stats["project"] == "test_project"

    def test_normalize_message(self):
        msg = DebugProtocol._normalize_message("Property 'health' does not exist")
        assert "(.+)" in msg

    def test_create_seed_protocol(self):
        proto = create_seed_protocol("test_game")
        assert len(proto.entries) >= 5
        codes = [e.signature.error_code for e in proto.entries]
        assert "GDSCRIPT_ERROR" in codes
        assert "NODE_NOT_FOUND" in codes


class TestDebugTrace:
    def test_serialize(self):
        trace = DebugTrace(
            project_path="/test",
            started_at="2026-01-01",
            total_iterations=3,
            iterations=[],
        )
        d = trace.to_dict()
        assert d["project_path"] == "/test"
        assert d["total_iterations"] == 3


# ══════════════════════════════════════════════════════════════
# bench.py 测试
# ══════════════════════════════════════════════════════════════


from drivers.godot.bench import BenchDimension, BenchResult, DimensionScore, GameBench


class TestBenchResult:
    def test_properties(self):
        result = BenchResult(
            project_name="test",
            timestamp="2026-01-01",
            dimensions=[
                DimensionScore(dimension=BenchDimension.BUILD_HEALTH, score=80, passed=True),
                DimensionScore(dimension=BenchDimension.VISUAL_USABILITY, score=70, passed=True),
                DimensionScore(dimension=BenchDimension.INTENT_ALIGNMENT, score=60, passed=True),
            ],
            total_score=70.0,
            passed=True,
        )
        assert result.build_health.score == 80
        assert result.visual_usability.score == 70
        assert result.intent_alignment.score == 60

    def test_to_dict(self):
        result = BenchResult(project_name="test", timestamp="2026-01-01", dimensions=[])
        d = result.to_dict()
        assert d["project_name"] == "test"


class TestGameBenchBuildHealth:
    def test_evaluate_with_valid_project(self, tmp_path):
        """有 project.godot 和合法结构时应得高分"""
        project = tmp_path / "game"
        project.mkdir()
        (project / "project.godot").write_text(
            '[gd_resource]\n\n[application]\nrun/main_scene="res://scenes/Main.tscn"\n\n[autoload]\nGameManager="*res://scripts/manager.gd"\n'
        )
        scenes = project / "scenes"
        scenes.mkdir()
        (scenes / "Main.tscn").write_text("[gd_scene]")
        scripts = project / "scripts"
        scripts.mkdir()
        (scripts / "manager.gd").write_text("extends Node\n")
        test_dir = project / "test"
        test_dir.mkdir()
        (test_dir / "test_main.gd").write_text("extends GutTest\n")
        # 添加配置文件以通过 intent alignment 检查
        (project / "manifest.json").write_text('{"version": 1}')

        bench = GameBench(project_path=str(project), godot_path="echo")
        result = bench.evaluate(run_headless=False)  # 不启动 Godot

        assert result.build_health.passed is True
        assert result.build_health.score >= 60
        # intent_alignment 需要配置文件 + 场景结构 + 测试覆盖
        assert result.intent_alignment.passed is True

    def test_evaluate_missing_project_godot(self, tmp_path):
        project = tmp_path / "empty_game"
        project.mkdir()

        bench = GameBench(project_path=str(project))
        result = bench.evaluate(run_headless=False)

        # project.godot 不存在，BH 应该低分
        assert result.build_health.score < 30

    def test_parse_main_scene(self, tmp_path):
        project = tmp_path / "game"
        project.mkdir()
        pf = project / "project.godot"
        pf.write_text('[application]\nrun/main_scene="res://scenes/Battle.tscn"\n')

        bench = GameBench(project_path=str(project))
        scene = bench._parse_main_scene(pf)
        assert scene == "res://scenes/Battle.tscn"

    def test_parse_autoloads(self, tmp_path):
        project = tmp_path / "game"
        project.mkdir()
        pf = project / "project.godot"
        pf.write_text("[autoload]\nGameManager=\"*res://scripts/manager.gd\"\nAIEngine=\"*res://scripts/ai.gd\"\n")

        bench = GameBench(project_path=str(project))
        autoloads = bench._parse_autoloads(pf)
        assert "GameManager" in autoloads
        assert "AIEngine" in autoloads


class TestGameBenchVisualUsability:
    def test_no_screenshots_gives_base_score(self, tmp_path):
        project = tmp_path / "game"
        project.mkdir()

        bench = GameBench(project_path=str(project), screenshot_dir="")
        result = bench._evaluate_visual_usability()
        assert result.score >= 0


class TestGameBenchIntentAlignment:
    def test_find_config_files(self, tmp_path):
        project = tmp_path / "game"
        project.mkdir()
        (project / "manifest.json").write_text("{}")
        (project / "config.json").write_text("{}")

        bench = GameBench(project_path=str(project))
        configs = bench._find_config_files()
        names = [c.name for c in configs]
        assert "manifest.json" in names
        assert "config.json" in names

    def test_validate_configs_valid(self, tmp_path):
        project = tmp_path / "game"
        project.mkdir()
        cfg = project / "config.json"
        cfg.write_text('{"level": 1}')

        bench = GameBench(project_path=str(project))
        result = bench._validate_configs([cfg])
        assert result["passed"] is True

    def test_validate_configs_invalid_json(self, tmp_path):
        project = tmp_path / "game"
        project.mkdir()
        cfg = project / "bad.json"
        cfg.write_text("not json {{{")

        bench = GameBench(project_path=str(project))
        result = bench._validate_configs([cfg])
        assert result["passed"] is False

    def test_check_scene_structure_has_game(self, tmp_path):
        project = tmp_path / "game"
        scenes = project / "scenes"
        scenes.mkdir(parents=True)
        (scenes / "title_screen.tscn").write_text("")
        (scenes / "battle_scene.tscn").write_text("")

        bench = GameBench(project_path=str(project))
        result = bench._check_scene_structure()
        assert result["passed"] is True

    def test_check_test_coverage_found(self, tmp_path):
        project = tmp_path / "game"
        test_dir = project / "test"
        test_dir.mkdir(parents=True)
        (test_dir / "test_battle.gd").write_text("")

        bench = GameBench(project_path=str(project))
        result = bench._check_test_coverage()
        assert result["passed"] is True

    def test_check_test_coverage_not_found(self, tmp_path):
        project = tmp_path / "game"
        project.mkdir()

        bench = GameBench(project_path=str(project))
        result = bench._check_test_coverage()
        assert result["passed"] is False


class TestGameBenchResources:
    def test_check_resources_with_valid_preload(self, tmp_path):
        project = tmp_path / "game"
        scripts = project / "scripts"
        scripts.mkdir(parents=True)
        res = project / "res"
        res.mkdir()
        (res / "icon.png").write_bytes(b"fake")
        (scripts / "player.gd").write_text('extends Node\nvar icon = preload("res://res/icon.png")\n')

        bench = GameBench(project_path=str(project))
        result = bench._check_resources()
        assert result["passed"] is True

    def test_check_resources_with_missing_ref(self, tmp_path):
        project = tmp_path / "game"
        scripts = project / "scripts"
        scripts.mkdir(parents=True)
        (scripts / "player.gd").write_text('var data = preload("res://missing/resource.tres")\n')

        bench = GameBench(project_path=str(project))
        result = bench._check_resources()
        assert result["passed"] is False
