"""分析流水线 — 受 evilsocket/audit 启发的多窄 Agent 架构

将 testground 的分析层从单一规则引擎升级为：
  Recon → Hunt(并行) → Validate(对抗) → Feedback(扩散) → Report

每个阶段有独立 prompt、JSON Schema 约束、成本追踪。
"""

from .orchestrator import AnalysisPipeline, PipelineConfig
from .runner import AgentResult, AgentRunner
from .state import PipelineState

__all__ = [
    "AnalysisPipeline",
    "PipelineConfig",
    "PipelineState",
    "AgentRunner",
    "AgentResult",
]
