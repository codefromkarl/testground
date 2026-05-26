"""跨项目异常检测分析器

对比多个项目的测试事件，发现共性问题和异常模式。"""

from __future__ import annotations

from typing import Any, Dict, List

from .base import AnalysisResult, BaseAnalyzer


class AnomalyDetector(BaseAnalyzer):
    """跨项目异常检测。

    分析多个项目的测试事件，检测：
    - 跨项目同时出现的失败模式
    - 测试通过率突降
    - 异常的事件分布
    """

    @property
    def name(self) -> str:
        return "anomaly_detector"

    def analyze(self, events: List[Dict[str, Any]]) -> AnalysisResult:
        findings: List[Dict[str, Any]] = []
        session_id = events[0].get("session_id", "") if events else ""

        # 按项目分组
        by_project: Dict[str, List[Dict[str, Any]]] = {}
        for event in events:
            project = event.get("source", {}).get("project", "unknown")
            by_project.setdefault(project, []).append(event)

        # 检测通过率异常
        findings.extend(self._detect_pass_rate_anomalies(by_project))

        # 检测事件分布异常
        findings.extend(self._detect_event_distribution_anomalies(events))

        # 检测时间分布异常
        findings.extend(self._detect_time_anomalies(events))

        return AnalysisResult(
            analyzer=self.name,
            session_id=session_id,
            findings=findings,
            confidence=0.75,
            summary=f"跨项目异常检测完成，分析了 {len(by_project)} 个项目的 {len(events)} 个事件",
            recommendations=self._generate_recommendations(findings),
        )

    def _detect_pass_rate_anomalies(self, by_project: Dict[str, List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
        """检测通过率异常"""
        findings = []

        for project, events in by_project.items():
            passed = sum(1 for e in events if e["type"] == "test.end")
            failed = sum(1 for e in events if e["type"] == "test.fail")
            total = passed + failed

            if total == 0:
                continue

            pass_rate = passed / total
            if pass_rate < 0.8:
                findings.append(
                    {
                        "severity": "high" if pass_rate < 0.5 else "medium",
                        "category": "low_pass_rate",
                        "description": f"项目 {project} 通过率过低: {pass_rate:.1%} ({passed}/{total})",
                        "project": project,
                        "pass_rate": pass_rate,
                        "total_tests": total,
                    }
                )

        return findings

    def _detect_event_distribution_anomalies(self, events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """检测事件分布异常（某种事件类型异常多或少）"""
        findings = []
        type_counts: Dict[str, int] = {}

        for event in events:
            t = event["type"]
            type_counts[t] = type_counts.get(t, 0) + 1

        total = len(events)
        if total == 0:
            return findings

        # 检测异常高的失败率
        fail_count = type_counts.get("test.fail", 0)
        if total > 10 and fail_count / total > 0.3:
            findings.append(
                {
                    "severity": "high",
                    "category": "high_failure_ratio",
                    "description": f"失败事件占比过高: {fail_count}/{total} ({fail_count / total:.1%})",
                    "fail_count": fail_count,
                    "total_events": total,
                }
            )

        return findings

    def _detect_time_anomalies(self, events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """检测时间分布异常（事件间隔异常长）"""
        findings = []

        if len(events) < 2:
            return findings

        timestamps = [e["timestamp"] for e in events if "timestamp" in e]
        if len(timestamps) < 2:
            return findings

        # 计算事件间隔
        gaps = []
        for i in range(1, len(timestamps)):
            gap = timestamps[i] - timestamps[i - 1]
            gaps.append(gap)

        if not gaps:
            return findings

        avg_gap = sum(gaps) / len(gaps)
        max_gap = max(gaps)

        # 检测异常长间隔（超过平均的 10 倍且超过 30 秒）
        if max_gap > avg_gap * 10 and max_gap > 30000:
            findings.append(
                {
                    "severity": "medium",
                    "category": "time_gap",
                    "description": f"检测到异常长间隔: {max_gap / 1000:.1f}秒（平均 {avg_gap / 1000:.1f}秒）",
                    "max_gap_ms": max_gap,
                    "avg_gap_ms": avg_gap,
                }
            )

        return findings

    def _generate_recommendations(self, findings: List[Dict[str, Any]]) -> List[str]:
        recs = []
        categories = {f["category"] for f in findings}

        if "low_pass_rate" in categories:
            recs.append("存在通过率过低的项目，建议优先排查环境和依赖问题")

        if "high_failure_ratio" in categories:
            recs.append("失败事件占比过高，建议检查最近的代码变更")

        if "time_gap" in categories:
            recs.append("存在异常长时间间隔，可能存在超时或阻塞问题")

        return recs
