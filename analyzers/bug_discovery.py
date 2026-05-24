"""Bug 发现分析器 — 从 episode trace 中检测异常

迁移自 loopexpedition/scripts/ai_testing/bug_discovery.py，
适配统一事件格式。
"""

from __future__ import annotations

from typing import Any, Dict, List

from .base import AnalysisResult, BaseAnalyzer


class BugDiscoveryAnalyzer(BaseAnalyzer):
    """基于测试事件的异常检测。

    检测：
    - 连续无进展步骤（卡住状态）
    - 异常奖励/数值偏差
    - 未覆盖的测试路径
    - 断言失败模式
    """

    def __init__(
        self,
        max_steps_without_progress: int = 8,
        anomaly_threshold: float = 3.0,
    ) -> None:
        self.max_steps_without_progress = max_steps_without_progress
        self.anomaly_threshold = anomaly_threshold

    @property
    def name(self) -> str:
        return "bug_discovery"

    def analyze(self, events: List[Dict[str, Any]]) -> AnalysisResult:
        findings: List[Dict[str, Any]] = []

        # 检测连续失败
        findings.extend(self._detect_failure_streaks(events))

        # 检测异常长测试
        findings.extend(self._detect_slow_tests(events))

        # 检测重复失败模式
        findings.extend(self._detect_repeated_failures(events))

        # 检测未完成的测试
        findings.extend(self._detect_incomplete_tests(events))

        confidence = min(1.0, len(events) / 50)  # 事件越多越有信心
        session_id = events[0].get("session_id", "") if events else ""

        return AnalysisResult(
            analyzer=self.name,
            session_id=session_id,
            findings=findings,
            confidence=confidence,
            summary=f"分析了 {len(events)} 个事件，发现 {len(findings)} 个潜在问题",
            recommendations=self._generate_recommendations(findings),
        )

    def _detect_failure_streaks(self, events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """检测连续失败"""
        findings = []
        streak = 0
        streak_start = None

        for event in events:
            if event["type"] == "test.fail":
                if streak == 0:
                    streak_start = event.get("data", {}).get("test_name", "unknown")
                streak += 1
            else:
                if streak >= 3:
                    findings.append({
                        "severity": "high",
                        "category": "failure_streak",
                        "description": f"连续 {streak} 个测试失败，起始于 {streak_start}",
                        "streak_length": streak,
                        "start_test": streak_start,
                    })
                streak = 0

        # 检查末尾的连续失败
        if streak >= 3:
            findings.append({
                "severity": "high",
                "category": "failure_streak",
                "description": f"末尾连续 {streak} 个测试失败",
                "streak_length": streak,
            })

        return findings

    def _detect_slow_tests(self, events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """检测异常慢的测试"""
        findings = []
        durations: List[float] = []

        for event in events:
            if event["type"] in ("test.end", "test.fail"):
                duration = event.get("data", {}).get("duration_ms", 0)
                if duration > 0:
                    durations.append(duration)

        if not durations:
            return findings

        avg = sum(durations) / len(durations)
        threshold = max(avg * self.anomaly_threshold, 5000)  # 至少 5 秒

        for event in events:
            if event["type"] in ("test.end", "test.fail"):
                duration = event.get("data", {}).get("duration_ms", 0)
                if duration > threshold and duration > 0:  # 排除 duration=0
                    findings.append({
                        "severity": "medium",
                        "category": "slow_test",
                        "description": f"测试 {event.get('data', {}).get('test_name', 'unknown')} 耗时 {duration:.0f}ms（平均 {avg:.0f}ms）",
                        "test_name": event.get("data", {}).get("test_name"),
                        "duration_ms": duration,
                        "avg_duration_ms": avg,
                    })

        return findings

    def _detect_repeated_failures(self, events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """检测同一测试重复失败"""
        findings = []
        failure_counts: Dict[str, int] = {}

        for event in events:
            if event["type"] == "test.fail":
                name = event.get("data", {}).get("test_name", "unknown")
                failure_counts[name] = failure_counts.get(name, 0) + 1

        for name, count in failure_counts.items():
            if count >= 2:
                findings.append({
                    "severity": "high" if count >= 3 else "medium",
                    "category": "repeated_failure",
                    "description": f"测试 {name} 失败了 {count} 次",
                    "test_name": name,
                    "failure_count": count,
                })

        return findings

    def _detect_incomplete_tests(self, events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """检测开始但未结束的测试"""
        findings = []
        started = set()
        ended = set()

        for event in events:
            name = event.get("data", {}).get("test_name")
            if not name:
                continue
            if event["type"] == "test.start":
                started.add(name)
            elif event["type"] in ("test.end", "test.fail", "test.skip"):
                ended.add(name)

        incomplete = started - ended
        for name in incomplete:
            findings.append({
                "severity": "high",
                "category": "incomplete_test",
                "description": f"测试 {name} 开始但未结束（可能崩溃或超时）",
                "test_name": name,
            })

        return findings

    def _generate_recommendations(self, findings: List[Dict[str, Any]]) -> List[str]:
        """根据发现生成建议"""
        recs = []
        categories = {f["category"] for f in findings}

        if "failure_streak" in categories:
            recs.append("存在连续失败，建议检查环境或最近的代码变更")

        if "slow_test" in categories:
            recs.append("存在慢测试，建议检查是否有不必要的等待或外部调用")

        if "repeated_failure" in categories:
            recs.append("存在重复失败的测试，建议优先修复以提高 CI 稳定性")

        if "incomplete_test" in categories:
            recs.append("存在未完成的测试，建议检查超时设置和资源清理")

        return recs
