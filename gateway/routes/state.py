"""状态追踪接口 — /sessions/{session_id}/states"""

from __future__ import annotations

import time
import uuid
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

from drivers.godot.state_tracker import GameStateTracker, StateSnapshot

router = APIRouter()


# ─── 请求/响应模型 ──────────────────────────────────────────


class StateRecordRequest(BaseModel):
    """记录状态请求"""

    state: Dict[str, Any]


class StateResponse(BaseModel):
    """状态响应"""

    status: str = "ok"
    snapshot: Optional[Dict[str, Any]] = None
    snapshots: Optional[List[Dict[str, Any]]] = None
    count: Optional[int] = None


class StateDiffResponse(BaseModel):
    """状态差异响应"""

    status: str = "ok"
    diff: Dict[str, Any]


class StateTimelineResponse(BaseModel):
    """状态时间线响应"""

    status: str = "ok"
    timeline: List[Dict[str, Any]]
    count: int


# ─── 辅助函数 ──────────────────────────────────────────────


def _get_tracker(request: Request) -> GameStateTracker:
    """获取 StateTracker 实例"""
    tracker: GameStateTracker = request.app.state.state_tracker
    return tracker


def _ensure_session(request: Request, session_id: str) -> Dict[str, Any]:
    """确保 session 存在"""
    session = request.app.state.storage.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail=f"Session not found: {session_id}")
    return session


# ─── 路由 ──────────────────────────────────────────────────


@router.post("/sessions/{session_id}/states", response_model=StateResponse)
async def record_state(
    session_id: str,
    body: StateRecordRequest,
    request: Request,
) -> StateResponse:
    """记录一个状态快照

    如果 session 尚未开始追踪，自动启动追踪。
    """
    _ensure_session(request, session_id)
    tracker = _get_tracker(request)

    # 自动开始追踪
    if not tracker.is_tracking(session_id):
        tracker.start_tracking(session_id)

    snapshot = tracker.record_state(session_id, body.state)
    return StateResponse(
        status="ok",
        snapshot=snapshot.to_dict(),
    )


@router.get("/sessions/{session_id}/states", response_model=StateResponse)
async def list_states(
    session_id: str,
    request: Request,
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
) -> StateResponse:
    """获取状态历史"""
    _ensure_session(request, session_id)
    tracker = _get_tracker(request)

    snapshots = tracker.get_snapshots(session_id)
    total = len(snapshots)

    # 分页
    page = snapshots[offset : offset + limit]

    return StateResponse(
        status="ok",
        snapshots=[s.to_dict() for s in page],
        count=total,
    )


@router.get("/sessions/{session_id}/states/diff", response_model=StateDiffResponse)
async def get_state_diff(
    session_id: str,
    request: Request,
    from_index: int = Query(0, ge=0, description="起始快照索引"),
    to_index: int = Query(-1, description="结束快照索引，-1 表示最后一个"),
) -> StateDiffResponse:
    """获取状态差异

    比较两个快照之间的差异，返回新增/删除/修改的字段。
    """
    _ensure_session(request, session_id)
    tracker = _get_tracker(request)

    try:
        diff = tracker.get_diff_between(session_id, from_index, to_index)
    except RuntimeError:
        raise HTTPException(status_code=404, detail=f"No state snapshots for session {session_id}")
    except IndexError:
        raise HTTPException(status_code=400, detail="Snapshot index out of range")

    return StateDiffResponse(
        status="ok",
        diff=diff.to_dict(),
    )


@router.get("/sessions/{session_id}/states/timeline", response_model=StateTimelineResponse)
async def get_state_timeline(
    session_id: str,
    request: Request,
) -> StateTimelineResponse:
    """获取状态时间线

    返回每个状态快照及相对于上一个状态的变更，适合 Timeline 组件展示。
    """
    _ensure_session(request, session_id)
    tracker = _get_tracker(request)

    timeline = tracker.get_timeline(session_id)

    return StateTimelineResponse(
        status="ok",
        timeline=[entry.to_dict() for entry in timeline],
        count=len(timeline),
    )
