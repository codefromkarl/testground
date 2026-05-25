"""JSON Schema 定义 — 每个分析阶段的输出约束

受 audit 的 Schema-First 设计启发：把 schema 注入 system prompt，
让 LLM 首次输出就符合结构，减少 repair 次数。
"""

from __future__ import annotations

from typing import Any, Dict

# ─── Recon: 事件分类 + 分析任务生成 ───────────────────────

RECON_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "required": ["summary", "event_breakdown", "analysis_tasks"],
    "additionalProperties": False,
    "properties": {
        "summary": {
            "type": "object",
            "required": ["total_events", "sessions", "projects", "frameworks"],
            "properties": {
                "total_events": {"type": "integer"},
                "sessions": {"type": "integer"},
                "projects": {"type": "array", "items": {"type": "string"}},
                "frameworks": {"type": "array", "items": {"type": "string"}},
                "time_span_ms": {"type": "integer"},
                "pass_rate": {"type": "number", "minimum": 0, "maximum": 1},
            },
        },
        "event_breakdown": {
            "type": "object",
            "description": "按事件类型统计数量",
            "additionalProperties": {"type": "integer"},
        },
        "analysis_tasks": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["task_id", "agent_type", "scope_hint", "target_events"],
                "properties": {
                    "task_id": {"type": "string"},
                    "agent_type": {
                        "type": "string",
                        "enum": [
                            "flaky_detector",
                            "regression_detector",
                            "semantic_evaluator",
                            "coverage_analyzer",
                            "performance_analyzer",
                        ],
                    },
                    "scope_hint": {
                        "type": "string",
                        "description": "具体说明要分析什么，如 'TravelAgent 项目中重试过的测试'",
                    },
                    "target_events": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "相关事件 ID 列表",
                    },
                    "priority": {"type": "integer", "minimum": 1, "maximum": 5},
                    "rationale": {"type": "string"},
                },
            },
        },
        "anomalies_detected": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Recon 阶段直接发现的明显异常（如 0% 通过率）",
        },
    },
}

# ─── Hunt: 单类问题的详细发现 ─────────────────────────────

HUNT_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "required": ["task_id", "agent_type", "findings"],
    "additionalProperties": False,
    "properties": {
        "task_id": {"type": "string"},
        "agent_type": {"type": "string"},
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "required": [
                    "finding_id",
                    "category",
                    "severity",
                    "description",
                    "evidence",
                ],
                "properties": {
                    "finding_id": {"type": "string"},
                    "category": {
                        "type": "string",
                        "enum": [
                            "flaky_test",
                            "performance_regression",
                            "assertion_gap",
                            "coverage_gap",
                            "semantic_mismatch",
                            "race_condition",
                            "resource_leak",
                            "test_isolation",
                        ],
                    },
                    "severity": {
                        "type": "string",
                        "enum": ["critical", "high", "medium", "low"],
                    },
                    "description": {"type": "string"},
                    "evidence": {
                        "type": "object",
                        "required": ["event_ids", "snippet"],
                        "properties": {
                            "event_ids": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                            "snippet": {
                                "type": "string",
                                "description": "关键事件数据摘要",
                            },
                        },
                    },
                    "affected_tests": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "affected_projects": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "confidence": {
                        "type": "number",
                        "minimum": 0,
                        "maximum": 1,
                    },
                    "suggested_fix": {"type": "string"},
                },
            },
        },
        "analysis_summary": {"type": "string"},
    },
}

# ─── Validate: 对抗验证 ───────────────────────────────────

VALIDATE_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "required": ["finding_id", "verdict", "rationale"],
    "additionalProperties": False,
    "properties": {
        "finding_id": {"type": "string"},
        "verdict": {
            "type": "string",
            "enum": ["confirmed", "rejected", "needs_more_info"],
        },
        "rationale": {
            "type": "string",
            "description": "为什么确认/推翻，必须引用具体证据",
        },
        "alternative_explanation": {
            "type": "string",
            "description": "你考虑过的良性解释（即使确认也必须提供）",
        },
        "confidence": {
            "type": "number",
            "minimum": 0,
            "maximum": 1,
        },
        "suggested_test": {
            "type": "string",
            "description": "如果 verdict=needs_more_info，建议的验证方法",
        },
    },
}

# ─── Feedback: 发现扩散 ───────────────────────────────────

FEEDBACK_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "required": ["new_tasks"],
    "additionalProperties": False,
    "properties": {
        "new_tasks": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["task_id", "agent_type", "scope_hint", "seeded_from"],
                "properties": {
                    "task_id": {"type": "string"},
                    "agent_type": {"type": "string"},
                    "scope_hint": {"type": "string"},
                    "seeded_from": {
                        "type": "string",
                        "description": "原始 finding_id",
                    },
                    "target_events": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "priority": {"type": "integer", "minimum": 1, "maximum": 5},
                },
            },
        },
        "pattern_description": {
            "type": "string",
            "description": "从已确认发现中提取的模式描述",
        },
    },
}

# ─── Report: 最终报告 ─────────────────────────────────────

REPORT_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "required": ["title", "executive_summary", "confirmed_findings", "metrics"],
    "additionalProperties": False,
    "properties": {
        "title": {"type": "string"},
        "executive_summary": {"type": "string"},
        "confirmed_findings": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["finding_id", "category", "severity", "description", "impact"],
                "properties": {
                    "finding_id": {"type": "string"},
                    "category": {"type": "string"},
                    "severity": {"type": "string"},
                    "description": {"type": "string"},
                    "impact": {"type": "string"},
                    "affected_tests": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "affected_projects": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "suggested_fix": {"type": "string"},
                    "validation_rationale": {"type": "string"},
                },
            },
        },
        "rejected_count": {"type": "integer"},
        "metrics": {
            "type": "object",
            "required": ["total_analyzed", "confirmed", "rejected", "quality_score"],
            "properties": {
                "total_analyzed": {"type": "integer"},
                "confirmed": {"type": "integer"},
                "rejected": {"type": "integer"},
                "needs_more_info": {"type": "integer"},
                "quality_score": {
                    "type": "number",
                    "minimum": 0,
                    "maximum": 100,
                },
            },
        },
        "recommendations": {
            "type": "array",
            "items": {"type": "string"},
        },
    },
}

# Schema 注册表
SCHEMAS: Dict[str, Dict[str, Any]] = {
    "recon": RECON_SCHEMA,
    "hunt": HUNT_SCHEMA,
    "validate": VALIDATE_SCHEMA,
    "feedback": FEEDBACK_SCHEMA,
    "report": REPORT_SCHEMA,
}


def get_schema(stage: str) -> Dict[str, Any]:
    """获取指定阶段的 JSON Schema"""
    if stage not in SCHEMAS:
        raise ValueError(f"Unknown stage: {stage}. Available: {list(SCHEMAS.keys())}")
    return SCHEMAS[stage]


def schema_as_text(stage: str) -> str:
    """将 schema 序列化为文本，用于注入 system prompt"""
    import json

    return json.dumps(get_schema(stage), indent=2, ensure_ascii=False)
