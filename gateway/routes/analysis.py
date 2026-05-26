"""分析 & 门禁接口 — /sessions/{id}/analysis, /sessions/{id}/gate, /trace/{id}"""

from __future__ import annotations

import time
import uuid
from typing import Any, Dict

from fastapi import APIRouter, HTTPException, Request

from gateway.routes.models import AnalysisCreate

router = APIRouter()


@router.post("/sessions/{session_id}/analysis")
async def create_analysis(session_id: str, analysis: AnalysisCreate, request: Request) -> Dict[str, Any]:
    """创建 AI 分析结果"""
    from schema.events import AnalysisResult

    storage = request.app.state.storage
    schema_analysis = AnalysisResult(
        analysis_id=str(uuid.uuid4()),
        session_id=session_id,
        timestamp=int(time.time() * 1000),
        analyzer=analysis.analyzer,
        findings=analysis.findings,
        confidence=analysis.confidence,
        summary=analysis.summary,
        recommendations=analysis.recommendations,
    )
    storage.store_analysis(schema_analysis)
    return {"status": "created", "analysis_id": schema_analysis.analysis_id}


@router.get("/sessions/{session_id}/analysis")
async def get_analysis(session_id: str, request: Request) -> Dict[str, Any]:
    """获取会话的 AI 分析结果"""
    storage = request.app.state.storage
    analyses = storage.get_session_analyses(session_id)
    return {"session_id": session_id, "analyses": analyses, "count": len(analyses)}


@router.get("/sessions/{session_id}/gate")
async def get_gate_result(session_id: str, request: Request) -> Dict[str, Any]:
    """获取门禁结果"""
    storage = request.app.state.storage
    session = storage.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    gate_result = session.get("gate_result")
    if gate_result:
        return gate_result

    # 从事件中聚合门禁结果
    events = storage.get_session_events(session_id, event_type="report.gate_result")
    if events:
        return events[-1]["data"]

    return {"verdict": "UNKNOWN", "message": "No gate result available"}


@router.get("/trace/{trace_id}")
async def get_trace(trace_id: str, request: Request) -> Dict[str, Any]:
    """按 trace_id 查询跨系统事件链"""
    storage = request.app.state.storage
    events = storage.get_events_by_trace(trace_id)
    return {
        "trace_id": trace_id,
        "events": events,
        "count": len(events),
    }
