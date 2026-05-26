"""FastAPI 网关 — 测试观测平台 API

接收测试事件、查询时间线、获取分析结果。
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Any, Dict

# 确保项目根目录在 path 中
_root = Path(__file__).parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from gateway.routes import analysis, events, projects, sessions
from gateway.routes import screenshots
from gateway.routes import websocket as ws_router
from gateway.screenshot_storage import ScreenshotStorage
from gateway.routes.websocket import ConnectionManager
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

# 存储层（挂到 app.state 以便路由通过 request.app.state 访问）
storage = Storage()
app.state.storage = storage

# 截图存储层
screenshot_storage = ScreenshotStorage()
app.state.screenshot_storage = screenshot_storage

# WebSocket 连接管理器
manager = ConnectionManager()
app.state.manager = manager

# ─── 注册路由 ──────────────────────────────────────────────

app.include_router(events.router, tags=["events"])
app.include_router(sessions.router, tags=["sessions"])
app.include_router(analysis.router, tags=["analysis"])
app.include_router(projects.router, tags=["projects"])
app.include_router(screenshots.router, tags=["screenshots"])
app.include_router(ws_router.router, tags=["websocket"])


# ─── 健康检查 ──────────────────────────────────────────────


@app.get("/health")
async def health_check() -> Dict[str, Any]:
    """健康检查"""
    return {
        "status": "ok",
        "timestamp": int(time.time() * 1000),
        "version": "0.1.0",
    }


# ─── 启动入口 ──────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8900)
