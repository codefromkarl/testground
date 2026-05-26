"""AI 分析器基类"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List


@dataclass
class AnalysisResult:
    """分析结果"""

    analyzer: str
    session_id: str
    findings: List[Dict[str, Any]]
    confidence: float  # 0-1
    summary: str
    recommendations: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "analyzer": self.analyzer,
            "session_id": self.session_id,
            "findings": self.findings,
            "confidence": self.confidence,
            "summary": self.summary,
            "recommendations": self.recommendations,
        }


class BaseAnalyzer(ABC):
    """所有 AI 分析器的基类。"""

    @abstractmethod
    def analyze(self, events: List[Dict[str, Any]]) -> AnalysisResult:
        """分析一组事件，返回结果。"""
        ...

    @property
    @abstractmethod
    def name(self) -> str:
        """分析器名称"""
        ...
