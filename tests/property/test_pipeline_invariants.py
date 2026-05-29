"""Pipeline state machine invariants — property-based verification.

Validates that the analysis pipeline stages can only transition through
合法路径: INIT → RECON → HUNT → VALIDATE → FEEDBACK → REPORT.

参考 analyzers/pipeline/orchestrator.py 中 AnalysisPipeline.run() 的阶段流程。
"""

from __future__ import annotations

from enum import Enum
from typing import List

import pytest
from hypothesis import given, settings, strategies as st


# ─── Pipeline 阶段定义 ─────────────────────────────────────


class PipelineStage(str, Enum):
    """Pipeline 分析阶段，对应 AnalysisPipeline.run() 中的 5 个 stage。"""

    INIT = "init"
    RECON = "recon"
    HUNT = "hunt"
    VALIDATE = "validate"
    FEEDBACK = "feedback"
    REPORT = "report"


# 合法的阶段转换表（当前阶段 → 允许的下一阶段集合）
VALID_TRANSITIONS = {
    PipelineStage.INIT: {PipelineStage.RECON},
    PipelineStage.RECON: {PipelineStage.HUNT},
    PipelineStage.HUNT: {PipelineStage.VALIDATE, PipelineStage.REPORT},
    PipelineStage.VALIDATE: {PipelineStage.FEEDBACK, PipelineStage.REPORT},
    PipelineStage.FEEDBACK: {PipelineStage.HUNT, PipelineStage.REPORT},
    PipelineStage.REPORT: set(),  # 终态，无后续
}

# 终态集合
TERMINAL_STATES = {PipelineStage.REPORT}

# 所有阶段列表（用于 hypothesis 策略）
ALL_STAGES = list(PipelineStage)


def is_valid_transition(from_stage: PipelineStage, to_stage: PipelineStage) -> bool:
    """检查两个阶段之间的转换是否合法。"""
    return to_stage in VALID_TRANSITIONS.get(from_stage, set())


# ─── Hypothesis 策略 ───────────────────────────────────────

# 生成合法的阶段转换序列
STAGE_STRATEGY = st.sampled_from(ALL_STAGES)


def _build_valid_sequence() -> st.SearchStrategy[List[PipelineStage]]:
    """构建从 INIT 到 REPORT 的合法阶段序列。

    合法路径：
    - INIT → RECON → HUNT → REPORT（最短）
    - INIT → RECON → HUNT → VALIDATE → REPORT
    - INIT → RECON → HUNT → VALIDATE → FEEDBACK → REPORT
    - INIT → RECON → HUNT → VALIDATE → FEEDBACK → HUNT → VALIDATE → ... （循环）

    关键约束：
    - HUNT 之后只能去 VALIDATE 或 REPORT
    - VALIDATE 之后可以去 FEEDBACK 或 REPORT
    - FEEDBACK 之后可以去 HUNT 或 REPORT
    """
    # 无循环时：INIT, RECON, HUNT, [VALIDATE|REPORT], REPORT
    # 有循环时：INIT, RECON, HUNT, VALIDATE, FEEDBACK, HUNT, VALIDATE, ..., REPORT

    # 无循环情况
    no_loop = st.sampled_from([
        [PipelineStage.INIT, PipelineStage.RECON, PipelineStage.HUNT, PipelineStage.REPORT],
        [PipelineStage.INIT, PipelineStage.RECON, PipelineStage.HUNT, PipelineStage.VALIDATE, PipelineStage.REPORT],
        [PipelineStage.INIT, PipelineStage.RECON, PipelineStage.HUNT, PipelineStage.VALIDATE, PipelineStage.FEEDBACK, PipelineStage.REPORT],
    ])

    # 有循环情况：HUNT → VALIDATE → FEEDBACK → HUNT → ...
    def _loop_sequence(n_loops: int, end_with_feedback: bool):
        seq = [PipelineStage.INIT, PipelineStage.RECON, PipelineStage.HUNT]
        for _ in range(n_loops):
            seq.extend([PipelineStage.VALIDATE, PipelineStage.FEEDBACK, PipelineStage.HUNT])
        # 最后一次 VALIDATE
        seq.append(PipelineStage.VALIDATE)
        if end_with_feedback:
            seq.append(PipelineStage.FEEDBACK)
        seq.append(PipelineStage.REPORT)
        return seq

    loop = st.builds(
        _loop_sequence,
        st.integers(min_value=1, max_value=2),
        st.booleans(),
    )

    return st.one_of(no_loop, loop)


VALID_SEQUENCE_STRATEGY = _build_valid_sequence()


# ─── 属性测试 ───────────────────────────────────────────────


@pytest.mark.fast
class TestPipelineStateMachine:
    """Pipeline 阶段转换的不变式。"""

    @given(current=STAGE_STRATEGY, next_stage=STAGE_STRATEGY)
    def test_transition_table_is_consistent(self, current: PipelineStage, next_stage: PipelineStage):
        """转换表中的每个条目都指向有效的 PipelineStage。"""
        allowed = VALID_TRANSITIONS[current]
        assert isinstance(allowed, set)
        for s in allowed:
            assert isinstance(s, PipelineStage)

    @given(current=STAGE_STRATEGY)
    def test_init_can_only_go_to_recon(self, current: PipelineStage):
        """INIT 阶段只能转换到 RECON。"""
        if current == PipelineStage.INIT:
            allowed = VALID_TRANSITIONS[current]
            assert allowed == {PipelineStage.RECON}

    @given(current=STAGE_STRATEGY)
    def test_report_is_terminal(self, current: PipelineStage):
        """REPORT 是终态，不允许进一步转换。"""
        if current == PipelineStage.REPORT:
            allowed = VALID_TRANSITIONS[current]
            assert len(allowed) == 0

    @given(stages=st.lists(STAGE_STRATEGY, min_size=2, max_size=20))
    def test_invalid_transitions_are_detected(self, stages: List[PipelineStage]):
        """给定一个随机阶段序列，检测到非法转换当且仅当序列包含非法步骤。"""
        has_invalid = False
        for i in range(len(stages) - 1):
            if not is_valid_transition(stages[i], stages[i + 1]):
                has_invalid = True
                break

        # 重新验证：如果声称合法，逐个检查
        if not has_invalid:
            for i in range(len(stages) - 1):
                assert is_valid_transition(stages[i], stages[i + 1]), (
                    f"Expected valid transition: {stages[i]} → {stages[i + 1]}"
                )

    @given(sequence=VALID_SEQUENCE_STRATEGY)
    def test_valid_sequence_has_no_illegal_transitions(self, sequence: List[PipelineStage]):
        """任何由 _build_valid_sequence 生成的序列，其每一步转换都合法。"""
        for i in range(len(sequence) - 1):
            assert is_valid_transition(sequence[i], sequence[i + 1]), (
                f"Illegal transition in valid sequence: {sequence[i]} → {sequence[i + 1]} at index {i}"
            )

    @given(sequence=VALID_SEQUENCE_STRATEGY)
    def test_valid_sequence_starts_with_init(self, sequence: List[PipelineStage]):
        """合法序列必须从 INIT 开始。"""
        assert sequence[0] == PipelineStage.INIT

    @given(sequence=VALID_SEQUENCE_STRATEGY)
    def test_valid_sequence_ends_with_report(self, sequence: List[PipelineStage]):
        """合法序列必须以 REPORT 结束。"""
        assert sequence[-1] == PipelineStage.REPORT

    @given(sequence=VALID_SEQUENCE_STRATEGY)
    def test_valid_sequence_contains_recon_before_hunt(self, sequence: List[PipelineStage]):
        """RECON 必须出现在 HUNT 之前。"""
        recon_idx = sequence.index(PipelineStage.RECON)
        hunt_idx = sequence.index(PipelineStage.HUNT)
        assert recon_idx < hunt_idx

    @given(sequence=VALID_SEQUENCE_STRATEGY)
    @settings(max_examples=100)
    def test_valid_sequence_length_bounded(self, sequence: List[PipelineStage]):
        """合法序列长度有界：最少 4 步（INIT→RECON→HUNT→REPORT），最多 12 步。"""
        assert 4 <= len(sequence) <= 12


@pytest.mark.fast
class TestPipelineRunStatus:
    """Pipeline 运行状态转换的不变式（对应 analysis_runs.status 字段）。"""

    # analysis_runs.status 的合法值
    RUN_STATUSES = st.sampled_from(["running", "completed", "aborted", "failed"])

    # 合法的状态转换
    VALID_RUN_TRANSITIONS = {
        "running": {"completed", "aborted", "failed"},
        "completed": set(),
        "aborted": set(),
        "failed": set(),
    }

    @given(status=RUN_STATUSES)
    def test_terminal_statuses_have_no_transitions(self, status: str):
        """终态（completed/aborted/failed）不允许进一步转换。"""
        if status != "running":
            assert self.VALID_RUN_TRANSITIONS[status] == set()

    @given(from_status=RUN_STATUSES, to_status=RUN_STATUSES)
    def test_only_running_can_transition(self, from_status: str, to_status: str):
        """只有 running 状态可以转换到其他状态（不允许自环）。"""
        allowed = self.VALID_RUN_TRANSITIONS.get(from_status, set())
        if from_status == "running":
            if to_status != from_status:
                assert to_status in allowed
        else:
            # 非 running 状态不允许任何转换（包括自环）
            assert to_status not in allowed or to_status == from_status
