"""截图管理接口 — /sessions/{session_id}/screenshots"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

from gateway.screenshot_storage import ScreenshotStorage

router = APIRouter()


# ─── 请求/响应模型 ──────────────────────────────────────────


class ScreenshotUpload(BaseModel):
    """截图上传请求"""

    base64_data: str
    context: Optional[str] = None
    filename: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None


class ScreenshotDiffRequest(BaseModel):
    """截图对比请求"""

    screenshot_id_1: str
    screenshot_id_2: str
    threshold: float = 0.01
    generate_diff_image: bool = True


class ScreenshotResponse(BaseModel):
    """截图响应"""

    status: str = "ok"
    screenshot: Optional[Dict[str, Any]] = None
    screenshots: Optional[List[Dict[str, Any]]] = None
    count: Optional[int] = None


class ScreenshotDiffResponse(BaseModel):
    """截图对比响应"""

    status: str = "ok"
    diff: Dict[str, Any]


# ─── 路由 ──────────────────────────────────────────────


@router.post("/sessions/{session_id}/screenshots", response_model=ScreenshotResponse)
async def upload_screenshot(
    session_id: str,
    request: Request,
) -> ScreenshotResponse:
    """上传截图

    支持两种方式：
    1. JSON body: {"base64_data": "...", "context": "..."}
    2. Multipart form: file=..., context=...
    """
    storage: ScreenshotStorage = request.app.state.screenshot_storage

    # 检查 session 是否存在
    session = request.app.state.storage.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail=f"Session not found: {session_id}")

    # 尝试解析 JSON body
    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        try:
            body = await request.json()
            base64_data = body.get("base64_data")
            context = body.get("context")
            filename = body.get("filename")
            metadata = body.get("metadata")

            if not base64_data:
                raise HTTPException(status_code=400, detail="base64_data is required")

            info = storage.store_screenshot_base64(
                session_id=session_id,
                base64_data=base64_data,
                context=context,
                filename=filename,
                metadata=metadata,
            )
            return ScreenshotResponse(
                status="ok",
                screenshot=info.to_dict(),
            )
        except json.JSONDecodeError:
            raise HTTPException(status_code=400, detail="Invalid JSON")
        except Exception as e:
            if isinstance(e, HTTPException):
                raise
            raise HTTPException(status_code=400, detail=f"Invalid base64 data: {e}")

    # 处理 multipart form
    if "multipart/form-data" in content_type:
        try:
            form = await request.form()
            file = form.get("file")
            context = form.get("context")
            filename = form.get("filename")

            if file:
                image_data = await file.read()
                info = storage.store_screenshot(
                    session_id=session_id,
                    image_data=image_data,
                    context=context,
                    filename=filename or file.filename,
                )
                return ScreenshotResponse(
                    status="ok",
                    screenshot=info.to_dict(),
                )
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Failed to store screenshot: {e}")

    raise HTTPException(status_code=400, detail="No screenshot data provided")


@router.get("/sessions/{session_id}/screenshots", response_model=ScreenshotResponse)
async def list_screenshots(
    session_id: str,
    request: Request,
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
) -> ScreenshotResponse:
    """列出会话截图"""
    storage: ScreenshotStorage = request.app.state.screenshot_storage

    # 检查 session 是否存在
    session = request.app.state.storage.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail=f"Session not found: {session_id}")

    screenshots = storage.list_screenshots(session_id, limit=limit, offset=offset)
    count = storage.count_screenshots(session_id)

    return ScreenshotResponse(
        status="ok",
        screenshots=screenshots,
        count=count,
    )


@router.get("/sessions/{session_id}/screenshots/stats")
async def get_screenshot_stats(
    session_id: str,
    request: Request,
) -> Dict[str, Any]:
    """获取会话截图统计"""
    storage: ScreenshotStorage = request.app.state.screenshot_storage

    # 检查 session 是否存在
    session = request.app.state.storage.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail=f"Session not found: {session_id}")

    count = storage.count_screenshots(session_id)

    return {
        "status": "ok",
        "session_id": session_id,
        "count": count,
    }


@router.get("/sessions/{session_id}/screenshots/{screenshot_id}")
async def get_screenshot(
    session_id: str,
    screenshot_id: str,
    request: Request,
    include_base64: bool = Query(True, description="是否包含 base64 数据"),
) -> Dict[str, Any]:
    """获取截图详情"""
    storage: ScreenshotStorage = request.app.state.screenshot_storage

    # 检查 session 是否存在
    session = request.app.state.storage.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail=f"Session not found: {session_id}")

    result = storage.get_screenshot(screenshot_id, include_base64=include_base64)
    if not result:
        raise HTTPException(status_code=404, detail=f"Screenshot not found: {screenshot_id}")

    # 验证截图属于该 session
    if result.get("session_id") != session_id:
        raise HTTPException(status_code=404, detail=f"Screenshot not found in session: {session_id}")

    return {
        "status": "ok",
        "screenshot": result,
    }


@router.post("/sessions/{session_id}/screenshots/diff", response_model=ScreenshotDiffResponse)
async def diff_screenshots(
    session_id: str,
    request: Request,
    diff_request: ScreenshotDiffRequest,
) -> ScreenshotDiffResponse:
    """对比两张截图"""
    storage: ScreenshotStorage = request.app.state.screenshot_storage

    # 检查 session 是否存在
    session = request.app.state.storage.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail=f"Session not found: {session_id}")

    # 验证截图属于该 session
    screenshot1 = storage.get_screenshot(diff_request.screenshot_id_1, include_base64=False)
    screenshot2 = storage.get_screenshot(diff_request.screenshot_id_2, include_base64=False)

    if not screenshot1:
        raise HTTPException(status_code=404, detail=f"Screenshot not found: {diff_request.screenshot_id_1}")
    if not screenshot2:
        raise HTTPException(status_code=404, detail=f"Screenshot not found: {diff_request.screenshot_id_2}")

    if screenshot1.get("session_id") != session_id:
        raise HTTPException(status_code=400, detail=f"Screenshot {diff_request.screenshot_id_1} does not belong to session {session_id}")
    if screenshot2.get("session_id") != session_id:
        raise HTTPException(status_code=400, detail=f"Screenshot {diff_request.screenshot_id_2} does not belong to session {session_id}")

    # 执行对比
    diff_result = storage.diff_screenshots(
        screenshot_id_1=diff_request.screenshot_id_1,
        screenshot_id_2=diff_request.screenshot_id_2,
        threshold=diff_request.threshold,
        generate_diff_image=diff_request.generate_diff_image,
    )

    if diff_result is None:
        raise HTTPException(status_code=400, detail="Failed to compute diff")

    return ScreenshotDiffResponse(
        status="ok",
        diff=diff_result.to_dict(),
    )


@router.delete("/sessions/{session_id}/screenshots/{screenshot_id}")
async def delete_screenshot(
    session_id: str,
    screenshot_id: str,
    request: Request,
) -> Dict[str, Any]:
    """删除截图"""
    storage: ScreenshotStorage = request.app.state.screenshot_storage

    # 检查 session 是否存在
    session = request.app.state.storage.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail=f"Session not found: {session_id}")

    # 验证截图存在
    screenshot = storage.get_screenshot(screenshot_id, include_base64=False)
    if not screenshot:
        raise HTTPException(status_code=404, detail=f"Screenshot not found: {screenshot_id}")

    if screenshot.get("session_id") != session_id:
        raise HTTPException(status_code=404, detail=f"Screenshot not found in session: {session_id}")

    # 删除
    storage.delete_screenshot(screenshot_id)

    return {"status": "ok", "message": f"Screenshot {screenshot_id} deleted"}
