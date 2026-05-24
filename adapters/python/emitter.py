"""Python 事件发射器 — 连接 loopexpedition 的 AI 测试框架

将 RuntimeObservation、BugDiscovery、CoverageTracker 的输出
转换为统一事件格式并发送到网关。
"""

from __future__ import annotations

import time
import uuid
from typing import Any, Dict, Optional

import httpx

from schema.events import (
    EventSource,
    TestEvent,
    create_bug_candidate,
    create_game_state_change,
    create_test_end,
    create_test_start,
)


class UnifiedEventEmitter:
    """将 loopexpedition 的遥测数据转换为统一事件并发送。"""

    def __init__(
        self,
        gateway_url: str = "http://localhost:8900",
        project: str = "loopexpedition",
        session_id: Optional[str] = None,
    ) -> None:
        self.gateway_url = gateway_url.rstrip("/")
        self.session_id = session_id or str(uuid.uuid4())
        self.source = EventSource(framework="custom", project=project)
        self._client = httpx.Client(timeout=10.0)

    def close(self) -> None:
        self._client.close()

    # ─── 发送事件 ──────────────────────────────────────────

    def emit(self, event: TestEvent) -> None:
        """发送单个事件到网关"""
        try:
            self._client.post(
                f"{self.gateway_url}/events",
                json=event.to_dict(),
            )
        except httpx.RequestError as e:
            # 静默失败，不阻塞测试执行
            print(f"[EventEmitter] Failed to emit event: {e}")

    def emit_batch(self, events: list[TestEvent]) -> None:
        """批量发送事件"""
        try:
            self._client.post(
                f"{self.gateway_url}/events/batch",
                json={"events": [e.to_dict() for e in events]},
            )
        except httpx.RequestError as e:
            print(f"[EventEmitter] Failed to emit batch: {e}")

    # ─── 会话管理 ──────────────────────────────────────────

    def create_session(self, metadata: Optional[Dict[str, Any]] = None) -> str:
        """在网关创建测试会话"""
        try:
            resp = self._client.post(
                f"{self.gateway_url}/sessions",
                json={
                    "session_id": self.session_id,
                    "project": self.source.project,
                    "framework": self.source.framework,
                    "metadata": metadata,
                },
            )
            resp.raise_for_status()
        except httpx.RequestError as e:
            print(f"[EventEmitter] Failed to create session: {e}")
        return self.session_id

    def end_session(
        self,
        total_tests: int,
        passed: int,
        failed: int,
        duration_ms: int,
        gate_result: Optional[Dict[str, Any]] = None,
    ) -> None:
        """更新会话结束状态"""
        try:
            self._client.put(
                f"{self.gateway_url}/sessions/{self.session_id}",
                json={
                    "ended_at": int(time.time() * 1000),
                    "total_tests": total_tests,
                    "passed_tests": passed,
                    "failed_tests": failed,
                    "duration_ms": duration_ms,
                    "gate_result": gate_result,
                },
            )
        except httpx.RequestError as e:
            print(f"[EventEmitter] Failed to end session: {e}")

    # ─── 从现有数据结构转换 ────────────────────────────────

    def from_observation(self, obs: Any, context: str = "") -> TestEvent:
        """转换 RuntimeObservation 为统一事件
        
        Args:
            obs: RuntimeObservation 的 Observation 对象
            context: 附加上下文描述
        """
        event = create_game_state_change(
            session_id=self.session_id,
            source=self.source,
            scene_path=context or "observation",
            state={
                "tree_summary": getattr(obs, "tree_summary", lambda: "")(),
                "runtime_state": getattr(obs, "runtime_state", {}),
                "has_screenshot": bool(getattr(obs, "screenshot_base64", None)),
            },
        )
        self.emit(event)
        return event

    def from_telemetry(self, telemetry: Dict[str, Any]) -> TestEvent:
        """转换单条 telemetry 记录为统一事件
        
        Args:
            telemetry: 包含 type 和其他字段的字典
        """
        event_type = telemetry.get("type", "game.state_change")
        data = {k: v for k, v in telemetry.items() if k != "type"}

        event = TestEvent(
            event_id=str(uuid.uuid4()),
            session_id=self.session_id,
            timestamp=int(time.time() * 1000),
            source=self.source,
            type=event_type,
            data=data,
        )
        self.emit(event)
        return event

    def from_bug_candidate(self, bug: Any) -> TestEvent:
        """转换 BugCandidate 为统一事件
        
        Args:
            bug: BugDiscovery 的 BugCandidate 对象
        """
        event = create_bug_candidate(
            session_id=self.session_id,
            source=self.source,
            severity=getattr(bug, "severity", "medium"),
            category=getattr(bug, "category", "unknown"),
            description=getattr(bug, "description", ""),
            evidence=getattr(bug, "evidence", {}),
        )
        self.emit(event)
        return event

    def from_coverage(self, tracker: Any) -> TestEvent:
        """转换 CoverageTracker 为统一事件
        
        Args:
            tracker: CoverageTracker 对象
        """
        from schema.events import ObservationType

        event = TestEvent(
            event_id=str(uuid.uuid4()),
            session_id=self.session_id,
            timestamp=int(time.time() * 1000),
            source=self.source,
            type=ObservationType.OBSERVATION_COVERAGE.value,
            data={
                "seen_events": list(getattr(tracker, "_seen_events", set())),
                "seen_obs_keys": list(getattr(tracker, "_seen_obs_keys", set())),
                "event_coverage": getattr(tracker, "event_coverage", 0.0),
                "obs_coverage": getattr(tracker, "obs_coverage", 0.0),
            },
        )
        self.emit(event)
        return event

    def from_gate_result(self, gate_result: Dict[str, Any]) -> TestEvent:
        """转换 loopexpedition 的 gate_result 为统一事件
        
        Args:
            gate_result: 包含 verdict 和 rules 的字典
        """
        from schema.events import ReportType

        event = TestEvent(
            event_id=str(uuid.uuid4()),
            session_id=self.session_id,
            timestamp=int(time.time() * 1000),
            source=self.source,
            type=ReportType.REPORT_GATE_RESULT.value,
            data=gate_result,
        )
        self.emit(event)
        return event

    def from_test_report(self, report_path: str) -> list[TestEvent]:
        """从 loopexpedition 的 JSON 测试报告导入事件
        
        Args:
            report_path: test_report_*.json 文件路径
        """
        import json
        from pathlib import Path

        report = json.loads(Path(report_path).read_text())
        events = []

        # 导入门禁结果
        if "gate_result" in report:
            events.append(self.from_gate_result(report["gate_result"]))

        # 导入各阶段结果
        for phase_name, phase_data in report.get("phases", {}).items():
            event = TestEvent(
                event_id=str(uuid.uuid4()),
                session_id=self.session_id,
                timestamp=int(time.time() * 1000),
                source=EventSource(
                    framework="custom",
                    project=self.source.project,
                    suite=phase_name,
                ),
                type="report.summary",
                data={
                    "phase": phase_name,
                    **phase_data,
                },
            )
            events.append(event)
            self.emit(event)

        # 导入覆盖率数据
        coverage = report.get("phases", {}).get("coverage", {})
        if coverage:
            event = TestEvent(
                event_id=str(uuid.uuid4()),
                session_id=self.session_id,
                timestamp=int(time.time() * 1000),
                source=self.source,
                type="observation.coverage",
                data=coverage,
            )
            events.append(event)
            self.emit(event)

        return events


# ─── 上下文管理器用法 ──────────────────────────────────────


class emitter_session:
    """上下文管理器，自动创建和结束会话。

    Usage::

        with emitter_session("http://localhost:8900") as emitter:
            emitter.from_observation(obs)
            # ... 更多事件 ...
        # 退出时自动结束会话
    """

    def __init__(
        self,
        gateway_url: str = "http://localhost:8900",
        project: str = "loopexpedition",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.emitter = UnifiedEventEmitter(gateway_url=gateway_url, project=project)
        self.metadata = metadata
        self._start_time: int = 0

    def __enter__(self) -> UnifiedEventEmitter:
        self._start_time = int(time.time() * 1000)
        self.emitter.create_session(self.metadata)
        return self.emitter

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        duration = int(time.time() * 1000) - self._start_time
        self.emitter.end_session(
            total_tests=0,
            passed=0,
            failed=0,
            duration_ms=duration,
        )
        self.emitter.close()
