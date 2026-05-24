"""语义评估分析器 — 迁移自 TravelAgent evaluators

使用 LLM-as-Judge 对 Agent 输出进行语义质量评估。
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from .base import AnalysisResult, BaseAnalyzer


class SemanticEvaluator(BaseAnalyzer):
    """LLM-as-Judge 语义评估器。

    分析 agent.tool_call / agent.tool_result 事件，
    评估输出质量。
    """

    def __init__(
        self,
        llm_client: Optional[Any] = None,
        eval_fn: Optional[Callable[[str, str], float]] = None,
    ) -> None:
        """
        Args:
            llm_client: LLM 客户端（可选）
            eval_fn: 自定义评估函数 (input, output) -> score 0-1
        """
        self.llm = llm_client
        self._eval_fn = eval_fn

    @property
    def name(self) -> str:
        return "semantic_eval"

    def analyze(self, events: List[Dict[str, Any]]) -> AnalysisResult:
        findings: List[Dict[str, Any]] = []
        session_id = events[0].get("session_id", "") if events else ""

        # 提取 Agent 交互事件
        agent_events = [e for e in events if e["type"].startswith("agent.")]

        for event in agent_events:
            if event["type"] == "agent.tool_result":
                result = self._evaluate_tool_output(event)
                if result and result["score"] < 0.6:
                    findings.append(result)

        return AnalysisResult(
            analyzer=self.name,
            session_id=session_id,
            findings=findings,
            confidence=0.7,
            summary=f"评估了 {len(agent_events)} 个 Agent 交互，发现 {len(findings)} 个低质量输出",
            recommendations=self._generate_recommendations(findings),
        )

    def _evaluate_tool_output(self, event: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """评估单个工具输出"""
        data = event.get("data", {})
        tool_name = data.get("tool_name", "unknown")
        tool_input = str(data.get("input", ""))[:500]
        tool_output = str(data.get("output", ""))[:500]
        success = data.get("success", True)

        # 基本检查
        if not success:
            return {
                "severity": "medium",
                "category": "tool_failure",
                "description": f"工具 {tool_name} 调用失败",
                "tool": tool_name,
                "score": 0.0,
            }

        # 输出为空
        if not tool_output or tool_output.strip() == "":
            return {
                "severity": "medium",
                "category": "empty_output",
                "description": f"工具 {tool_name} 返回空输出",
                "tool": tool_name,
                "score": 0.1,
            }

        # 使用自定义评估函数
        if self._eval_fn:
            try:
                score = self._eval_fn(tool_input, tool_output)
                if score < 0.6:
                    return {
                        "severity": "low" if score > 0.3 else "medium",
                        "category": "low_quality",
                        "description": f"工具 {tool_name} 输出质量较低 (score={score:.2f})",
                        "tool": tool_name,
                        "score": score,
                    }
            except Exception:
                pass

        return None

    def _generate_recommendations(self, findings: List[Dict[str, Any]]) -> List[str]:
        recs = []
        categories = {f["category"] for f in findings}

        if "tool_failure" in categories:
            recs.append("存在工具调用失败，建议检查 API 可用性和错误处理")

        if "empty_output" in categories:
            recs.append("存在空输出，建议检查数据源和 mock 覆盖")

        if "low_quality" in categories:
            recs.append("存在低质量输出，建议优化 prompt 或增加后处理")

        return recs


# ─── 结构化断言（迁移自 TravelAgent）────────────────────────


def assert_trip_plan_structure(output: str) -> List[Dict[str, Any]]:
    """验证 Agent 输出包含行程规划所需的结构化信息。

    迁移自 TravelAgent evaluators.test.ts
    """
    import re

    results = []
    checks = [
        ("包含目的地", r"目的地|城市|景点|行程|旅游|出发|抵达", True),
        ("包含日期", r"\d{4}[-/年]\d{1,2}[-/月]\d{1,2}[日]?|Day\s*\d|第[一二三四五六七八九十]+天", True),
        ("包含景点推荐", r"景点|游览|参观|推荐|攻略", True),
        ("包含餐饮建议", r"早餐|午餐|晚餐|美食|餐厅", False),
        ("包含住宿建议", r"住宿|酒店|民宿", False),
        ("包含费用信息", r"预算|费用|价格|元|¥", False),
    ]

    for name, pattern, required in checks:
        match = bool(re.search(pattern, output))
        results.append({
            "check": name,
            "passed": match,
            "required": required,
            "score": 1.0 if match else 0.0,
        })

    return results
