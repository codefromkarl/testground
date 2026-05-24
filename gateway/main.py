"""FastAPI 网关 — 测试观测平台 API

接收测试事件、查询时间线、获取分析结果。
"""

from __future__ import annotations

import time
import uuid
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

import sys
from pathlib import Path

# 确保项目根目录在 path 中
_root = Path(__file__).parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from gateway.storage import Storage

# ─── 应用初始化 ─────────────────────────────────────────────

app = FastAPI(
    title="测试观测平台 API",
    description="统一测试事件网关，接收、存储、查询测试事件",
    version="0.1.0",
)

# CORS 支持（前端 Timeline 页面需要）
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 存储层
storage = Storage()


# ─── 请求/响应模型 ─────────────────────────────────────────


class EventSource(BaseModel):
    framework: str
    project: str
    file: Optional[str] = None
    test_name: Optional[str] = None
    suite: Optional[str] = None


class TestEvent(BaseModel):
    event_id: Optional[str] = None
    session_id: str
    timestamp: Optional[int] = None
    source: EventSource
    type: str
    data: Dict[str, Any]
    parent_event_id: Optional[str] = None
    trace_id: Optional[str] = None
    span_id: Optional[str] = None


class TestSessionCreate(BaseModel):
    session_id: Optional[str] = None
    project: str
    framework: str
    metadata: Optional[Dict[str, Any]] = None


class TestSessionUpdate(BaseModel):
    ended_at: Optional[int] = None
    total_tests: Optional[int] = None
    passed_tests: Optional[int] = None
    failed_tests: Optional[int] = None
    duration_ms: Optional[int] = None
    gate_result: Optional[Dict[str, Any]] = None


class BatchEventRequest(BaseModel):
    events: List[TestEvent]


class AnalysisCreate(BaseModel):
    analyzer: str
    findings: List[Dict[str, Any]]
    confidence: float
    summary: str
    recommendations: List[str] = []


# ─── 事件接口 ──────────────────────────────────────────────


@app.post("/events")
async def ingest_event(event: TestEvent) -> Dict[str, Any]:
    """接收单个测试事件"""
    event_id = event.event_id or str(uuid.uuid4())
    timestamp = event.timestamp or int(time.time() * 1000)

    from schema.events import EventSource as SchemaEventSource, TestEvent as SchemaTestEvent

    schema_event = SchemaTestEvent(
        event_id=event_id,
        session_id=event.session_id,
        timestamp=timestamp,
        source=SchemaEventSource(
            framework=event.source.framework,
            project=event.source.project,
            file=event.source.file,
            test_name=event.source.test_name,
            suite=event.source.suite,
        ),
        type=event.type,
        data=event.data,
        parent_event_id=event.parent_event_id,
        trace_id=event.trace_id,
        span_id=event.span_id,
    )

    storage.store_event(schema_event)
    return {"status": "accepted", "event_id": event_id}


@app.post("/events/batch")
async def ingest_events_batch(request: BatchEventRequest) -> Dict[str, Any]:
    """批量接收测试事件"""
    from schema.events import EventSource as SchemaEventSource, TestEvent as SchemaTestEvent

    schema_events = []
    for event in request.events:
        event_id = event.event_id or str(uuid.uuid4())
        timestamp = event.timestamp or int(time.time() * 1000)

        schema_events.append(
            SchemaTestEvent(
                event_id=event_id,
                session_id=event.session_id,
                timestamp=timestamp,
                source=SchemaEventSource(
                    framework=event.source.framework,
                    project=event.source.project,
                    file=event.source.file,
                    test_name=event.source.test_name,
                    suite=event.source.suite,
                ),
                type=event.type,
                data=event.data,
                parent_event_id=event.parent_event_id,
                trace_id=event.trace_id,
                span_id=event.span_id,
            )
        )

    count = storage.store_events_batch(schema_events)
    return {"status": "accepted", "count": count}


# ─── 时间线接口 ────────────────────────────────────────────


@app.get("/sessions/{session_id}/timeline")
async def get_timeline(
    session_id: str,
    event_type: Optional[str] = Query(None, description="按事件类型过滤"),
    limit: int = Query(1000, le=10000),
) -> Dict[str, Any]:
    """获取测试会话的时间线数据"""
    events = storage.get_session_events(session_id, event_type=event_type, limit=limit)
    if not events:
        raise HTTPException(status_code=404, detail="Session not found or no events")

    return {
        "session_id": session_id,
        "events": events,
        "count": len(events),
        "timeline_items": _events_to_timeline_items(events),
    }


@app.get("/trace/{trace_id}")
async def get_trace(trace_id: str) -> Dict[str, Any]:
    """按 trace_id 查询跨系统事件链"""
    events = storage.get_events_by_trace(trace_id)
    return {
        "trace_id": trace_id,
        "events": events,
        "count": len(events),
    }


# ─── 会话接口 ──────────────────────────────────────────────


@app.post("/sessions")
async def create_session(session: TestSessionCreate) -> Dict[str, Any]:
    """创建测试会话"""
    from schema.events import TestSession

    session_id = session.session_id or str(uuid.uuid4())
    schema_session = TestSession(
        session_id=session_id,
        project=session.project,
        framework=session.framework,
        started_at=int(time.time() * 1000),
        metadata=session.metadata,
    )
    storage.store_session(schema_session)
    return {"status": "created", "session_id": session_id}


@app.put("/sessions/{session_id}")
async def update_session(session_id: str, update: TestSessionUpdate) -> Dict[str, Any]:
    """更新测试会话"""
    existing = storage.get_session(session_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Session not found")

    from schema.events import TestSession

    updated = TestSession(
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


@app.get("/sessions/{session_id}")
async def get_session(session_id: str) -> Dict[str, Any]:
    """获取会话详情"""
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


@app.get("/sessions")
async def list_sessions(
    project: Optional[str] = Query(None),
    limit: int = Query(20, le=100),
) -> Dict[str, Any]:
    """列出最近的会话"""
    sessions = storage.get_recent_sessions(project=project, limit=limit)
    return {"sessions": sessions, "count": len(sessions)}


# ─── 分析接口 ──────────────────────────────────────────────


@app.post("/sessions/{session_id}/analysis")
async def create_analysis(session_id: str, analysis: AnalysisCreate) -> Dict[str, Any]:
    """创建 AI 分析结果"""
    from schema.events import AnalysisResult

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


@app.get("/sessions/{session_id}/analysis")
async def get_analysis(session_id: str) -> Dict[str, Any]:
    """获取会话的 AI 分析结果"""
    analyses = storage.get_session_analyses(session_id)
    return {"session_id": session_id, "analyses": analyses, "count": len(analyses)}


# ─── 项目统计接口 ──────────────────────────────────────────


@app.get("/projects/{project}/summary")
async def get_project_summary(
    project: str,
    days: int = Query(7, ge=1, le=90),
) -> Dict[str, Any]:
    """获取项目测试摘要"""
    return storage.get_project_stats(project, days=days)


@app.get("/projects/{project}/events")
async def get_project_events(
    project: str,
    since: Optional[int] = Query(None, description="起始时间戳 (Unix ms)"),
    limit: int = Query(100, le=1000),
) -> Dict[str, Any]:
    """获取项目的事件列表"""
    events = storage.get_events_by_project(project, since=since, limit=limit)
    return {"project": project, "events": events, "count": len(events)}


# ─── 门禁结果接口（兼容 loopexpedition 格式）────────────────


@app.get("/sessions/{session_id}/gate")
async def get_gate_result(session_id: str) -> Dict[str, Any]:
    """获取门禁结果"""
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


# ─── 健康检查 ──────────────────────────────────────────────


@app.get("/health")
async def health_check() -> Dict[str, Any]:
    """健康检查"""
    return {
        "status": "ok",
        "timestamp": int(time.time() * 1000),
        "version": "0.1.0",
    }


# ─── 辅助函数 ──────────────────────────────────────────────


def _events_to_timeline_items(events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """将事件转换为 Timeline 可视化格式"""
    items = []
    for event in events:
        # 确定分组
        group = event["source"]["project"]

        # 确定显示内容
        content = _format_event_content(event)

        # 确定样式
        className = _get_event_class(event["type"])

        items.append({
            "id": event["event_id"],
            "group": group,
            "start": event["timestamp"],
            "content": content,
            "className": className,
            "data": event,
        })

    return items


def _format_event_content(event: Dict[str, Any]) -> str:
    """格式化事件显示内容"""
    etype = event["type"]
    data = event["data"]

    if etype == "test.start":
        return f"▶ {data.get('test_name', 'test')}"
    elif etype == "test.end":
        status = "✅" if data.get("passed") else "❌"
        duration = data.get("duration_ms", 0)
        return f"{status} {data.get('test_name', 'test')} ({duration}ms)"
    elif etype == "test.fail":
        return f"❌ {data.get('test_name', 'test')}"
    elif etype == "assert.pass":
        return f"✓ {data.get('assertion_name', 'assertion')}"
    elif etype == "assert.fail":
        return f"✗ {data.get('assertion_name', 'assertion')}"
    elif etype == "agent.tool_call":
        return f"🔧 {data.get('tool_name', 'tool')}"
    elif etype == "agent.tool_result":
        status = "✓" if data.get("success") else "✗"
        return f"{status} {data.get('tool_name', 'tool')} result"
    elif etype == "game.state_change":
        return f"🎮 {data.get('scene_path', 'state change')}"
    elif etype == "report.bug_candidate":
        severity = data.get("severity", "medium")
        icon = {"high": "🔴", "medium": "🟡", "low": "🟢"}.get(severity, "⚪")
        return f"{icon} Bug: {data.get('description', '')[:50]}"
    elif etype == "report.gate_result":
        verdict = data.get("verdict", "UNKNOWN")
        return f"{'✅' if verdict == 'PASS' else '❌'} Gate: {verdict}"
    else:
        return f"[{etype}]"


def _get_event_class(event_type: str) -> str:
    """获取事件的 CSS 类名"""
    if event_type.startswith("test."):
        if "fail" in event_type or "error" in event_type:
            return "event-test-fail"
        return "event-test"
    elif event_type.startswith("assert."):
        if "fail" in event_type:
            return "event-assert-fail"
        return "event-assert"
    elif event_type.startswith("agent."):
        return "event-agent"
    elif event_type.startswith("game."):
        return "event-game"
    elif event_type.startswith("report."):
        if "bug" in event_type:
            return "event-bug"
        return "event-report"
    elif event_type.startswith("observation."):
        return "event-observation"
    return "event-default"


# ─── 启动入口 ──────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8900)
