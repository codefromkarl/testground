"""Godot 游戏自动化测试驱动

兼容两个项目的 AutomationServer:
- pogongshichongzou: 基础 TCP JSONL 服务器 (PGC_AUTOMATION=1)
- loopexpedition: godot_e2e 插件 (GODOT_E2E=1)

参考:
- Airtest: 图像识别定位 (本模块提供截图能力)
- OpenGame: Agent 工具抽象 (本模块提供结构化命令)
"""

from .godot.bench import BenchDimension, BenchResult, GameBench
from .godot.debug_protocol import (
    DebugEntry,
    DebugIteration,
    DebugProtocol,
    DebugTrace,
    FailureSignature,
    ProtocolRule,
    protocol_dir,
)
from .godot.driver import DriverConfig, GodotDriver
from .godot.visual import TemplateMatch, VisualAsserter, VisualMatchResult

__all__ = [
    "GodotDriver",
    "DriverConfig",
    "VisualAsserter",
    "TemplateMatch",
    "VisualMatchResult",
    "DebugProtocol",
    "DebugEntry",
    "FailureSignature",
    "ProtocolRule",
    "DebugTrace",
    "DebugIteration",
    "protocol_dir",
    "GameBench",
    "BenchResult",
    "BenchDimension",
]
