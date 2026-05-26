"""共享依赖注入 — 获取 Storage / ConnectionManager 实例"""

from __future__ import annotations

from fastapi import Request

from gateway.routes.websocket import ConnectionManager
from gateway.storage import Storage


def get_storage(request: Request) -> Storage:
    return request.app.state.storage


def get_manager(request: Request) -> ConnectionManager:
    return request.app.state.manager
