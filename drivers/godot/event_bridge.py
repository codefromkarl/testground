"""Driver → Gateway 事件桥接层

将 GodotDriver 的操作结果自动转化为 ObsEvent，发送到 Gateway API。
桥接可选：Driver 可独立使用，EventBridge 是装饰层。

用法:
    driver = GodotDriver("127.0.0.1", 19090)
    bridge = EventBridge(driver, gateway_url="http://localhost:8900")

    async with bridge:
        await bridge.start_session("my_game", "godot_driver")
        await driver.click_node("UI/Button")
        await bridge.report_test_start("button_click_test", "button_click_test")
        await bridge.report_test_end("button_click_test", True, 120)
        await bridge.end_session()
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

from schema.events import (
    EventSource,
    Framework,
    ObsEvent,
    create_bench_event,
    create_debug_event,
    create_test_end,
    create_test_start,
    create_visual_assertion,
)


class EventBridge:
    """事件桥接 — 连接 GodotDriver 到 Gateway

    职责:
    1. 管理 session 生命周期（创建/结束）
    2. 将 Driver 操作转化为 ObsEvent
    3. 批量发送事件到 Gateway
    4. 容错：Gateway 不可用时不阻塞 Driver

    参数:
        driver: GodotDriver 实例
        gateway_url: Gateway API 基础 URL
        project: 项目名称
        framework: 框架标识（默认 godot_driver）
        flush_interval: 自动刷新间隔（秒），0 表示禁用自动刷新
        batch_size: 达到此数量时自动发送
        max_buffer: 缓冲区上限，超过后丢弃最旧事件
    """

    def __init__(
        self,
        driver: Any,  # GodotDriver，避免循环导入
        gateway_url: str = "http://localhost:8900",
        project: str = "",
        framework: str = Framework.GODOT_DRIVER,
        flush_interval: float = 5.0,
        batch_size: int = 50,
        max_buffer: int = 1000,
    ):
        self._driver = driver
        self._gateway_url = gateway_url.rstrip("/")
        self._framework = framework
        self._flush_interval = flush_interval
        self._batch_size = batch_size
        self._max_buffer = max_buffer

        # Session 状态
        self._session_id: Optional[str] = None
        self._project = project
        self._session_started_at: Optional[int] = None
        self._test_count = 0
        self._passed_count = 0
        self._failed_count = 0

        # 事件缓冲
        self._buffer: List[Dict[str, Any]] = []
        self._buffer_lock = asyncio.Lock()

        # HTTP 客户端
        self._client: Optional[httpx.AsyncClient] = None
        # 自动刷新任务
        self._flush_task: Optional[asyncio.Task] = None
        # 统计
        self._sent_count = 0
        self._error_count = 0

    async def __aenter__(self) -> "EventBridge":
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self._gateway_url,
                timeout=httpx.Timeout(10.0, connect=5.0),
            )
        if self._flush_interval > 0:
            self._flush_task = asyncio.create_task(self._auto_flush_loop())
        return self

    async def __aexit__(self, *args) -> None:
        if self._flush_task:
            self._flush_task.cancel()
            try:
                await self._flush_task
            except asyncio.CancelledError:
                pass
        # 最终刷新
        await self._flush()
        if self._client:
            await self._client.aclose()
            self._client = None

    # ─── 属性 ──────────────────────────────────────────

    @property
    def session_id(self) -> Optional[str]:
        return self._session_id

    @property
    def project(self) -> str:
        return self._project

    @property
    def sent_count(self) -> int:
        return self._sent_count

    @property
    def error_count(self) -> int:
        return self._error_count

    @property
    def buffer_size(self) -> int:
        return len(self._buffer)

    @property
    def is_active(self) -> bool:
        return self._session_id is not None

    # ─── Session 管理 ───────────────────────────────────

    async def start_session(
        self,
        project: str,
        framework: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> str:
        """创建 Gateway session，开始桥接事件流

        Args:
            project: 项目名称
            framework: 框架标识（默认使用构造时的值）
            metadata: 附加元数据

        Returns:
            session_id
        """
        self._project = project
        if framework:
            self._framework = framework

        session_id = f"driver-{project}-{int(time.time() * 1000)}"
        self._session_id = session_id
        self._session_started_at = int(time.time() * 1000)
        self._test_count = 0
        self._passed_count = 0
        self._failed_count = 0

        # 创建 session
        if self._client:
            try:
                resp = await self._client.post(
                    "/sessions",
                    json={
                        "session_id": session_id,
                        "project": project,
                        "framework": self._framework,
                        "metadata": metadata or {},
                    },
                )
                resp.raise_for_status()
            except Exception:
                # 容错：Gateway 不可用不阻塞 Driver
                self._error_count += 1

        return session_id

    async def end_session(
        self,
        gate_result: Optional[Dict[str, Any]] = None,
    ) -> None:
        """结束 session，刷新缓冲区，更新 session 统计

        Args:
            gate_result: 门禁结果（可选）
        """
        if not self._session_id:
            return

        # 刷新剩余事件
        await self._flush()

        # 更新 session
        if self._client:
            now = int(time.time() * 1000)
            try:
                resp = await self._client.put(
                    f"/sessions/{self._session_id}",
                    json={
                        "ended_at": now,
                        "total_tests": self._test_count,
                        "passed_tests": self._passed_count,
                        "failed_tests": self._failed_count,
                        "duration_ms": now - (self._session_started_at or now),
                        "gate_result": gate_result,
                    },
                )
                resp.raise_for_status()
            except Exception:
                self._error_count += 1

        self._session_id = None

    # ─── 事件报告方法 ──────────────────────────────────

    async def report_test_start(
        self,
        test_name: str,
        full_name: Optional[str] = None,
        trace_id: Optional[str] = None,
    ) -> None:
        """报告测试开始事件

        Args:
            test_name: 测试短名
            full_name: 完整名称（默认同 test_name）
            trace_id: 链路追踪 ID
        """
        event = create_test_start(
            session_id=self._session_id or "",
            source=self._make_source(test_name=test_name),
            test_name=test_name,
            full_name=full_name or test_name,
            trace_id=trace_id,
        )
        await self._enqueue(event)

    async def report_test_end(
        self,
        test_name: str,
        passed: bool,
        duration_ms: int,
        errors: Optional[List[Dict[str, Any]]] = None,
        trace_id: Optional[str] = None,
    ) -> None:
        """报告测试结束事件

        Args:
            test_name: 测试名
            passed: 是否通过
            duration_ms: 耗时（毫秒）
            errors: 错误列表
            trace_id: 链路追踪 ID
        """
        self._test_count += 1
        if passed:
            self._passed_count += 1
        else:
            self._failed_count += 1

        event = create_test_end(
            session_id=self._session_id or "",
            source=self._make_source(test_name=test_name),
            test_name=test_name,
            passed=passed,
            duration_ms=duration_ms,
            errors=errors,
            trace_id=trace_id,
        )
        await self._enqueue(event)

    async def screenshot_and_report(
        self,
        context: Optional[str] = None,
        filename: Optional[str] = None,
        trace_id: Optional[str] = None,
    ) -> Path:
        """截图并报告 action.screenshot 事件

        Args:
            context: 截图上下文描述
            filename: 文件名
            trace_id: 链路追踪 ID

        Returns:
            截图文件路径
        """
        filepath = await self._driver.screenshot(filename)
        from schema.events import ActionType, _generate_id, _timestamp

        event = ObsEvent(
            event_id=_generate_id(),
            session_id=self._session_id or "",
            timestamp=_timestamp(),
            source=self._make_source(),
            type=ActionType.ACTION_SCREENSHOT.value,
            data={
                "context": context or "",
                "filepath": str(filepath),
                "exists": filepath.exists() if isinstance(filepath, Path) else False,
            },
            trace_id=trace_id,
        )
        await self._enqueue(event)
        return filepath

    async def visual_assert(
        self,
        template_path: str,
        threshold: float = 0.8,
        source_image: Optional[Any] = None,
        trace_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """视觉断言 + 报告 assert.* 事件

        会截取当前屏幕（除非提供 source_image），用 VisualAsserter 模板匹配。

        Args:
            template_path: 模板图片路径
            threshold: 匹配阈值
            source_image: 源图片（默认从 driver 截图）
            trace_id: 链路追踪 ID

        Returns:
            匹配结果字典
        """
        # 延迟导入，避免循环依赖
        from drivers.godot.visual import TemplateMatch, VisualAsserter

        asserter = VisualAsserter()
        if source_image is None:
            source_image = await self._driver.screenshot("visual_assert.png")

        template = TemplateMatch(template_path=template_path, threshold=threshold)
        result = asserter.match_template(source_image, template)

        event = create_visual_assertion(
            session_id=self._session_id or "",
            source=self._make_source(),
            template_name=Path(template_path).stem,
            matched=result.matched,
            confidence=result.confidence,
            position=list(result.position) if result.position else None,
            trace_id=trace_id,
        )
        await self._enqueue(event)

        return {
            "matched": result.matched,
            "confidence": result.confidence,
            "position": result.position,
        }

    async def report_debug_match(
        self,
        entry_id: str,
        error_code: str,
        error_message: str,
        trace_id: Optional[str] = None,
    ) -> None:
        """报告调试匹配事件 (debug.match)

        Args:
            entry_id: 匹配的 DebugEntry ID
            error_code: 错误码
            error_message: 错误消息
            trace_id: 链路追踪 ID
        """
        event = create_debug_event(
            session_id=self._session_id or "",
            source=self._make_source(),
            debug_type="debug.match",
            entry_id=entry_id,
            error_code=error_code,
            error_message=error_message,
            trace_id=trace_id,
        )
        await self._enqueue(event)

    async def report_debug_repair(
        self,
        entry_id: str,
        fix_description: str,
        error_code: Optional[str] = None,
        trace_id: Optional[str] = None,
    ) -> None:
        """报告调试修复事件 (debug.repair)

        Args:
            entry_id: 修复的 DebugEntry ID
            fix_description: 修复描述
            error_code: 关联错误码
            trace_id: 链路追踪 ID
        """
        event = create_debug_event(
            session_id=self._session_id or "",
            source=self._make_source(),
            debug_type="debug.repair",
            entry_id=entry_id,
            error_code=error_code,
            fix_description=fix_description,
            trace_id=trace_id,
        )
        await self._enqueue(event)

    async def report_bench_result(
        self,
        dimension: str,
        score: float,
        passed: bool,
        checks: Optional[List[Dict[str, Any]]] = None,
        trace_id: Optional[str] = None,
    ) -> None:
        """报告评估结果事件 (bench.*)

        Args:
            dimension: 评估维度（build_health / visual_usability / intent_alignment）
            score: 分数（0-100）
            passed: 是否及格
            checks: 检查项列表
            trace_id: 链路追踪 ID
        """
        event = create_bench_event(
            session_id=self._session_id or "",
            source=self._make_source(),
            dimension=dimension,
            score=score,
            passed=passed,
            checks=checks,
            trace_id=trace_id,
        )
        await self._enqueue(event)

    async def report_game_event(
        self,
        event_type: str,
        data: Dict[str, Any],
        trace_id: Optional[str] = None,
    ) -> None:
        """报告游戏事件 (game.*)

        Args:
            event_type: 事件类型（如 "game.state_change"）
            data: 事件数据
            trace_id: 链路追踪 ID
        """
        from schema.events import _generate_id, _timestamp

        event = ObsEvent(
            event_id=_generate_id(),
            session_id=self._session_id or "",
            timestamp=_timestamp(),
            source=self._make_source(),
            type=event_type,
            data=data,
            trace_id=trace_id,
        )
        await self._enqueue(event)

    # ─── 批量发送 ───────────────────────────────────────

    async def flush(self) -> int:
        """手动刷新缓冲区，返回发送数量"""
        return await self._flush()

    # ─── 内部方法 ───────────────────────────────────────

    def _make_source(
        self,
        test_name: Optional[str] = None,
        file: Optional[str] = None,
        suite: Optional[str] = None,
    ) -> EventSource:
        """构建 EventSource"""
        return EventSource(
            framework=self._framework,
            project=self._project,
            test_name=test_name,
            file=file,
            suite=suite,
        )

    async def _enqueue(self, event: ObsEvent) -> None:
        """将事件加入缓冲区，达到阈值时自动刷新"""
        payload = event.to_dict()
        async with self._buffer_lock:
            # 容量保护
            if len(self._buffer) >= self._max_buffer:
                self._buffer.pop(0)
            self._buffer.append(payload)

            if len(self._buffer) >= self._batch_size:
                # 取出当前批次
                batch = self._buffer[: self._batch_size]
                self._buffer = self._buffer[self._batch_size :]
                # 异步发送（不阻塞调用方）
                asyncio.create_task(self._send_batch(batch))

    async def _flush(self) -> int:
        """刷新所有缓冲事件"""
        async with self._buffer_lock:
            if not self._buffer:
                return 0
            batch = self._buffer[:]
            self._buffer.clear()

        return await self._send_batch(batch)

    async def _send_batch(self, events: List[Dict[str, Any]]) -> int:
        """发送一批事件到 Gateway

        Returns:
            成功发送的数量
        """
        if not self._client or not events:
            return 0

        try:
            resp = await self._client.post(
                "/events/batch",
                json={"events": events},
            )
            resp.raise_for_status()
            self._sent_count += len(events)
            return len(events)
        except Exception:
            self._error_count += 1
            # 发送失败：将事件放回缓冲区头部（最多保留 max_buffer）
            async with self._buffer_lock:
                remaining = self._max_buffer - len(self._buffer)
                if remaining > 0:
                    self._buffer = events[:remaining] + self._buffer
            return 0

    async def _auto_flush_loop(self) -> None:
        """定时自动刷新循环"""
        while True:
            try:
                await asyncio.sleep(self._flush_interval)
                await self._flush()
            except asyncio.CancelledError:
                break
            except Exception:
                # 自动刷新失败不影响主流程
                pass
