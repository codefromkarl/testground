"""事件接口 — /events 和 /events/batch"""

from __future__ import annotations

import time
import uuid
from typing import Any, Dict

from fastapi import APIRouter, Request

from gateway.routes.models import BatchEventRequest, EventPayload

router = APIRouter()


def _to_schema_event(event: EventPayload):
    """将 API 模型转换为 schema 数据类"""
    from schema.events import EventSource as SchemaEventSource
    from schema.events import ObsEvent as SchemaObsEvent

    event_id = event.event_id or str(uuid.uuid4())
    timestamp = event.timestamp or int(time.time() * 1000)

    return (
        SchemaObsEvent(
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
        ),
        event_id,
        timestamp,
    )


@router.post("/events")
async def ingest_event(event: EventPayload, request: Request) -> Dict[str, Any]:
    """接收单个测试事件"""
    storage = request.app.state.storage
    manager = request.app.state.manager

    schema_event, event_id, timestamp = _to_schema_event(event)

    storage.store_event(schema_event)

    await manager.broadcast(
        event.session_id,
        {
            "event_id": event_id,
            "session_id": event.session_id,
            "timestamp": timestamp,
            "source": {
                "framework": event.source.framework,
                "project": event.source.project,
                "file": event.source.file,
                "test_name": event.source.test_name,
                "suite": event.source.suite,
            },
            "type": event.type,
            "data": event.data,
        },
    )
    return {"status": "accepted", "event_id": event_id}


@router.post("/events/batch")
async def ingest_events_batch(request_body: BatchEventRequest, request: Request) -> Dict[str, Any]:
    """批量接收测试事件"""
    storage = request.app.state.storage

    schema_events = []
    for event in request_body.events:
        schema_event, _, _ = _to_schema_event(event)
        schema_events.append(schema_event)

    count = storage.store_events_batch(schema_events)
    return {"status": "accepted", "count": count}
