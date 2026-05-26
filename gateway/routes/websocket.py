"""WebSocket 连接管理 + /ws/events/{session_id} 端点"""

from __future__ import annotations

import asyncio
import time
from typing import Dict, List, Optional

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

router = APIRouter()


class ConnectionManager:
    """管理 WebSocket 连接的 pub/sub 管理器"""

    def __init__(self):
        # session_id -> list of (websocket, set of event_type filters)
        self.active_connections: Dict[str, List[tuple]] = {}

    async def connect(
        self,
        websocket: WebSocket,
        session_id: str,
        event_types: Optional[List[str]] = None,
    ):
        await websocket.accept()
        filters = set(event_types) if event_types else set()
        self.active_connections.setdefault(session_id, []).append((websocket, filters))

    def disconnect(self, websocket: WebSocket, session_id: str):
        conns = self.active_connections.get(session_id, [])
        self.active_connections[session_id] = [(ws, f) for ws, f in conns if ws is not websocket]
        if not self.active_connections[session_id]:
            del self.active_connections[session_id]

    async def broadcast(self, session_id: str, event: dict):
        conns = self.active_connections.get(session_id, [])
        event_type = event.get("type", "")
        stale: list[WebSocket] = []
        for ws, filters in conns:
            if filters and event_type not in filters:
                continue
            try:
                await ws.send_json(event)
            except Exception:
                stale.append(ws)
        if stale:
            self.active_connections[session_id] = [(ws, f) for ws, f in conns if ws not in stale]
            if not self.active_connections[session_id]:
                del self.active_connections[session_id]

    def get_connection_count(self, session_id: Optional[str] = None) -> int:
        if session_id:
            return len(self.active_connections.get(session_id, []))
        return sum(len(conns) for conns in self.active_connections.values())


# ─── 心跳辅助 ──────────────────────────────────────────────


async def _heartbeat_loop(websocket: WebSocket):
    """每 30 秒发送心跳"""
    try:
        while True:
            await asyncio.sleep(30)
            await websocket.send_json({"type": "heartbeat", "timestamp": int(time.time() * 1000)})
    except (WebSocketDisconnect, Exception, asyncio.CancelledError):
        pass


# ─── WebSocket 端点 ─────────────────────────────────────────


@router.websocket("/ws/events/{session_id}")
async def event_stream(
    websocket: WebSocket,
    session_id: str,
    event_types: Optional[str] = Query(None, description="逗号分隔的事件类型过滤列表"),
):
    """WebSocket 实时事件流

    连接后实时接收指定 session 的事件推送。
    支持通过 query 参数 event_types 过滤事件类型（逗号分隔）。
    客户端可发送 JSON 消息动态更新过滤条件：
        {"action": "subscribe", "event_types": ["test.end", "test.fail"]}
        {"action": "unsubscribe"}  # 取消所有过滤，接收全部事件
        {"action": "ping"}  # 心跳
    服务端定期发送 {"type": "heartbeat"} 作为心跳。
    """
    manager: ConnectionManager = websocket.app.state.manager

    # 解析初始过滤条件
    initial_filters = [t.strip() for t in event_types.split(",")] if event_types else None
    await manager.connect(websocket, session_id, initial_filters)

    # 发送连接确认
    await websocket.send_json(
        {
            "type": "connected",
            "session_id": session_id,
            "event_types_filter": initial_filters,
        }
    )

    heartbeat_task = asyncio.create_task(_heartbeat_loop(websocket))

    try:
        while True:
            data = await websocket.receive_json()
            action = data.get("action", "")

            if action == "ping":
                await websocket.send_json({"type": "pong"})

            elif action == "subscribe":
                new_types = data.get("event_types", [])
                conns = manager.active_connections.get(session_id, [])
                for i, (ws, _) in enumerate(conns):
                    if ws is websocket:
                        conns[i] = (ws, set(new_types) if new_types else set())
                        break
                await websocket.send_json(
                    {
                        "type": "subscribed",
                        "event_types_filter": new_types or None,
                    }
                )

            elif action == "unsubscribe":
                conns = manager.active_connections.get(session_id, [])
                for i, (ws, _) in enumerate(conns):
                    if ws is websocket:
                        conns[i] = (ws, set())
                        break
                await websocket.send_json({"type": "unsubscribed"})

    except (WebSocketDisconnect, Exception):
        pass
    finally:
        heartbeat_task.cancel()
        manager.disconnect(websocket, session_id)
