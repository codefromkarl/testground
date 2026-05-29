"""测试工厂函数包 — 统一的测试数据创建工具"""

from .events import make_event, make_events_batch, make_session

__all__ = ["make_event", "make_events_batch", "make_session"]
