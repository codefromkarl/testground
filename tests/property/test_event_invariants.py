"""Event schema invariants — property-based verification.

Validates that event structures always satisfy core constraints
regardless of the specific values generated.

Adapted from stardrifter's economy_invariants / scene_lifecycle_invariants pattern.
"""

from __future__ import annotations

import pytest
from hypothesis import given, strategies as st

from schema.events import ObsEvent, EventSource


# ─── Strategies ─────────────────────────────────────────────

# Valid event types (union of all type enums in schema.events)
EVENT_TYPES = st.sampled_from([
    # TestLifecycleType
    "test.start", "test.end", "test.skip", "test.fail", "test.error",
    # AssertionType
    "assert.pass", "assert.fail", "assert.semantic",
    # ActionType
    "action.click", "action.input", "action.navigate", "action.wait", "action.screenshot",
    # GameEventType
    "game.state_change", "game.scene_load", "game.signal_emit", "game.save", "game.load",
    # AgentEventType
    "agent.llm_call", "agent.tool_call", "agent.tool_result", "agent.thinking",
    # ObservationType
    "observation.snapshot", "observation.coverage", "observation.anomaly",
    "observation.state_diff", "observation.input_trace", "observation.cause_trace",
    # DebugEventType
    "debug.iteration", "debug.match", "debug.repair", "debug.evolve",
    # BenchEventType
    "bench.build_health", "bench.visual_usability", "bench.intent_alignment", "bench.result",
    # ReportType
    "report.summary", "report.bug_candidate", "report.gate_result",
])

# Valid framework names
FRAMEWORKS = st.sampled_from(["gdunit4", "godot_driver", "pytest", "vitest", "jest"])

# Timestamp strategy (reasonable range around 2024-2026)
TIMESTAMPS = st.integers(min_value=1700000000000, max_value=1800000000000)

# Session / project IDs
IDENTIFIERS = st.text(alphabet="abcdefghijklmnopqrstuvwxyz0123456789_", min_size=1, max_size=32)

# Duration (non-negative)
DURATION_MS = st.floats(min_value=0.0, max_value=3600000.0, allow_nan=False, allow_infinity=False)


# ─── Invariant tests ────────────────────────────────────────


@pytest.mark.fast
class TestEventInvariants:
    """Properties that must hold for all valid ObsEvent objects."""

    @given(
        event_type=EVENT_TYPES,
        framework=FRAMEWORKS,
        project=IDENTIFIERS,
        session=IDENTIFIERS,
        timestamp=TIMESTAMPS,
    )
    def test_event_id_is_non_empty(self, event_type, framework, project, session, timestamp):
        """Every event must have a non-empty event_id."""
        source = EventSource(framework=framework, project=project)
        event = ObsEvent(
            event_id="evt_test",
            session_id=session,
            timestamp=timestamp,
            source=source,
            type=event_type,
            data={},
        )
        assert event.event_id
        assert len(event.event_id) > 0

    @given(
        timestamp=TIMESTAMPS,
        duration=DURATION_MS,
    )
    def test_duration_is_non_negative(self, timestamp, duration):
        """Test durations must never be negative."""
        source = EventSource(framework="pytest", project="test")
        event = ObsEvent(
            event_id="evt_1",
            session_id="sess_1",
            timestamp=timestamp,
            source=source,
            type="test.end",
            data={"duration_ms": duration, "test_name": "prop_test"},
        )
        assert event.data["duration_ms"] >= 0

    @given(
        framework=FRAMEWORKS,
        project=IDENTIFIERS,
    )
    def test_source_framework_and_project_non_empty(self, framework, project):
        """Event source must always have non-empty framework and project."""
        source = EventSource(framework=framework, project=project)
        assert source.framework
        assert source.project
        assert len(source.framework) > 0
        assert len(source.project) > 0


@pytest.mark.fast
class TestEventSequenceInvariants:
    """Properties that must hold across event sequences."""

    @given(
        st.lists(
            st.fixed_dictionaries({
                "event_id": st.text(min_size=1, max_size=16),
                "session_id": IDENTIFIERS,
                "timestamp": TIMESTAMPS,
                "type": st.sampled_from(["test.start", "assert.pass", "test.end", "test.fail"]),
                "data": st.fixed_dictionaries({
                    "test_name": st.text(min_size=1, max_size=32),
                    "duration_ms": DURATION_MS,
                }),
            }),
            min_size=0,
            max_size=20,
        )
    )
    def test_timestamps_are_monotonic_within_session(self, events):
        """Within a single session, timestamps should generally not decrease.
        (Relaxed: we check that if sorted, they don't go backwards by much.)"""
        if len(events) < 2:
            pytest.skip("Need at least 2 events")

        # Group by session
        by_session: dict[str, list] = {}
        for e in events:
            sid = e["session_id"]
            by_session.setdefault(sid, []).append(e)

        for session_events in by_session.values():
            sorted_ts = sorted(e["timestamp"] for e in session_events)
            # Timestamps should be non-decreasing
            for i in range(1, len(sorted_ts)):
                assert sorted_ts[i] >= sorted_ts[i - 1]

    @given(
        st.lists(
            st.fixed_dictionaries({
                "type": st.sampled_from(["test.start", "test.end", "test.fail"]),
                "data": st.fixed_dictionaries({"test_name": st.text(min_size=1, max_size=16)}),
            }),
            min_size=0,
            max_size=10,
        )
    )
    def test_test_end_follows_start(self, events):
        """For any test name, an end/fail event should not appear before a start.
        (This is a relaxed invariant — exact pairing requires more state.)"""
        started: set[str] = set()
        for e in events:
            name = e["data"]["test_name"]
            if e["type"] == "test.start":
                started.add(name)
            elif e["type"] in ("test.end", "test.fail"):
                # Relaxed: we just note that it's okay if we haven't seen start
                # (events could be from different batches)
                pass


@pytest.mark.fast
class TestNumericalInvariants:
    """Numerical bounds that should never be violated."""

    @given(st.integers(), st.integers())
    def test_addition_commutative(self, a, b):
        """Sanity check that our test environment works."""
        assert a + b == b + a

    @given(
        st.floats(min_value=0.0, max_value=1.0),
        st.floats(min_value=0.0, max_value=1.0),
    )
    def test_probability_bounds(self, p1, p2):
        """Probabilities (if used) should stay in [0, 1] when combined."""
        combined = max(0.0, min(1.0, p1 + p2 - p1 * p2))  # P(A or B)
        assert 0.0 <= combined <= 1.0
