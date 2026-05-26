"""游戏操作录制器 — 录制 Godot 游戏的操作序列为 JSON

录制格式可读，方便人工检查和调试。
支持从 EventBridge 自动截获操作，也可手动调用。

录制 JSON 格式:
{
    "session_id": "rec-xxx",
    "started_at": 1716700000000,
    "ended_at": 1716700060000,
    "metadata": {"project": "my_game", "scene": "res://main.tscn"},
    "actions": [
        {
            "timestamp": 1716700001000,
            "type": "click_node",
            "params": {"path": "UI/Button"},
            "screenshot": "rec-xxx_0001.png",
            "index": 0
        }
    ]
}

用法:
    recorder = GameRecorder(driver, output_dir="recordings")

    # 方式 1: 手动录制
    await recorder.start_recording("session-1", metadata={"project": "game"})
    await recorder.record_action("click_node", {"path": "UI/Button"})
    await recorder.record_action("input_key", {"keycode": 4194305})
    result = await recorder.stop_recording()

    # 方式 2: 从 EventBridge 自动截获
    bridge = EventBridge(driver, ...)
    recorder = GameRecorder.from_bridge(bridge, driver)
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional


@dataclass
class RecordedAction:
    """单个录制的操作"""

    timestamp: int  # 毫秒时间戳
    type: str  # 操作类型 (click_node, input_key, etc.)
    params: Dict[str, Any]  # 操作参数
    screenshot: Optional[str] = None  # 截图文件名
    index: int = 0  # 序号

    def to_dict(self) -> Dict[str, Any]:
        """转换为可序列化字典"""
        d = {
            "timestamp": self.timestamp,
            "type": self.type,
            "params": self.params,
            "index": self.index,
        }
        if self.screenshot:
            d["screenshot"] = self.screenshot
        return d


@dataclass
class RecordingResult:
    """录制结果"""

    session_id: str
    json_path: Path  # 录制文件路径
    action_count: int
    duration_ms: int  # 录制时长
    screenshots: List[str]  # 截图文件名列表
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def duration_s(self) -> float:
        return self.duration_ms / 1000.0


class GameRecorder:
    """游戏操作录制器

    录制 GodotDriver 的操作序列为 JSON 文件。
    可选截获 EventBridge 的操作自动录制。

    参数:
        driver: GodotDriver 实例（用于截图）
        output_dir: 录制输出目录
        auto_screenshot: 每个操作是否自动截图
        screenshot_prefix: 截图文件名前缀
    """

    def __init__(
        self,
        driver: Any,  # GodotDriver
        output_dir: str = "recordings",
        auto_screenshot: bool = False,
        screenshot_prefix: str = "",
    ):
        self._driver = driver
        self._output_dir = Path(output_dir)
        self._auto_screenshot = auto_screenshot
        self._screenshot_prefix = screenshot_prefix

        # 录制状态
        self._session_id: Optional[str] = None
        self._actions: List[RecordedAction] = []
        self._metadata: Dict[str, Any] = {}
        self._started_at: Optional[int] = None
        self._is_recording: bool = False

        # EventBridge 截获
        self._bridge_hook: Optional[Callable] = None

    @classmethod
    def from_bridge(
        cls,
        bridge: Any,  # EventBridge
        driver: Any,  # GodotDriver
        output_dir: str = "recordings",
        auto_screenshot: bool = False,
    ) -> "GameRecorder":
        """从 EventBridge 创建自动截获录制器

        会 hook EventBridge 的 _enqueue 方法，自动截获所有操作事件。
        """
        recorder = cls(driver, output_dir, auto_screenshot)
        recorder._setup_bridge_hook(bridge)
        return recorder

    def _setup_bridge_hook(self, bridge: Any) -> None:
        """设置 EventBridge 截获钩子"""
        original_enqueue = bridge._enqueue

        async def hooked_enqueue(event: Any) -> None:
            """拦截 EventBridge 事件，自动录制"""
            if self._is_recording and event.type.startswith("action."):
                # 从事件中提取操作信息
                action_type = event.type.replace("action.", "")
                params = event.data.copy()
                # 移除内部字段
                params.pop("timestamp", None)
                await self.record_action(action_type, params)
            # 调用原始方法
            await original_enqueue(event)

        bridge._enqueue = hooked_enqueue
        self._bridge_hook = hooked_enqueue

    @property
    def is_recording(self) -> bool:
        return self._is_recording

    @property
    def action_count(self) -> int:
        return len(self._actions)

    async def start_recording(
        self,
        session_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> str:
        """开始录制操作序列

        Args:
            session_id: 会话 ID（默认自动生成）
            metadata: 附加元数据（如项目名、场景路径等）

        Returns:
            session_id
        """
        if self._is_recording:
            raise RuntimeError("录制已在进行中，请先 stop_recording()")

        self._session_id = session_id or f"rec-{int(time.time() * 1000)}"
        self._metadata = metadata or {}
        self._started_at = int(time.time() * 1000)
        self._actions = []
        self._is_recording = True

        # 尝试获取当前场景信息
        try:
            scene = await self._driver.get_scene()
            if scene:
                self._metadata["start_scene"] = scene
        except Exception:
            pass  # driver 可能未连接

        print(f"[GameRecorder] 开始录制: {self._session_id}")
        return self._session_id

    async def record_action(
        self,
        action_type: str,
        params: Optional[Dict[str, Any]] = None,
        screenshot_path: Optional[str] = None,
    ) -> RecordedAction:
        """记录单个操作

        Args:
            action_type: 操作类型（click_node, input_key, input_action, etc.）
            params: 操作参数
            screenshot_path: 可选截图路径

        Returns:
            RecordedAction 对象
        """
        if not self._is_recording:
            raise RuntimeError("未在录制中，请先 start_recording()")

        timestamp = int(time.time() * 1000)
        index = len(self._actions)

        # 自动截图
        screenshot_name = screenshot_path
        if self._auto_screenshot and screenshot_name is None:
            try:
                prefix = self._screenshot_prefix or self._session_id
                filename = f"{prefix}_{index:04d}.png"
                filepath = await self._driver.screenshot(filename)
                screenshot_name = filename
            except Exception:
                screenshot_name = None  # 截图失败不阻塞录制

        action = RecordedAction(
            timestamp=timestamp,
            type=action_type,
            params=params or {},
            screenshot=screenshot_name,
            index=index,
        )
        self._actions.append(action)

        print(f"[GameRecorder] 记录操作 #{index}: {action_type}")
        return action

    async def stop_recording(self) -> RecordingResult:
        """停止录制并保存为 JSON 文件

        Returns:
            RecordingResult 包含录制元信息和文件路径
        """
        if not self._is_recording:
            raise RuntimeError("未在录制中")

        self._is_recording = False
        ended_at = int(time.time() * 1000)

        # 构建录制 JSON
        recording = {
            "session_id": self._session_id,
            "started_at": self._started_at,
            "ended_at": ended_at,
            "metadata": self._metadata,
            "actions": [action.to_dict() for action in self._actions],
        }

        # 保存文件
        self._output_dir.mkdir(parents=True, exist_ok=True)
        json_path = self._output_dir / f"{self._session_id}.json"
        json_path.write_text(json.dumps(recording, indent=2, ensure_ascii=False), encoding="utf-8")

        # 收集截图列表
        screenshots = [a.screenshot for a in self._actions if a.screenshot]

        result = RecordingResult(
            session_id=self._session_id,
            json_path=json_path,
            action_count=len(self._actions),
            duration_ms=ended_at - (self._started_at or ended_at),
            screenshots=screenshots,
            metadata=self._metadata,
        )

        print(f"[GameRecorder] 录制完成: {result.action_count} 个操作, 耗时 {result.duration_s:.1f}s")
        print(f"[GameRecorder] 保存到: {json_path}")

        # 重置状态
        self._session_id = None
        self._actions = []
        self._metadata = {}
        self._started_at = None

        return result

    def get_actions(self) -> List[RecordedAction]:
        """获取当前录制中的操作列表（用于调试）"""
        return list(self._actions)
