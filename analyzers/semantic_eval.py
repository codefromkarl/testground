"""语义评估分析器 — 迁移自 TravelAgent evaluators

使用 LLM-as-Judge 对 Agent 输出进行语义质量评估。
支持 LLM 评估（主路径）和规则引擎（fallback）。
"""

from __future__ import annotations

import json
import logging
from typing import Any, Callable, Dict, List, Optional

from .base import AnalysisResult, BaseAnalyzer
from .llm_client import LLMClient, LLMConfig, LLMError, is_llm_available

logger = logging.getLogger(__name__)

# LLM 评估 prompt 模板
EVAL_PROMPT_TEMPLATE = """评估以下 AI 工具输出质量（0-1 分数）:

输入: {input}
输出: {output}

评估维度:
1. 相关性 - 输出是否回答了输入问题
2. 完整性 - 信息是否完整
3. 准确性 - 内容是否准确

返回 JSON: {{"score": 0.8, "reason": "..."}}"""


class SemanticEvaluator(BaseAnalyzer):
    """LLM-as-Judge 语义评估器。

    分析 agent.tool_call / agent.tool_result 事件，
    评估输出质量。

    评估策略:
    - 有 LLM 客户端时：使用 LLM 评估（主路径）
    - 无 LLM 客户端时：降级到规则引擎（fallback）
    """

    def __init__(
        self,
        llm_client: Optional[LLMClient] = None,
        eval_fn: Optional[Callable[[str, str], float]] = None,
        llm_config: Optional[LLMConfig] = None,
        use_llm: Optional[bool] = None,
    ) -> None:
        """
        Args:
            llm_client: LLM 客户端实例（可选）
            eval_fn: 自定义评估函数 (input, output) -> score 0-1
            llm_config: LLM 配置（当 llm_client 为 None 时用于创建客户端）
            use_llm: 是否使用 LLM（None=自动检测，True=强制使用，False=强制规则引擎）
        """
        self._eval_fn = eval_fn
        self._llm_config = llm_config
        self._use_llm = use_llm

        # 初始化 LLM 客户端
        if llm_client is not None:
            self.llm = llm_client
        elif use_llm is True or (use_llm is None and is_llm_available()):
            try:
                self.llm = LLMClient(config=llm_config or LLMConfig())
            except Exception as e:
                logger.warning("LLM 客户端初始化失败，降级到规则引擎: %s", e)
                self.llm = None
        else:
            self.llm = None

    @property
    def name(self) -> str:
        return "semantic_eval"

    @property
    def uses_llm(self) -> bool:
        """当前是否使用 LLM 评估"""
        return self.llm is not None

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

        eval_method = "LLM" if self.llm else "规则引擎"
        return AnalysisResult(
            analyzer=self.name,
            session_id=session_id,
            findings=findings,
            confidence=0.8 if self.llm else 0.7,
            summary=f"评估了 {len(agent_events)} 个 Agent 交互（{eval_method}），发现 {len(findings)} 个低质量输出",
            recommendations=self._generate_recommendations(findings),
        )

    def _evaluate_tool_output(self, event: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """评估单个工具输出"""
        data = event.get("data", {})
        tool_name = data.get("tool_name", "unknown")
        tool_input = str(data.get("input", ""))[:500]
        tool_output = str(data.get("output", ""))[:500]
        success = data.get("success", True)

        # 基本检查（始终执行）
        if not success:
            return {
                "severity": "medium",
                "category": "tool_failure",
                "description": f"工具 {tool_name} 调用失败",
                "tool": tool_name,
                "score": 0.0,
                "eval_method": "rule",
            }

        # 输出为空
        if not tool_output or tool_output.strip() == "":
            return {
                "severity": "medium",
                "category": "empty_output",
                "description": f"工具 {tool_name} 返回空输出",
                "tool": tool_name,
                "score": 0.1,
                "eval_method": "rule",
            }

        # 优先使用 LLM 评估
        if self.llm is not None:
            try:
                score, reason = self._evaluate_with_llm(tool_input, tool_output)
                if score < 0.6:
                    return {
                        "severity": "low" if score > 0.3 else "medium",
                        "category": "low_quality",
                        "description": f"工具 {tool_name} 输出质量较低 (score={score:.2f}): {reason}",
                        "tool": tool_name,
                        "score": score,
                        "reason": reason,
                        "eval_method": "llm",
                    }
                return None  # LLM 评估通过
            except LLMError as e:
                logger.warning("LLM 评估失败，降级到规则引擎: %s", e)
                # 继续走规则引擎 fallback

        # Fallback: 自定义评估函数
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
                        "eval_method": "custom_fn",
                    }
            except Exception:
                pass

        return None

    def _evaluate_with_llm(self, input_text: str, output_text: str) -> tuple[float, str]:
        """使用 LLM 评估输出质量。

        Args:
            input_text: 工具输入
            output_text: 工具输出

        Returns:
            (score, reason) 元组

        Raises:
            LLMError: 调用失败时抛出
        """
        prompt = EVAL_PROMPT_TEMPLATE.format(input=input_text, output=output_text)
        result = self.llm.chat_json(prompt)  # type: ignore

        score = float(result.get("score", 0.5))
        reason = str(result.get("reason", ""))

        # 钳位到 [0, 1]
        score = max(0.0, min(1.0, score))

        return score, reason

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
