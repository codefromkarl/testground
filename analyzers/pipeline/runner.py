"""Agent Runner — 执行单个分析 Agent，带 Schema 验证和 Repair

受 audit 的 runner.py 启发：
- Schema 注入 system prompt（首诊成功率高）
- 失败后自动 repair（重新输出）
- 成本追踪
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..base import AnalysisResult
from ..llm_client import LLMClient, LLMConfig, LLMError
from .schemas import schema_as_text

logger = logging.getLogger(__name__)


@dataclass
class AgentResult:
    """单次 Agent 执行的结果"""
    payload: Dict[str, Any]
    input_tokens: int
    output_tokens: int
    duration_ms: int
    repair_used: bool
    agent_type: str
    task_id: str


class SchemaValidationError(Exception):
    """Schema 验证失败（repair 后仍不通过）"""
    pass


def _validate_schema(payload: Dict[str, Any], schema: Dict[str, Any]) -> List[str]:
    """简易 JSON Schema 验证 — 检查 required、type、enum 约束。"""
    errors: List[str] = []

    # 检查 required
    required = schema.get("required", [])
    for key in required:
        if key not in payload:
            errors.append(f"Missing required field: {key}")

    # 检查 properties 的 type 和 enum
    properties = schema.get("properties", {})
    for key, spec in properties.items():
        if key not in payload:
            continue
        value = payload[key]

        # type 检查
        expected_type = spec.get("type")
        if expected_type:
            type_map = {
                "string": str,
                "integer": int,
                "number": (int, float),
                "boolean": bool,
                "array": list,
                "object": dict,
            }
            expected = type_map.get(expected_type)
            if expected and not isinstance(value, expected):
                # int 可以接受 float (如 1.0)
                if expected_type == "integer" and isinstance(value, float) and value == int(value):
                    pass
                else:
                    errors.append(f"Field '{key}': expected {expected_type}, got {type(value).__name__}")

        # enum 检查
        enum_values = spec.get("enum")
        if enum_values and value not in enum_values:
            errors.append(f"Field '{key}': value '{value}' not in enum {enum_values}")

    return errors


def _extract_json(text: str) -> Dict[str, Any]:
    """从 LLM 输出中提取 JSON（处理 markdown code block）"""
    text = text.strip()

    # 去掉 markdown code block
    if text.startswith("```"):
        lines = text.split("\n")
        # 找到结束的 ```
        end_idx = len(lines) - 1
        for i in range(len(lines) - 1, 0, -1):
            if lines[i].strip() == "```":
                end_idx = i
                break
        text = "\n".join(lines[1:end_idx])

    # 尝试直接解析
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # 尝试找到第一个 { 和最后一个 }
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            pass

    raise ValueError(f"Cannot extract JSON from LLM output: {text[:200]}...")


def _build_repair_prompt(prev_output: str, errors: List[str], stage: str) -> str:
    """构建 repair prompt，让 LLM 修正 schema 不合规的输出"""
    err_block = "\n".join(f"- {e}" for e in errors[:10])
    return (
        f"你的上一次输出未通过 {stage} 阶段的 Schema 验证。错误：\n"
        f"{err_block}\n\n"
        "请重新输出，仅修正这些错误。输出单个 JSON 对象 — 不要加任何文字或 markdown 标记。"
    )


class AgentRunner:
    """执行单个分析 Agent。

    职责：
    1. 构建带 Schema 的 system prompt
    2. 调用 LLM
    3. 验证输出是否符合 Schema
    4. 必要时执行 repair（重新输出）
    5. 返回结构化结果
    """

    def __init__(
        self,
        llm_client: Optional[LLMClient] = None,
        llm_config: Optional[LLMConfig] = None,
        repair_attempts: int = 1,
    ):
        if llm_client:
            self.llm = llm_client
        else:
            self.llm = LLMClient(config=llm_config or LLMConfig())
        self.repair_attempts = repair_attempts

    def run(
        self,
        *,
        stage: str,
        system_prompt: str,
        user_input: Dict[str, Any],
        agent_type: str = "unknown",
        task_id: str = "",
    ) -> AgentResult:
        """执行一个 Agent 分析任务。

        Args:
            stage: 阶段名（recon/hunt/validate/feedback/report）
            system_prompt: Agent 的角色和指令
            user_input: 传给 Agent 的输入数据
            agent_type: Agent 类型标识
            task_id: 任务 ID

        Returns:
            AgentResult 包含结构化 payload 和元数据

        Raises:
            SchemaValidationError: repair 后仍不通过
            LLMError: LLM 调用失败
        """
        # 构建完整 prompt: 角色指令 + Schema 约束
        schema_text = schema_as_text(stage)
        full_system = (
            f"{system_prompt}\n\n"
            f"# 输出 Schema\n\n"
            f"你的输出必须符合以下 JSON Schema。注意嵌套对象、必填字段和 `additionalProperties`。\n\n"
            f"```json\n{schema_text}\n```\n"
        )

        user_text = json.dumps(user_input, ensure_ascii=False)

        # 首次调用
        start_time = time.time()
        raw_output = self.llm.chat(user_text, full_system)
        duration_ms = int((time.time() - start_time) * 1000)

        # 提取 + 验证
        payload = _extract_json(raw_output)
        errors = _validate_schema(payload, get_schema(stage))
        repair_used = False

        # Repair 循环
        attempts = 0
        while errors and attempts < self.repair_attempts:
            attempts += 1
            repair_used = True
            repair_prompt = _build_repair_prompt(raw_output, errors, stage)
            logger.info("[%s/%s] Schema 验证失败，执行 repair %d/%d", stage, task_id, attempts, self.repair_attempts)

            repair_start = time.time()
            raw_output = self.llm.chat(repair_prompt, full_system)
            duration_ms += int((time.time() - repair_start) * 1000)

            payload = _extract_json(raw_output)
            errors = _validate_schema(payload, get_schema(stage))

        if errors:
            raise SchemaValidationError(
                f"[{stage}/{task_id}] Schema 验证失败（{self.repair_attempts} 次 repair 后）: {errors[:5]}"
            )

        # 估算 token（简易：中文 1 字 ≈ 2 token，英文 1 词 ≈ 1.3 token）
        input_tokens = len(user_text) * 2
        output_tokens = len(raw_output) * 2

        return AgentResult(
            payload=payload,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            duration_ms=duration_ms,
            repair_used=repair_used,
            agent_type=agent_type,
            task_id=task_id or f"task_{uuid.uuid4().hex[:8]}",
        )


# 为了避免循环导入，直接在此定义 get_schema
def get_schema(stage: str) -> Dict[str, Any]:
    from .schemas import SCHEMAS
    if stage not in SCHEMAS:
        raise ValueError(f"Unknown stage: {stage}")
    return SCHEMAS[stage]
