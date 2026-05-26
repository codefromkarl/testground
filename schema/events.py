"""统一测试事件模型 — Python 类型定义

所有测试框架（Vitest / gdUnit4 / Playwright / Airtest）
产生的事件都转换为这个格式。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

# ─── 事件类型枚举 ──────────────────────────────────────────


class TestLifecycleType(str, Enum):
    TEST_START = "test.start"
    TEST_END = "test.end"
    TEST_SKIP = "test.skip"
    TEST_FAIL = "test.fail"
    TEST_ERROR = "test.error"


class AssertionType(str, Enum):
    ASSERT_PASS = "assert.pass"
    ASSERT_FAIL = "assert.fail"
    ASSERT_SEMANTIC = "assert.semantic"


class ActionType(str, Enum):
    ACTION_CLICK = "action.click"
    ACTION_INPUT = "action.input"
    ACTION_NAVIGATE = "action.navigate"
    ACTION_WAIT = "action.wait"
    ACTION_SCREENSHOT = "action.screenshot"


class GameEventType(str, Enum):
    GAME_STATE_CHANGE = "game.state_change"
    GAME_SCENE_LOAD = "game.scene_load"
    GAME_SIGNAL_EMIT = "game.signal_emit"
    GAME_SAVE = "game.save"
    GAME_LOAD = "game.load"


class AgentEventType(str, Enum):
    AGENT_LLM_CALL = "agent.llm_call"
    AGENT_TOOL_CALL = "agent.tool_call"
    AGENT_TOOL_RESULT = "agent.tool_result"
    AGENT_THINKING = "agent.thinking"


class ObservationType(str, Enum):
    OBSERVATION_SNAPSHOT = "observation.snapshot"
    OBSERVATION_COVERAGE = "observation.coverage"
    OBSERVATION_ANOMALY = "observation.anomaly"
    OBSERVATION_STATE_DIFF = "observation.state_diff"
    OBSERVATION_INPUT_TRACE = "observation.input_trace"
    OBSERVATION_CAUSE_TRACE = "observation.cause_trace"


class DebugEventType(str, Enum):
    """调试协议事件 (借鉴 OpenGame Debug Skill)"""

    DEBUG_ITERATION = "debug.iteration"
    DEBUG_MATCH = "debug.match"
    DEBUG_REPAIR = "debug.repair"
    DEBUG_EVOLVE = "debug.evolve"


class BenchEventType(str, Enum):
    """评估事件 (借鉴 OpenGame-Bench)"""

    BENCH_BUILD_HEALTH = "bench.build_health"
    BENCH_VISUAL_USABILITY = "bench.visual_usability"
    BENCH_INTENT_ALIGNMENT = "bench.intent_alignment"
    BENCH_RESULT = "bench.result"


class ReportType(str, Enum):
    REPORT_SUMMARY = "report.summary"
    REPORT_BUG_CANDIDATE = "report.bug_candidate"
    REPORT_GATE_RESULT = "report.gate_result"


# 所有事件类型的联合（用于类型提示）
ObsEventType = str  # 实际值为上述枚举的 value


# ─── 框架和项目标识 ────────────────────────────────────────


class Framework(str, Enum):
    VITEST = "vitest"
    GDUNIT4 = "gdunit4"
    PLAYWRIGHT = "playwright"
    AIRTEST = "airtest"
    GODOT_E2E = "godot_e2e"
    GODOT_DRIVER = "godot_driver"
    CUSTOM = "custom"


# ─── 核心数据结构 ──────────────────────────────────────────


@dataclass
class EventSource:
    """事件来源"""

    framework: str  # Framework 枚举值
    project: str
    file: Optional[str] = None
    test_name: Optional[str] = None
    suite: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        d = {
            "framework": self.framework,
            "project": self.project,
        }
        if self.file:
            d["file"] = self.file
        if self.test_name:
            d["test_name"] = self.test_name
        if self.suite:
            d["suite"] = self.suite
        return d


@dataclass
class ObsEvent:
    """统一测试事件"""

    event_id: str
    session_id: str
    timestamp: int  # Unix ms
    source: EventSource
    type: str  # ObsEventType 枚举值
    data: Dict[str, Any]
    parent_event_id: Optional[str] = None
    trace_id: Optional[str] = None
    span_id: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "event_id": self.event_id,
            "session_id": self.session_id,
            "timestamp": self.timestamp,
            "source": self.source.to_dict(),
            "type": self.type,
            "data": self.data,
        }
        if self.parent_event_id:
            d["parent_event_id"] = self.parent_event_id
        if self.trace_id:
            d["trace_id"] = self.trace_id
        if self.span_id:
            d["span_id"] = self.span_id
        return d


@dataclass
class ObsSession:
    """测试会话"""

    session_id: str
    project: str
    framework: str
    started_at: int  # Unix ms
    ended_at: Optional[int] = None
    total_tests: Optional[int] = None
    passed_tests: Optional[int] = None
    failed_tests: Optional[int] = None
    duration_ms: Optional[int] = None
    gate_result: Optional[Dict[str, Any]] = None
    metadata: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "session_id": self.session_id,
            "project": self.project,
            "framework": self.framework,
            "started_at": self.started_at,
        }
        if self.ended_at:
            d["ended_at"] = self.ended_at
        if self.total_tests is not None:
            d["total_tests"] = self.total_tests
        if self.passed_tests is not None:
            d["passed_tests"] = self.passed_tests
        if self.failed_tests is not None:
            d["failed_tests"] = self.failed_tests
        if self.duration_ms is not None:
            d["duration_ms"] = self.duration_ms
        if self.gate_result:
            d["gate_result"] = self.gate_result
        if self.metadata:
            d["metadata"] = self.metadata
        return d


@dataclass
class AnalysisResult:
    """AI 分析结果"""

    analysis_id: str
    session_id: str
    timestamp: int
    analyzer: str
    findings: List[Dict[str, Any]]
    confidence: float  # 0-1
    summary: str
    recommendations: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "analysis_id": self.analysis_id,
            "session_id": self.session_id,
            "timestamp": self.timestamp,
            "analyzer": self.analyzer,
            "findings": self.findings,
            "confidence": self.confidence,
            "summary": self.summary,
            "recommendations": self.recommendations,
        }


# ─── 事件工厂函数 ──────────────────────────────────────────

import time
import uuid


def _generate_id() -> str:
    return str(uuid.uuid4())


def _timestamp() -> int:
    return int(time.time() * 1000)


def create_test_start(
    session_id: str,
    source: EventSource,
    test_name: str,
    full_name: str,
    trace_id: Optional[str] = None,
) -> ObsEvent:
    """创建 test.start 事件"""
    return ObsEvent(
        event_id=_generate_id(),
        session_id=session_id,
        timestamp=_timestamp(),
        source=source,
        type=TestLifecycleType.TEST_START.value,
        data={"test_name": test_name, "full_name": full_name},
        trace_id=trace_id,
    )


def create_test_end(
    session_id: str,
    source: EventSource,
    test_name: str,
    passed: bool,
    duration_ms: int,
    errors: Optional[List[Dict[str, Any]]] = None,
    trace_id: Optional[str] = None,
) -> ObsEvent:
    """创建 test.end / test.fail 事件"""
    return ObsEvent(
        event_id=_generate_id(),
        session_id=session_id,
        timestamp=_timestamp(),
        source=source,
        type=TestLifecycleType.TEST_END.value if passed else TestLifecycleType.TEST_FAIL.value,
        data={
            "test_name": test_name,
            "passed": passed,
            "duration_ms": duration_ms,
            "errors": errors or [],
        },
        trace_id=trace_id,
    )


def create_assertion(
    session_id: str,
    source: EventSource,
    assertion_name: str,
    passed: bool,
    expected: Any = None,
    actual: Any = None,
    message: Optional[str] = None,
    trace_id: Optional[str] = None,
) -> ObsEvent:
    """创建 assert.* 事件"""
    return ObsEvent(
        event_id=_generate_id(),
        session_id=session_id,
        timestamp=_timestamp(),
        source=source,
        type=AssertionType.ASSERT_PASS.value if passed else AssertionType.ASSERT_FAIL.value,
        data={
            "assertion_name": assertion_name,
            "passed": passed,
            "expected": expected,
            "actual": actual,
            "message": message,
        },
        trace_id=trace_id,
    )


def create_agent_tool_call(
    session_id: str,
    source: EventSource,
    tool_name: str,
    tool_input: Any,
    trace_id: Optional[str] = None,
) -> ObsEvent:
    """创建 agent.tool_call 事件"""
    return ObsEvent(
        event_id=_generate_id(),
        session_id=session_id,
        timestamp=_timestamp(),
        source=source,
        type=AgentEventType.AGENT_TOOL_CALL.value,
        data={
            "tool_name": tool_name,
            "input": tool_input,
        },
        trace_id=trace_id,
    )


def create_agent_tool_result(
    session_id: str,
    source: EventSource,
    tool_name: str,
    tool_input: Any,
    tool_output: Any,
    duration_ms: int,
    success: bool,
    error: Optional[str] = None,
    trace_id: Optional[str] = None,
) -> ObsEvent:
    """创建 agent.tool_result 事件"""
    return ObsEvent(
        event_id=_generate_id(),
        session_id=session_id,
        timestamp=_timestamp(),
        source=source,
        type=AgentEventType.AGENT_TOOL_RESULT.value,
        data={
            "tool_name": tool_name,
            "input": tool_input,
            "output": tool_output,
            "duration_ms": duration_ms,
            "success": success,
            "error": error,
        },
        trace_id=trace_id,
    )


def create_game_state_change(
    session_id: str,
    source: EventSource,
    scene_path: str,
    state: Dict[str, Any],
    previous_state: Optional[Dict[str, Any]] = None,
    trace_id: Optional[str] = None,
) -> ObsEvent:
    """创建 game.state_change 事件"""
    return ObsEvent(
        event_id=_generate_id(),
        session_id=session_id,
        timestamp=_timestamp(),
        source=source,
        type=GameEventType.GAME_STATE_CHANGE.value,
        data={
            "scene_path": scene_path,
            "state": state,
            "previous_state": previous_state,
        },
        trace_id=trace_id,
    )


def create_bug_candidate(
    session_id: str,
    source: EventSource,
    severity: str,
    category: str,
    description: str,
    evidence: Dict[str, Any],
    confidence: float = 0.8,
    trace_id: Optional[str] = None,
) -> ObsEvent:
    """创建 report.bug_candidate 事件"""
    return ObsEvent(
        event_id=_generate_id(),
        session_id=session_id,
        timestamp=_timestamp(),
        source=source,
        type=ReportType.REPORT_BUG_CANDIDATE.value,
        data={
            "severity": severity,
            "category": category,
            "description": description,
            "evidence": evidence,
            "confidence": confidence,
        },
        trace_id=trace_id,
    )


def create_gate_result(
    session_id: str,
    source: EventSource,
    verdict: str,
    rules: Dict[str, Any],
    trace_id: Optional[str] = None,
) -> ObsEvent:
    """创建 report.gate_result 事件"""
    return ObsEvent(
        event_id=_generate_id(),
        session_id=session_id,
        timestamp=_timestamp(),
        source=source,
        type=ReportType.REPORT_GATE_RESULT.value,
        data={
            "verdict": verdict,
            "rules": rules,
        },
        trace_id=trace_id,
    )


# ─── Godot 游戏测试事件工厂 ──────────────────────────────────


def create_visual_assertion(
    session_id: str,
    source: EventSource,
    template_name: str,
    matched: bool,
    confidence: float = 0.0,
    position: Optional[List[int]] = None,
    trace_id: Optional[str] = None,
) -> ObsEvent:
    """创建视觉断言事件 (Airtest 风格)"""
    return ObsEvent(
        event_id=_generate_id(),
        session_id=session_id,
        timestamp=_timestamp(),
        source=source,
        type=AssertionType.ASSERT_PASS.value if matched else AssertionType.ASSERT_FAIL.value,
        data={
            "assertion_type": "visual_template",
            "template_name": template_name,
            "matched": matched,
            "confidence": confidence,
            "position": position,
        },
        trace_id=trace_id,
    )


def create_debug_event(
    session_id: str,
    source: EventSource,
    debug_type: str,
    entry_id: Optional[str] = None,
    error_code: Optional[str] = None,
    error_message: Optional[str] = None,
    fix_description: Optional[str] = None,
    trace_id: Optional[str] = None,
) -> ObsEvent:
    """创建调试协议事件 (OpenGame Debug Skill 风格)"""
    return ObsEvent(
        event_id=_generate_id(),
        session_id=session_id,
        timestamp=_timestamp(),
        source=source,
        type=debug_type,
        data={
            "entry_id": entry_id,
            "error_code": error_code,
            "error_message": error_message,
            "fix_description": fix_description,
        },
        trace_id=trace_id,
    )


def create_bench_event(
    session_id: str,
    source: EventSource,
    dimension: str,
    score: float,
    passed: bool,
    checks: Optional[List[Dict[str, Any]]] = None,
    trace_id: Optional[str] = None,
) -> ObsEvent:
    """创建评估事件 (OpenGame-Bench 风格)"""
    return ObsEvent(
        event_id=_generate_id(),
        session_id=session_id,
        timestamp=_timestamp(),
        source=source,
        type=f"bench.{dimension}",
        data={
            "dimension": dimension,
            "score": score,
            "passed": passed,
            "checks": checks or [],
        },
        trace_id=trace_id,
    )
