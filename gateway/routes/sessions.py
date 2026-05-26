"""会话接口 — /sessions/*"""

from __future__ import annotations

import time
import uuid
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Query, Request

from gateway.routes.models import SessionCreate, SessionUpdate

router = APIRouter()


@router.post("/sessions")
async def create_session(session: SessionCreate, request: Request) -> Dict[str, Any]:
    """创建测试会话"""
    from schema.events import ObsSession

    storage = request.app.state.storage
    session_id = session.session_id or str(uuid.uuid4())
    schema_session = ObsSession(
        session_id=session_id,
        project=session.project,
        framework=session.framework,
        started_at=int(time.time() * 1000),
        metadata=session.metadata,
    )
    storage.store_session(schema_session)
    return {"status": "created", "session_id": session_id}


@router.put("/sessions/{session_id}")
async def update_session(session_id: str, update: SessionUpdate, request: Request) -> Dict[str, Any]:
    """更新测试会话"""
    from schema.events import ObsSession

    storage = request.app.state.storage
    existing = storage.get_session(session_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Session not found")

    updated = ObsSession(
        session_id=session_id,
        project=existing["project"],
        framework=existing["framework"],
        started_at=existing["started_at"],
        ended_at=update.ended_at or existing.get("ended_at"),
        total_tests=update.total_tests or existing.get("total_tests"),
        passed_tests=update.passed_tests or existing.get("passed_tests"),
        failed_tests=update.failed_tests or existing.get("failed_tests"),
        duration_ms=update.duration_ms or existing.get("duration_ms"),
        gate_result=update.gate_result or existing.get("gate_result"),
        metadata=existing.get("metadata"),
    )
    storage.store_session(updated)
    return {"status": "updated", "session_id": session_id}


@router.get("/sessions/{session_id}")
async def get_session(session_id: str, request: Request) -> Dict[str, Any]:
    """获取会话详情"""
    storage = request.app.state.storage
    session = storage.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    # 附带事件统计
    events = storage.get_session_events(session_id, limit=10000)
    event_stats: Dict[str, int] = {}
    for e in events:
        t = e["type"]
        event_stats[t] = event_stats.get(t, 0) + 1

    session["event_stats"] = event_stats
    session["total_events"] = len(events)
    return session


@router.get("/sessions")
async def list_sessions(
    request: Request,
    project: Optional[str] = Query(None),
    limit: int = Query(20, le=100),
) -> Dict[str, Any]:
    """列出最近的会话"""
    storage = request.app.state.storage
    sessions = storage.get_recent_sessions(project=project, limit=limit)
    return {"sessions": sessions, "count": len(sessions)}
