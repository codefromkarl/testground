"""Godot TCP 驱动 — Python 端连接 Godot AutomationServer

兼容:
  1. pogongshichongzou 的基础 AutomationServer (JSONL 事件流)
  2. loopexpedition 的 godot_e2e 插件 (JSON-RPC 命令协议)

用法:
    async with GodotDriver("127.0.0.1", 19090) as d:
        tree = await d.get_tree()
        await d.click_node("MainPanel/Button")
        await d.screenshot("test.png")
        await d.wait_for_scene("res://scenes/battle/BattleScene.tscn")
"""

from __future__ import annotations

import asyncio
import base64
import json
import struct
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


@dataclass
class DriverConfig:
    """驱动配置"""

    host: str = "127.0.0.1"
    port: int = 19090
    timeout: float = 10.0
    # 自动检测项目类型: "pogongshichongzou" | "loopexpedition" | "auto"
    project_type: str = "auto"
    # 截图保存目录
    screenshot_dir: str = "test_screenshots"
    # 连接重试
    max_retries: int = 3
    retry_interval: float = 1.0


@dataclass
class NodeInfo:
    """场景树节点信息"""

    path: str
    type: str
    name: str
    children_count: int = 0
    metadata: Dict[str, Any] = field(default_factory=dict)


class GodotDriver:
    """异步 TCP 驱动，连接 Godot AutomationServer"""

    def __init__(self, host: str = "127.0.0.1", port: int = 19090, config: Optional[DriverConfig] = None):
        self._config = config or DriverConfig(host=host, port=port)
        self._config.host = host
        self._config.port = port
        self._reader: Optional[asyncio.StreamReader] = None
        self._writer: Optional[asyncio.StreamWriter] = None
        self._project_type: Optional[str] = None
        self._command_id = 0

    async def __aenter__(self) -> "GodotDriver":
        await self.connect()
        return self

    async def __aexit__(self, *args) -> None:
        await self.close()

    # ─── 连接管理 ────────────────────────────────────────

    async def connect(self) -> None:
        """连接到 Godot AutomationServer"""
        for attempt in range(self._config.max_retries):
            try:
                self._reader, self._writer = await asyncio.wait_for(
                    asyncio.open_connection(self._config.host, self._config.port),
                    timeout=self._config.timeout,
                )
                # 检测项目类型
                if self._config.project_type == "auto":
                    await self._detect_project_type()
                else:
                    self._project_type = self._config.project_type
                print(
                    f"[GodotDriver] 连接成功 ({self._config.host}:{self._config.port}) 项目类型: {self._project_type}"
                )
                return
            except (ConnectionRefusedError, asyncio.TimeoutError) as e:
                if attempt < self._config.max_retries - 1:
                    await asyncio.sleep(self._config.retry_interval)
                else:
                    raise ConnectionError(
                        f"无法连接 Godot AutomationServer "
                        f"{self._config.host}:{self._config.port} ({self._config.max_retries} 次重试)"
                    ) from e

    async def close(self) -> None:
        """关闭连接"""
        if self._writer:
            self._writer.close()
            try:
                await self._writer.wait_closed()
            except Exception:
                pass
            self._writer = None
            self._reader = None

    async def _detect_project_type(self) -> None:
        """通过发送探测命令检测项目类型"""
        # godot_e2e (loopexpedition) 支持 "hello" 握手
        try:
            result = await self._send_command_e2e("get_scene", {})
            if result is not None:
                self._project_type = "loopexpedition"
                return
        except Exception:
            pass

        # 默认为 pogongshichongzou 风格 (JSONL 事件流)
        self._project_type = "pogongshichongzou"

    # ─── 协议层 ─────────────────────────────────────────

    async def _send_command_e2e(self, action: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """发送 godot_e2e 格式的 JSON 命令 (loopexpedition)

        协议: 4 字节 little-endian 长度头 + JSON body
        """
        if not self._writer or not self._reader:
            raise RuntimeError("未连接")

        self._command_id += 1
        cmd = {
            "id": self._command_id,
            "action": action,
            **params,
        }
        payload = json.dumps(cmd).encode("utf-8")
        header = struct.pack("<I", len(payload))
        self._writer.write(header + payload)
        await self._writer.drain()

        # 读取响应
        resp_header = await asyncio.wait_for(self._reader.readexactly(4), timeout=self._config.timeout)
        resp_len = struct.unpack("<I", resp_header)[0]
        resp_data = await asyncio.wait_for(self._reader.readexactly(resp_len), timeout=self._config.timeout)
        return json.loads(resp_data)

    async def _send_command_pgc(self, command: str, params: Dict[str, Any]) -> None:
        """发送 PGC AutomationServer 格式的 JSONL 命令 (pogongshichongzou)

        协议: 换行分隔的 JSON
        """
        if not self._writer:
            raise RuntimeError("未连接")

        cmd = {"command": command, **params}
        line = json.dumps(cmd) + "\n"
        self._writer.write(line.encode("utf-8"))
        await self._writer.drain()

    async def _send(self, action: str, params: Dict[str, Any] = None) -> Any:
        """根据项目类型自动选择协议"""
        params = params or {}
        if self._project_type == "loopexpedition":
            return await self._send_command_e2e(action, params)
        else:
            await self._send_command_pgc(action, params)
            return None

    # ─── 节点查询 ────────────────────────────────────────

    async def node_exists(self, path: str) -> bool:
        """检查节点是否存在"""
        if self._project_type == "loopexpedition":
            result = await self._send_command_e2e("node_exists", {"path": path})
            return result.get("exists", False)
        await self._send_command_pgc("node_exists", {"path": path})
        return True  # PGC 无返回值，假设成功

    async def get_property(self, path: str, property_name: str) -> Any:
        """获取节点属性"""
        if self._project_type == "loopexpedition":
            result = await self._send_command_e2e(
                "get_property",
                {
                    "path": path,
                    "property": property_name,
                },
            )
            return result.get("value")
        await self._send_command_pgc(
            "get_property",
            {
                "path": path,
                "property": property_name,
            },
        )
        return None

    async def set_property(self, path: str, property_name: str, value: Any) -> None:
        """设置节点属性"""
        await self._send(
            "set_property",
            {
                "path": path,
                "property": property_name,
                "value": value,
            },
        )

    async def call_method(self, path: str, method: str, args: List[Any] = None) -> Any:
        """调用节点方法"""
        if self._project_type == "loopexpedition":
            result = await self._send_command_e2e(
                "call_method",
                {
                    "path": path,
                    "method": method,
                    "args": args or [],
                },
            )
            return result.get("return_value")
        await self._send_command_pgc(
            "call_method",
            {
                "path": path,
                "method": method,
                "args": args or [],
            },
        )
        return None

    async def get_tree(self) -> Dict[str, Any]:
        """获取场景树"""
        if self._project_type == "loopexpedition":
            return await self._send_command_e2e("get_tree", {})
        await self._send_command_pgc("get_tree", {})
        return {}

    async def find_by_meta(self, role: str) -> List[str]:
        """通过 e2e_role meta 查找节点 (loopexpedition 风格)"""
        if self._project_type == "loopexpedition":
            result = await self._send_command_e2e("find_by_meta", {"role": role})
            return result.get("paths", [])
        await self._send_command_pgc("find_by_meta", {"role": role})
        return []

    async def query_nodes(self, query: str) -> List[Dict[str, Any]]:
        """查询节点"""
        if self._project_type == "loopexpedition":
            result = await self._send_command_e2e("query_nodes", {"query": query})
            return result.get("nodes", [])
        return []

    # ─── 输入模拟 ────────────────────────────────────────

    async def click_node(self, path: str) -> None:
        """点击节点"""
        await self._send("click_node", {"path": path})

    async def input_key(self, keycode: int, pressed: bool = True) -> None:
        """模拟按键"""
        await self._send("input_key", {"keycode": keycode, "pressed": pressed})

    async def input_action(self, action: str, strength: float = 1.0) -> None:
        """触发输入动作"""
        await self._send("input_action", {"action": action, "strength": strength})

    async def input_mouse_button(self, position: Tuple[int, int], button_index: int = 1, pressed: bool = True) -> None:
        """模拟鼠标点击"""
        await self._send(
            "input_mouse_button",
            {
                "position": list(position),
                "button_index": button_index,
                "pressed": pressed,
            },
        )

    async def input_mouse_motion(self, position: Tuple[int, int], relative: Tuple[int, int] = (0, 0)) -> None:
        """模拟鼠标移动"""
        await self._send(
            "input_mouse_motion",
            {
                "position": list(position),
                "relative": list(relative),
            },
        )

    # ─── 场景控制 ────────────────────────────────────────

    async def get_scene(self) -> str:
        """获取当前场景路径"""
        if self._project_type == "loopexpedition":
            result = await self._send_command_e2e("get_scene", {})
            return result.get("scene_path", "")
        return ""

    async def change_scene(self, scene_path: str) -> None:
        """切换场景"""
        await self._send("change_scene", {"scene_path": scene_path})

    async def reload_scene(self) -> None:
        """重载当前场景"""
        await self._send("reload_scene", {})

    # ─── 等待机制 ────────────────────────────────────────

    async def wait_process_frames(self, count: int) -> None:
        """等待指定帧数"""
        await self._send("wait_process_frames", {"count": count})

    async def wait_seconds(self, seconds: float) -> None:
        """等待指定秒数"""
        await self._send("wait_seconds", {"seconds": seconds})

    async def wait_for_node(self, path: str, timeout: float = 10.0) -> bool:
        """等待节点出现"""
        if self._project_type == "loopexpedition":
            result = await self._send_command_e2e(
                "wait_for_node",
                {
                    "path": path,
                    "timeout_ms": int(timeout * 1000),
                },
            )
            return result.get("found", False)
        # Fallback: 轮询
        deadline = time.time() + timeout
        while time.time() < deadline:
            if await self.node_exists(path):
                return True
            await asyncio.sleep(0.1)
        return False

    async def wait_for_signal(self, node_path: str, signal_name: str, timeout: float = 10.0) -> bool:
        """等待信号触发"""
        if self._project_type == "loopexpedition":
            result = await self._send_command_e2e(
                "wait_for_signal",
                {
                    "node_path": node_path,
                    "signal_name": signal_name,
                    "timeout_ms": int(timeout * 1000),
                },
            )
            return result.get("emitted", False)
        await asyncio.sleep(min(timeout, 1.0))
        return True

    async def wait_for_scene(self, scene_path: str, timeout: float = 10.0) -> bool:
        """等待场景加载 (借鉴 Airtest 的 loop_find 等待模式)"""
        deadline = time.time() + timeout
        while time.time() < deadline:
            current = await self.get_scene()
            if current == scene_path:
                return True
            await asyncio.sleep(0.2)
        return False

    async def wait_for_property(
        self, node_path: str, property_name: str, expected_value: Any, timeout: float = 10.0
    ) -> bool:
        """等待属性达到期望值"""
        if self._project_type == "loopexpedition":
            result = await self._send_command_e2e(
                "wait_for_property",
                {
                    "node_path": node_path,
                    "property": property_name,
                    "value": expected_value,
                    "timeout_ms": int(timeout * 1000),
                },
            )
            return result.get("matched", False)
        deadline = time.time() + timeout
        while time.time() < deadline:
            val = await self.get_property(node_path, property_name)
            if val == expected_value:
                return True
            await asyncio.sleep(0.2)
        return False

    # ─── 截图与观察 ─────────────────────────────────────

    async def screenshot(self, filename: Optional[str] = None) -> Path:
        """截图并保存到文件"""
        screenshot_dir = Path(self._config.screenshot_dir)
        screenshot_dir.mkdir(parents=True, exist_ok=True)

        if filename is None:
            filename = f"screenshot_{int(time.time() * 1000)}.png"

        filepath = screenshot_dir / filename

        if self._project_type == "loopexpedition":
            # godot_e2e 支持 screenshot 命令
            result = await self._send_command_e2e(
                "screenshot",
                {
                    "path": str(filepath),
                },
            )
            if result.get("success"):
                return filepath
            # 尝试 base64 方式
            result = await self._send_command_e2e("screenshot_base64", {})
            b64_data = result.get("data", "")
            if b64_data:
                filepath.write_bytes(base64.b64decode(b64_data))
                return filepath

        # Fallback: 通知 PGC 服务器截图
        await self._send_command_pgc("screenshot", {"path": str(filepath)})
        return filepath

    async def screenshot_base64(self) -> str:
        """截图返回 base64 数据"""
        if self._project_type == "loopexpedition":
            result = await self._send_command_e2e("screenshot_base64", {})
            return result.get("data", "")
        return ""

    async def observe(self) -> Dict[str, Any]:
        """获取当前游戏状态快照"""
        if self._project_type == "loopexpedition":
            return await self._send_command_e2e("observe", {})
        return {}

    async def observe_logs(self, query: str = "", limit: int = 50) -> List[Dict[str, Any]]:
        """查询运行时日志"""
        if self._project_type == "loopexpedition":
            result = await self._send_command_e2e(
                "observe_logs",
                {
                    "query": query,
                    "limit": limit,
                },
            )
            return result.get("logs", [])
        return []

    # ─── 批量执行 ───────────────────────────────────────

    async def batch(self, commands: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """批量执行命令"""
        if self._project_type == "loopexpedition":
            result = await self._send_command_e2e("batch", {"commands": commands})
            return result.get("results", [])
        results = []
        for cmd in commands:
            action = cmd.pop("action", "")
            result = await self._send(action, cmd)
            results.append(result or {})
        return results

    # ─── 可观测性 (Observability) ───────────────────────

    async def start_state_diff(self) -> None:
        """启动状态差异记录"""
        await self._send("start_state_diff", {})

    async def stop_state_diff(self) -> Dict[str, Any]:
        """停止并获取状态差异"""
        if self._project_type == "loopexpedition":
            return await self._send_command_e2e("stop_state_diff", {})
        return {}

    async def start_input_trace(self) -> None:
        """启动输入追踪"""
        await self._send("start_input_trace", {})

    async def stop_input_trace(self) -> Dict[str, Any]:
        """停止并获取输入追踪"""
        if self._project_type == "loopexpedition":
            return await self._send_command_e2e("stop_input_trace", {})
        return {}

    async def trigger_cause_test(self, label: str) -> Dict[str, Any]:
        """触发因果标注测试"""
        if self._project_type == "loopexpedition":
            return await self._send_command_e2e("trigger_cause_test", {"label": label})
        return {}
