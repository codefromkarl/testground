"""时间线 & 项目统计接口 — /sessions/{id}/timeline, /projects/*"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Query, Request

router = APIRouter()


# ─── 时间线 ────────────────────────────────────────────────


@router.get("/sessions/{session_id}/timeline")
async def get_timeline(
    session_id: str,
    request: Request,
    event_type: Optional[str] = Query(None, description="按事件类型过滤"),
    limit: int = Query(1000, le=10000),
) -> Dict[str, Any]:
    """获取测试会话的时间线数据"""
    storage = request.app.state.storage
    events = storage.get_session_events(session_id, event_type=event_type, limit=limit)
    if not events:
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail="Session not found or no events")

    return {
        "session_id": session_id,
        "events": events,
        "count": len(events),
        "timeline_items": _events_to_timeline_items(events),
    }


# ─── 项目统计 ──────────────────────────────────────────────


@router.get("/projects/{project}/summary")
async def get_project_summary(
    project: str,
    request: Request,
    days: int = Query(7, ge=1, le=90),
) -> Dict[str, Any]:
    """获取项目测试摘要"""
    storage = request.app.state.storage
    return storage.get_project_stats(project, days=days)


@router.get("/projects/{project}/events")
async def get_project_events(
    project: str,
    request: Request,
    since: Optional[int] = Query(None, description="起始时间戳 (Unix ms)"),
    limit: int = Query(100, le=1000),
) -> Dict[str, Any]:
    """获取项目的事件列表"""
    storage = request.app.state.storage
    events = storage.get_events_by_project(project, since=since, limit=limit)
    return {"project": project, "events": events, "count": len(events)}


# ─── 辅助函数 ──────────────────────────────────────────────


def _events_to_timeline_items(events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """将事件转换为 Timeline 可视化格式"""
    items = []
    for event in events:
        group = event["source"]["project"]
        content = _format_event_content(event)
        className = _get_event_class(event["type"])
        items.append(
            {
                "id": event["event_id"],
                "group": group,
                "start": event["timestamp"],
                "content": content,
                "className": className,
                "data": event,
            }
        )
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
