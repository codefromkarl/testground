"""Event sequence causality — property-based verification.

Validates that event sequences respect causal ordering:
- test.end / test.fail / test.skip must have a preceding test.start
- assert.pass / assert.fail must occur within a started test
- 事件序列的因果律是 ObsEvent 模型的核心约束

参考 schema/events.py 中的事件类型定义。
"""

from __future__ import annotations

from typing import Dict, List, Set

import pytest
from hypothesis import given, settings, strategies as st


# ─── Hypothesis 策略 ───────────────────────────────────────

# 测试名称策略
TEST_NAMES = st.text(alphabet="abcdefghijklmnopqrstuvwxyz_", min_size=1, max_size=16)

# 事件类型策略：仅 test 生命周期
TEST_LIFECYCLE_TYPES = st.sampled_from(["test.start", "test.end", "test.fail", "test.skip"])

# 事件类型策略：仅断言类型
ASSERTION_TYPES = st.sampled_from(["assert.pass", "assert.fail"])

# 混合事件类型策略（包含 test 生命周期和断言）
CAUSAL_EVENT_TYPES = st.sampled_from([
    "test.start", "test.end", "test.fail", "test.skip",
    "assert.pass", "assert.fail",
])

# 带测试名称的事件策略
CAUSAL_EVENT = st.fixed_dictionaries({
    "type": CAUSAL_EVENT_TYPES,
    "test_name": TEST_NAMES,
})


# ─── 因果律验证辅助函数 ────────────────────────────────────


def check_causality(events: List[Dict]) -> Dict[str, bool]:
    """检查事件序列的因果律，返回违规报告。"""
    started_tests: Set[str] = set()
    violations = {
        "end_without_start": False,
        "fail_without_start": False,
        "skip_without_start": False,
        "assert_without_start": False,
    }

    for event in events:
        event_type = event["type"]
        test_name = event["test_name"]

        if event_type == "test.start":
            started_tests.add(test_name)

        elif event_type == "test.end":
            if test_name not in started_tests:
                violations["end_without_start"] = True

        elif event_type == "test.fail":
            if test_name not in started_tests:
                violations["fail_without_start"] = True

        elif event_type == "test.skip":
            if test_name not in started_tests:
                violations["skip_without_start"] = True

        elif event_type in ("assert.pass", "assert.fail"):
            if test_name not in started_tests:
                violations["assert_without_start"] = True

    return violations


# ─── 属性测试 ───────────────────────────────────────────────


@pytest.mark.fast
class TestEventCausality:
    """事件序列因果律的不变式。"""

    @given(events=st.lists(CAUSAL_EVENT, min_size=0, max_size=30))
    def test_test_end_requires_start(self, events: List[Dict]):
        """test.end 事件之前必须有对应的 test.start。"""
        violations = check_causality(events)
        # 如果序列中有 test.end 但没有对应的 test.start，应该检测到
        started: Set[str] = set()
        for e in events:
            if e["type"] == "test.start":
                started.add(e["test_name"])
            elif e["type"] == "test.end":
                if e["test_name"] not in started:
                    assert violations["end_without_start"], (
                        f"test.end for '{e['test_name']}' without test.start should be detected"
                    )
                    return
        # 如果所有 test.end 都有对应的 start，不应该有违规
        assert not violations["end_without_start"]

    @given(events=st.lists(CAUSAL_EVENT, min_size=0, max_size=30))
    def test_test_fail_requires_start(self, events: List[Dict]):
        """test.fail 事件之前必须有对应的 test.start。"""
        violations = check_causality(events)
        started: Set[str] = set()
        for e in events:
            if e["type"] == "test.start":
                started.add(e["test_name"])
            elif e["type"] == "test.fail":
                if e["test_name"] not in started:
                    assert violations["fail_without_start"]
                    return
        assert not violations["fail_without_start"]

    @given(events=st.lists(CAUSAL_EVENT, min_size=0, max_size=30))
    def test_test_skip_requires_start(self, events: List[Dict]):
        """test.skip 事件之前必须有对应的 test.start。"""
        violations = check_causality(events)
        started: Set[str] = set()
        for e in events:
            if e["type"] == "test.start":
                started.add(e["test_name"])
            elif e["type"] == "test.skip":
                if e["test_name"] not in started:
                    assert violations["skip_without_start"]
                    return
        assert not violations["skip_without_start"]

    @given(events=st.lists(CAUSAL_EVENT, min_size=0, max_size=30))
    def test_assert_requires_test_start(self, events: List[Dict]):
        """assert.pass / assert.fail 必须在 test.start 之后。"""
        violations = check_causality(events)
        started: Set[str] = set()
        for e in events:
            if e["type"] == "test.start":
                started.add(e["test_name"])
            elif e["type"] in ("assert.pass", "assert.fail"):
                if e["test_name"] not in started:
                    assert violations["assert_without_start"]
                    return
        assert not violations["assert_without_start"]

    @given(events=st.lists(CAUSAL_EVENT, min_size=1, max_size=30))
    def test_all_causality_respects_ordering(self, events: List[Dict]):
        """综合因果律：所有终止/断言事件都必须有对应的 start 前驱。"""
        violations = check_causality(events)
        started: Set[str] = set()
        any_violation = False

        for e in events:
            if e["type"] == "test.start":
                started.add(e["test_name"])
            elif e["type"] in ("test.end", "test.fail", "test.skip"):
                if e["test_name"] not in started:
                    any_violation = True
                    break
            elif e["type"] in ("assert.pass", "assert.fail"):
                if e["test_name"] not in started:
                    any_violation = True
                    break

        has_any_violation = any(violations.values())
        assert has_any_violation == any_violation, (
            f"check_causality mismatch: reported={violations}, inline={any_violation}"
        )

    @given(
        test_names=st.lists(TEST_NAMES, min_size=1, max_size=5, unique=True),
        data=st.data(),
    )
    def test_causal_sequence_is_violation_free(self, test_names: List[str], data):
        """构造一个保证因果律的序列，验证 check_causality 不报告违规。"""
        events: List[Dict] = []

        # 为每个测试名生成因果正确的事件序列
        for name in test_names:
            events.append({"type": "test.start", "test_name": name})
            # 可选：添加断言
            if data.draw(st.booleans()):
                events.append({"type": "assert.pass", "test_name": name})
            if data.draw(st.booleans()):
                events.append({"type": "assert.fail", "test_name": name})
            # 结束事件
            end_type = data.draw(st.sampled_from(["test.end", "test.fail"]))
            events.append({"type": end_type, "test_name": name})

        violations = check_causality(events)
        assert not any(violations.values()), (
            f"Causal sequence should have no violations, got: {violations}"
        )

    @given(events=st.lists(CAUSAL_EVENT, min_size=0, max_size=50))
    @settings(max_examples=200)
    def test_violation_detection_is_sound(self, events: List[Dict]):
        """验证 check_causality 的结果与逐事件检查一致（健全性检查）。"""
        reported = check_causality(events)

        # 手动逐事件检查
        started: Set[str] = set()
        manual = {
            "end_without_start": False,
            "fail_without_start": False,
            "skip_without_start": False,
            "assert_without_start": False,
        }

        for e in events:
            t = e["type"]
            n = e["test_name"]
            if t == "test.start":
                started.add(n)
            elif t == "test.end" and n not in started:
                manual["end_without_start"] = True
            elif t == "test.fail" and n not in started:
                manual["fail_without_start"] = True
            elif t == "test.skip" and n not in started:
                manual["skip_without_start"] = True
            elif t in ("assert.pass", "assert.fail") and n not in started:
                manual["assert_without_start"] = True

        assert reported == manual, f"Mismatch: reported={reported}, manual={manual}"


@pytest.mark.fast
class TestEventCausalityEdgeCases:
    """边界情况的因果律测试。"""

    @given(name=TEST_NAMES)
    def test_start_then_end_is_valid(self, name: str):
        """单个 start → end 是合法的。"""
        events = [
            {"type": "test.start", "test_name": name},
            {"type": "test.end", "test_name": name},
        ]
        violations = check_causality(events)
        assert not any(violations.values())

    @given(name=TEST_NAMES)
    def test_end_alone_is_violation(self, name: str):
        """孤立的 test.end 是违规的。"""
        events = [{"type": "test.end", "test_name": name}]
        violations = check_causality(events)
        assert violations["end_without_start"]

    @given(name=TEST_NAMES)
    def test_assert_alone_is_violation(self, name: str):
        """孤立的 assert.pass 是违规的。"""
        events = [{"type": "assert.pass", "test_name": name}]
        violations = check_causality(events)
        assert violations["assert_without_start"]

    @given(names=st.lists(TEST_NAMES, min_size=2, max_size=10, unique=True))
    def test_multiple_tests_independent_causality(self, names: List[str]):
        """多个测试的因果律是独立的。"""
        events: List[Dict] = []
        # 只为第一个测试创建 start
        events.append({"type": "test.start", "test_name": names[0]})
        events.append({"type": "test.end", "test_name": names[0]})
        # 其他测试没有 start
        for name in names[1:]:
            events.append({"type": "test.end", "test_name": name})

        violations = check_causality(events)
        # 第一个测试不违规，其余违规
        assert not violations["end_without_start"] if len(names) == 1 else violations["end_without_start"]
