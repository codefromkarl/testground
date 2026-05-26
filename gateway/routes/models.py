"""请求/响应 Pydantic 模型"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel


class EventSource(BaseModel):
    framework: str
    project: str
    file: Optional[str] = None
    test_name: Optional[str] = None
    suite: Optional[str] = None


class EventPayload(BaseModel):
    event_id: Optional[str] = None
    session_id: str
    timestamp: Optional[int] = None
    source: EventSource
    type: str
    data: Dict[str, Any]
    parent_event_id: Optional[str] = None
    trace_id: Optional[str] = None
    span_id: Optional[str] = None


class SessionCreate(BaseModel):
    session_id: Optional[str] = None
    project: str
    framework: str
    metadata: Optional[Dict[str, Any]] = None


class SessionUpdate(BaseModel):
    ended_at: Optional[int] = None
    total_tests: Optional[int] = None
    passed_tests: Optional[int] = None
    failed_tests: Optional[int] = None
    duration_ms: Optional[int] = None
    gate_result: Optional[Dict[str, Any]] = None


class BatchEventRequest(BaseModel):
    events: List[EventPayload]


class AnalysisCreate(BaseModel):
    analyzer: str
    findings: List[Dict[str, Any]]
    confidence: float
    summary: str
    recommendations: List[str] = []
