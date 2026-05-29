"""测试事件工厂函数 — 统一的测试数据创建工具

提供与现有测试兼容的工厂函数，用于替代各测试文件中重复的
`make_event()`, `_evt()`, `_make_event()` 等函数。

使用示例:
    from tests.factories import make_event, make_events_batch

    # 创建单个事件
    event = make_event("test.start", test_name="test_login")

    # 创建一批测试事件
    events = make_events_batch(passed=5, failed=1, flaky=1)

与现有测试文件的兼容性:
    - test_pipeline.py: make_event(), make_events_batch() — 完全兼容
    - test_architecture_validation.py: _evt(), _batch() — 通过 make_event(data=...) 兼容
    - test_godot_agents.py: _make_event() — 通过 make_event(data=...) 兼容
    - test_llm_pipeline.py: _evt() — 完全兼容
"""

from __future__ import annotations

import time
import uuid
from typing import Any, Dict, List, Optional


def make_event(
    event_type: str,
    test_name: str = "",
    session_id: str = "sess-1",
    project: str = "test-proj",
    framework: str = "vitest",
    duration_ms: float = 0,
    data: Optional[Dict[str, Any]] = None,
    **extra: Any,
) -> dict:
    """创建测试事件字典

    Args:
        event_type: 事件类型，如 "test.start", "test.end", "assert.pass" 等
        test_name: 测试名称（可选）
        session_id: 会话 ID
        project: 项目名称
        framework: 测试框架名称
        duration_ms: 持续时间（毫秒），为 0 时不设置
        data: 直接传入 data 字典（用于 Godot 等复杂场景），优先级高于 test_name/duration_ms/extra
        **extra: 其他 data 字段，会合并到 data 字典中

    Returns:
        dict: 符合统一事件格式的字典

    示例:
        # 基础用法（兼容 test_pipeline.py）
        event = make_event("test.start", test_name="test_login")

        # 直接传入 data（兼容 test_godot_agents.py）
        event = make_event("game.scene_load", data={"scene_path": "res://main.tscn"})

        # 带额外字段
        event = make_event("test.fail", test_name="test_api", errors=[{"message": "timeout"}])
    """
    event = {
        "event_id": f"evt_{uuid.uuid4().hex[:8]}",
        "session_id": session_id,
        "timestamp": int(time.time() * 1000),
        "source": {"framework": framework, "project": project},
        "type": event_type,
        "data": {},
    }

    if data is not None:
        # 直接传入 data 字典模式（兼容 test_godot_agents.py）
        event["data"] = data.copy()
    else:
        # 从参数构建 data 模式（兼容 test_pipeline.py 等）
        if test_name:
            event["data"]["test_name"] = test_name
        if duration_ms:
            event["data"]["duration_ms"] = duration_ms
        event["data"].update(extra)

    return event


def make_events_batch(
    passed: int = 5,
    failed: int = 1,
    flaky: int = 0,
    slow: int = 0,
    no_assert: int = 0,
    incomplete: int = 0,
    project: str = "proj-a",
    session_id: str = "sess-1",
    framework: str = "vitest",
) -> List[dict]:
    """创建一批具有特定问题特征的测试事件

    Args:
        passed: 正常通过的测试数量
        failed: 失败的测试数量
        flaky: 不稳定测试数量（先 pass 后 fail）
        slow: 慢测试数量（60秒）
        no_assert: 无断言的测试数量
        incomplete: 未完成的测试数量（只有 start 没有 end）
        project: 项目名称
        session_id: 会话 ID
        framework: 测试框架名称

    Returns:
        list[dict]: 事件列表

    示例:
        # 基础用法
        events = make_events_batch(passed=5, failed=1)

        # 复杂场景（兼容 test_architecture_validation.py）
        events = make_events_batch(passed=3, flaky=1, slow=1, no_assert=1, incomplete=1)
    """
    events = []

    def _evt(event_type: str, test_name: str = "", **extra: Any) -> dict:
        return make_event(
            event_type,
            test_name=test_name,
            session_id=session_id,
            project=project,
            framework=framework,
            **extra,
        )

    # 正常通过的测试
    for i in range(passed):
        name = f"test_pass_{i}"
        events.append(_evt("test.start", name))
        events.append(_evt("assert.pass", name))
        events.append(_evt("test.end", name, duration_ms=100 + i * 10))

    # 失败的测试
    for i in range(failed):
        name = f"test_fail_{i}"
        events.append(_evt("test.start", name))
        events.append(_evt("test.fail", name, duration_ms=50, errors=[{"message": f"error {i}"}]))

    # 不稳定测试（先 pass 后 fail）
    for i in range(flaky):
        name = f"test_flaky_{i}"
        events.append(_evt("test.start", name))
        events.append(_evt("test.end", name, duration_ms=200))
        events.append(_evt("test.start", name))
        events.append(_evt("test.fail", name, duration_ms=300))

    # 慢测试（60秒）
    for i in range(slow):
        name = f"test_slow_{i}"
        events.append(_evt("test.start", name))
        events.append(_evt("test.end", name, duration_ms=60000))

    # 无断言的测试
    for i in range(no_assert):
        name = f"test_no_assert_{i}"
        events.append(_evt("test.start", name))
        events.append(_evt("test.end", name, duration_ms=50))

    # 未完成的测试（只有 start 没有 end）
    for i in range(incomplete):
        name = f"test_incomplete_{i}"
        events.append(_evt("test.start", name))

    return events


def make_session(
    project: str = "test",
    framework: str = "pytest",
    session_id: Optional[str] = None,
) -> dict:
    """创建测试会话字典

    Args:
        project: 项目名称
        framework: 测试框架名称
        session_id: 会话 ID（可选，默认自动生成）

    Returns:
        dict: 符合统一会话格式的字典

    示例:
        session = make_session(project="my-app", framework="vitest")
    """
    if session_id is None:
        session_id = f"sess_{uuid.uuid4().hex[:8]}"

    return {
        "session_id": session_id,
        "project": project,
        "framework": framework,
        "started_at": int(time.time() * 1000),
    }
