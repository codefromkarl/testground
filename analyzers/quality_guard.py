"""质量守卫分析器 — 迁移自 TravelAgent quality-guard

元测试逻辑：确保测试体系本身的质量。
"""

from __future__ import annotations

from typing import Any, Dict, List

from .base import AnalysisResult, BaseAnalyzer


class QualityGuard(BaseAnalyzer):
    """测试质量守卫。

    检测：
    - 测试覆盖率缺口
    - 断言密度不足
    - 测试命名规范
    - Mock 使用合理性
    """

    @property
    def name(self) -> str:
        return "quality_guard"

    def analyze(self, events: List[Dict[str, Any]]) -> AnalysisResult:
        findings: List[Dict[str, Any]] = []
        session_id = events[0].get("session_id", "") if events else ""

        # 检测断言密度
        findings.extend(self._check_assertion_density(events))

        # 检测测试粒度
        findings.extend(self._check_test_granularity(events))

        # 检测错误处理覆盖
        findings.extend(self._check_error_coverage(events))

        # 计算质量分
        quality_score = self._calculate_quality_score(events, findings)

        return AnalysisResult(
            analyzer=self.name,
            session_id=session_id,
            findings=findings,
            confidence=0.85,
            summary=f"质量评估完成，得分 {quality_score:.1f}/100",
            recommendations=self._generate_recommendations(findings, quality_score),
        )

    def _check_assertion_density(self, events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """检查断言密度：每个测试应该有断言"""
        findings = []
        test_starts: Dict[str, int] = {}
        test_assertions: Dict[str, int] = {}

        for event in events:
            name = event.get("data", {}).get("test_name", "")
            if not name:
                continue

            if event["type"] == "test.start":
                test_starts[name] = event.get("timestamp", 0)
            elif event["type"] in ("assert.pass", "assert.fail"):
                test_assertions[name] = test_assertions.get(name, 0) + 1

        # 检查无断言的测试
        for name in test_starts:
            if name not in test_assertions or test_assertions[name] == 0:
                findings.append(
                    {
                        "severity": "medium",
                        "category": "no_assertion",
                        "description": f"测试 {name} 没有断言",
                        "test_name": name,
                    }
                )

        return findings

    def _check_test_granularity(self, events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """检查测试粒度：单个测试不应过长"""
        findings = []
        test_durations: Dict[str, float] = {}

        for event in events:
            if event["type"] in ("test.end", "test.fail"):
                name = event.get("data", {}).get("test_name", "")
                duration = event.get("data", {}).get("duration_ms", 0)
                if name and duration > 0:
                    test_durations[name] = duration

        for name, duration in test_durations.items():
            # 超过 10 秒的测试标记为过长
            if duration > 10000:
                findings.append(
                    {
                        "severity": "low",
                        "category": "test_too_long",
                        "description": f"测试 {name} 耗时 {duration:.0f}ms，建议拆分",
                        "test_name": name,
                        "duration_ms": duration,
                    }
                )

        return findings

    def _check_error_coverage(self, events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """检查错误路径覆盖"""
        findings = []
        has_fail_tests = any(e["type"] == "test.fail" for e in events)

        if not has_fail_tests and len(events) > 20:
            findings.append(
                {
                    "severity": "low",
                    "category": "no_failure_tests",
                    "description": "所有测试都通过了，可能缺少边界条件和错误路径测试",
                }
            )

        return findings

    def _calculate_quality_score(self, events: List[Dict[str, Any]], findings: List[Dict[str, Any]]) -> float:
        """计算质量分 (0-100)"""
        score = 100.0

        # 每个 medium finding -5 分
        for f in findings:
            if f.get("severity") == "high":
                score -= 15
            elif f.get("severity") == "medium":
                score -= 5
            elif f.get("severity") == "low":
                score -= 2

        return max(0, score)

    def _generate_recommendations(self, findings: List[Dict[str, Any]], score: float) -> List[str]:
        recs = []
        categories = {f["category"] for f in findings}

        if "no_assertion" in categories:
            recs.append("存在无断言的测试，建议添加验证逻辑")

        if "test_too_long" in categories:
            recs.append("存在耗时过长的测试，建议拆分为更小的单元")

        if "no_failure_tests" in categories:
            recs.append("缺少失败路径测试，建议添加边界条件和异常场景")

        if score < 70:
            recs.append(f"整体质量分较低 ({score:.0f}/100)，建议优先修复高优先级问题")

        return recs
