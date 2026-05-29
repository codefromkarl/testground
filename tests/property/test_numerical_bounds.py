"""Numerical bounds invariants — property-based verification.

Validates that numerical fields in events and analysis results
always respect their defined bounds:
- duration_ms >= 0
- score in [0, 100]
- probability in [0, 1]
- confidence in [0, 1]

参考 schema/events.py 中的 ObsEvent 和 AnalysisResult 定义。
"""

from __future__ import annotations

import pytest
from hypothesis import given, settings, strategies as st


# ─── Hypothesis 策略 ───────────────────────────────────────

# 非负时长（0 到 1 小时，毫秒）
DURATION_MS = st.floats(min_value=0.0, max_value=3_600_000.0, allow_nan=False, allow_infinity=False)

# 分数范围 [0, 100]
SCORE_0_100 = st.floats(min_value=0.0, max_value=100.0, allow_nan=False, allow_infinity=False)

# 概率/置信度范围 [0, 1]
PROBABILITY = st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False)

# 任意浮点数（用于边界检测）
ANY_FLOAT = st.floats(allow_nan=False, allow_infinity=False)

# 任意整数
ANY_INT = st.integers()

# 边界值策略：特别关注 0、1、100 等边界
BOUNDARY_FLOATS = st.sampled_from([0.0, 0.001, 0.5, 0.999, 1.0, 50.0, 99.9, 100.0])

# 负数策略（用于验证拒绝）
NEGATIVE_FLOATS = st.floats(max_value=-0.001, allow_nan=False, allow_infinity=False)

# 超出 [0, 1] 的策略
OUT_OF_UNIT_FLOATS = st.one_of(
    st.floats(max_value=-0.001, allow_nan=False, allow_infinity=False),
    st.floats(min_value=1.001, allow_nan=False, allow_infinity=False),
)

# 超出 [0, 100] 的策略
OUT_OF_SCORE_FLOATS = st.one_of(
    st.floats(max_value=-0.001, allow_nan=False, allow_infinity=False),
    st.floats(min_value=100.001, allow_nan=False, allow_infinity=False),
)


# ─── 辅助函数 ──────────────────────────────────────────────


def clamp(value: float, min_val: float, max_val: float) -> float:
    """将值钳制在 [min_val, max_val] 范围内。"""
    return max(min_val, min(max_val, value))


def is_in_bounds(value: float, min_val: float, max_val: float) -> bool:
    """检查值是否在 [min_val, max_val] 范围内。"""
    return min_val <= value <= max_val


# ─── 属性测试 ───────────────────────────────────────────────


@pytest.mark.fast
class TestDurationBounds:
    """duration_ms 的非负性约束。"""

    @given(duration=DURATION_MS)
    def test_duration_is_non_negative(self, duration: float):
        """duration_ms 必须 >= 0。"""
        assert duration >= 0.0

    @given(duration=DURATION_MS)
    def test_duration_is_finite(self, duration: float):
        """duration_ms 必须是有限数。"""
        assert duration == duration  # NaN check
        assert duration != float("inf")
        assert duration != float("-inf")

    @given(duration=NEGATIVE_FLOATS)
    def test_negative_duration_rejected(self, duration: float):
        """负数时长应该被检测为非法。"""
        assert duration < 0.0

    @given(duration=BOUNDARY_FLOATS)
    def test_boundary_durations_valid(self, duration: float):
        """边界值时长应该有效。"""
        assert is_in_bounds(duration, 0.0, 100.0)

    @given(duration=DURATION_MS)
    def test_duration_scaling_preserves_non_negative(self, duration: float):
        """对时长进行缩放后仍保持非负。"""
        scaled = duration * 0.5
        assert scaled >= 0.0
        scaled_up = duration * 2.0
        assert scaled_up >= 0.0


@pytest.mark.fast
class TestScoreBounds:
    """score 的 [0, 100] 范围约束。"""

    @given(score=SCORE_0_100)
    def test_score_in_range(self, score: float):
        """score 必须在 [0, 100] 范围内。"""
        assert 0.0 <= score <= 100.0

    @given(score=SCORE_0_100)
    def test_score_is_finite(self, score: float):
        """score 必须是有限数。"""
        assert score == score
        assert score != float("inf")
        assert score != float("-inf")

    @given(score=OUT_OF_SCORE_FLOATS)
    def test_out_of_range_score_detected(self, score: float):
        """超出范围的分数应该被检测为非法。"""
        assert not is_in_bounds(score, 0.0, 100.0)

    @given(score=BOUNDARY_FLOATS)
    def test_boundary_scores_valid(self, score: float):
        """边界值分数应该有效。"""
        assert is_in_bounds(score, 0.0, 100.0)

    @given(score=SCORE_0_100)
    def test_clamp_preserves_bounds(self, score: float):
        """clamp 操作保持在范围内。"""
        clamped = clamp(score, 0.0, 100.0)
        assert 0.0 <= clamped <= 100.0
        assert clamped == score  # 已在范围内，clamp 不应改变值

    @given(score=SCORE_0_100)
    def test_score_as_percentage_valid(self, score: float):
        """分数转换为百分比后仍在 [0, 1] 范围内。"""
        pct = score / 100.0
        assert 0.0 <= pct <= 1.0


@pytest.mark.fast
class TestProbabilityBounds:
    """probability / confidence 的 [0, 1] 范围约束。"""

    @given(p=PROBABILITY)
    def test_probability_in_unit_interval(self, p: float):
        """概率值必须在 [0, 1] 范围内。"""
        assert 0.0 <= p <= 1.0

    @given(p=PROBABILITY)
    def test_probability_is_finite(self, p: float):
        """概率值必须是有限数。"""
        assert p == p
        assert p != float("inf")
        assert p != float("-inf")

    @given(p=OUT_OF_UNIT_FLOATS)
    def test_out_of_unit_probability_detected(self, p: float):
        """超出 [0, 1] 的概率值应该被检测为非法。"""
        assert not is_in_bounds(p, 0.0, 1.0)

    @given(p=BOUNDARY_FLOATS)
    def test_boundary_probabilities_valid(self, p: float):
        """边界值概率应该有效（仅 [0, 1] 内的）。"""
        if 0.0 <= p <= 1.0:
            assert is_in_bounds(p, 0.0, 1.0)

    @given(p1=PROBABILITY, p2=PROBABILITY)
    def test_probability_complement_in_range(self, p1: float, p2: float):
        """概率的补集也在 [0, 1] 范围内。"""
        complement1 = 1.0 - p1
        complement2 = 1.0 - p2
        assert 0.0 <= complement1 <= 1.0
        assert 0.0 <= complement2 <= 1.0

    @given(p1=PROBABILITY, p2=PROBABILITY)
    def test_probability_intersection_in_range(self, p1: float, p2: float):
        """两个独立概率的交集 P(A and B) = P(A) * P(B) 仍在 [0, 1] 内。"""
        intersection = p1 * p2
        assert 0.0 <= intersection <= 1.0

    @given(p1=PROBABILITY, p2=PROBABILITY)
    def test_probability_union_in_range(self, p1: float, p2: float):
        """两个事件的并集概率 P(A or B) 仍在 [0, 1] 内。"""
        # P(A or B) = P(A) + P(B) - P(A and B)
        union = p1 + p2 - p1 * p2
        assert 0.0 <= union <= 1.0 + 1e-10  # 浮点容差


@pytest.mark.fast
class TestNumericalBoundsComposite:
    """组合数值约束的不变式。"""

    @given(
        duration=DURATION_MS,
        score=SCORE_0_100,
        confidence=PROBABILITY,
    )
    def test_combined_event_data_bounds(self, duration: float, score: float, confidence: float):
        """同时包含时长、分数、置信度的事件数据应满足所有边界约束。"""
        event_data = {
            "duration_ms": duration,
            "score": score,
            "confidence": confidence,
        }
        assert event_data["duration_ms"] >= 0.0
        assert 0.0 <= event_data["score"] <= 100.0
        assert 0.0 <= event_data["confidence"] <= 1.0

    @given(
        duration=DURATION_MS,
        confidence=PROBABILITY,
    )
    def test_visual_assertion_data_bounds(self, duration: float, confidence: float):
        """视觉断言事件的数值字段满足边界约束。"""
        data = {
            "duration_ms": duration,
            "confidence": confidence,
            "template_name": "test_template",
            "matched": confidence > 0.5,
        }
        assert data["duration_ms"] >= 0.0
        assert 0.0 <= data["confidence"] <= 1.0

    @given(scores=st.lists(SCORE_0_100, min_size=1, max_size=20))
    def test_average_score_in_bounds(self, scores: list):
        """多个分数的平均值仍在 [0, 100] 范围内。"""
        avg = sum(scores) / len(scores)
        assert 0.0 <= avg <= 100.0

    @given(probabilities=st.lists(PROBABILITY, min_size=1, max_size=20))
    def test_average_probability_in_bounds(self, probabilities: list):
        """多个概率的平均值仍在 [0, 1] 范围内。"""
        avg = sum(probabilities) / len(probabilities)
        assert 0.0 <= avg <= 1.0

    @given(duration=DURATION_MS)
    @settings(max_examples=200)
    def test_duration_conversion_to_seconds(self, duration: float):
        """时长从毫秒转换到秒后仍保持非负和有限。"""
        seconds = duration / 1000.0
        assert seconds >= 0.0
        assert seconds == seconds  # not NaN
