"""报告导出接口 — /sessions/{id}/report"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any, Dict

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, Response

router = APIRouter()


@router.get("/sessions/{session_id}/report")
async def get_session_report(
    session_id: str,
    request: Request,
    format: str = Query("html", pattern="^(html|json|md)$"),
) -> Response:
    """生成并返回测试报告。

    Args:
        session_id: 测试会话 ID
        format: 报告格式 — html / json / md
    """
    from analyzers.report import ReportGenerator

    storage = request.app.state.storage
    generator = ReportGenerator(storage)

    # 检查 session 是否存在
    session = storage.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    try:
        content = generator.generate_string(session_id, format=format)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Report generation failed: {e}")

    if format == "html":
        return HTMLResponse(content=content)
    elif format == "json":
        return Response(
            content=content,
            media_type="application/json",
        )
    else:  # md
        return PlainTextResponse(content=content, media_type="text/markdown")
