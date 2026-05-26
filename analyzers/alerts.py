"""告警管理器 — 规则匹配、去重、冷却期、通知分发

核心职责:
- 管理告警规则（AlertRule）
- 从 Pipeline findings 中筛选触发告警的条目
- 去重 + 冷却期：同一 finding_id 在冷却期内不重复告警
- 分发到多个通知渠道
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from .notifiers import AlertMessage, ConsoleNotifier, FileNotifier, Notifier

logger = logging.getLogger(__name__)

# 严重级别排序（数字越大越严重）
SEVERITY_ORDER = {"low": 1, "medium": 2, "high": 3, "critical": 4}

# 默认冷却期（秒）
DEFAULT_COOLDOWN_S = 300  # 5 分钟


# ─── 告警规则 ─────────────────────────────────────────────


@dataclass
class AlertRule:
    """告警规则

    Attributes:
        name: 规则名称
        condition: 匹配条件 — category 前缀匹配列表（空 = 全匹配）
        severity_threshold: 最低严重级别（只触发 >= 该级别的告警）
        channels: 通知渠道名称列表（空 = 全部渠道）
        cooldown_s: 冷却期（秒），同一 finding_id 在此期间不重复告警
        enabled: 是否启用
    """

    name: str
    condition: List[str] = field(default_factory=list)  # category 匹配列表，空=全匹配
    severity_threshold: str = "medium"  # low / medium / high / critical
    channels: List[str] = field(default_factory=list)  # 空=全部
    cooldown_s: float = DEFAULT_COOLDOWN_S
    enabled: bool = True

    def matches(self, finding: Dict[str, Any]) -> bool:
        """检查 finding 是否匹配此规则"""
        if not self.enabled:
            return False

        # 严重级别检查
        severity = finding.get("severity", "low")
        if SEVERITY_ORDER.get(severity, 0) < SEVERITY_ORDER.get(self.severity_threshold, 0):
            return False

        # Category 条件检查
        if self.condition:
            category = finding.get("category", "")
            if not any(category.startswith(prefix) for prefix in self.condition):
                return False

        return True


# ─── 默认规则 ─────────────────────────────────────────────

DEFAULT_RULES = [
    AlertRule(
        name="critical-alerts",
        condition=[],  # 全匹配
        severity_threshold="critical",
        cooldown_s=60,
    ),
    AlertRule(
        name="high-severity",
        condition=[],
        severity_threshold="high",
        cooldown_s=300,
    ),
    AlertRule(
        name="flaky-tests",
        condition=["flaky_test"],
        severity_threshold="medium",
        cooldown_s=600,
    ),
]


# ─── 告警管理器 ───────────────────────────────────────────


class AlertManager:
    """告警管理器

    Args:
        rules: 告警规则列表（不提供则使用默认规则）
        notifiers: 通知渠道列表（不提供则使用 ConsoleNotifier）
        alert_log_path: 告警日志文件路径（提供则自动添加 FileNotifier）
    """

    def __init__(
        self,
        rules: Optional[List[AlertRule]] = None,
        notifiers: Optional[List[Notifier]] = None,
        alert_log_path: Optional[str | Path] = None,
    ):
        self.rules = rules if rules is not None else list(DEFAULT_RULES)
        self.notifiers: List[Notifier] = list(notifiers) if notifiers else [ConsoleNotifier()]

        if alert_log_path:
            self.file_notifier = FileNotifier(alert_log_path)
            # 避免重复添加
            if not any(isinstance(n, FileNotifier) for n in self.notifiers):
                self.notifiers.append(self.file_notifier)
        else:
            self.file_notifier = None

        # 去重缓存: finding_id -> 最近告警时间
        self._cooldown_cache: Dict[str, float] = {}

    @property
    def name(self) -> str:
        return "AlertManager"

    def check_and_notify(
        self,
        findings: List[Dict[str, Any]],
        session_id: str = "",
    ) -> List[AlertMessage]:
        """检查 findings 并发送通知

        Args:
            findings: Pipeline 输出的 confirmed_findings（必须含 finding_id, severity, category, description）
            session_id: 当前会话 ID

        Returns:
            实际发送的告警消息列表
        """
        now = time.time()
        sent: List[AlertMessage] = []

        for finding in findings:
            finding_id = finding.get("finding_id", "")
            if not finding_id:
                continue

            # 匹配规则
            matching_rules = [r for r in self.rules if r.matches(finding)]
            if not matching_rules:
                continue

            # 取最短冷却期（最敏感的规则）
            min_cooldown = min(r.cooldown_s for r in matching_rules)

            # 去重检查
            last_alert = self._cooldown_cache.get(finding_id, 0)
            if now - last_alert < min_cooldown:
                logger.debug("跳过 %s（冷却期 %.0fs 未过）", finding_id, min_cooldown - (now - last_alert))
                continue

            # 构造消息
            msg = AlertMessage(
                finding_id=finding_id,
                category=finding.get("category", "unknown"),
                severity=finding.get("severity", "low"),
                description=finding.get("description", ""),
                session_id=session_id,
                affected_tests=finding.get("affected_tests", []),
                affected_projects=finding.get("affected_projects", []),
                suggested_fix=finding.get("suggested_fix", ""),
                confidence=finding.get("confidence", 0),
            )

            # 收集目标渠道
            target_channels: Set[str] = set()
            for r in matching_rules:
                if r.channels:
                    target_channels.update(r.channels)
            # 空 channels = 发到全部渠道
            if not target_channels:
                target_names = {n.name for n in self.notifiers}
            else:
                target_names = target_channels

            # 发送
            for notifier in self.notifiers:
                if notifier.name in target_names:
                    try:
                        notifier.send(msg)
                    except Exception as e:
                        logger.warning("通知发送失败 [%s]: %s", notifier.name, e)

            # 更新冷却缓存
            self._cooldown_cache[finding_id] = now
            sent.append(msg)

        return sent

    def add_rule(self, rule: AlertRule) -> None:
        """添加规则"""
        self.rules.append(rule)

    def add_notifier(self, notifier: Notifier) -> None:
        """添加通知渠道"""
        self.notifiers.append(notifier)

    def get_alert_history(self, limit: int = 100) -> List[Dict[str, Any]]:
        """读取告警历史（从 FileNotifier 日志）"""
        if self.file_notifier:
            return self.file_notifier.read_history(limit)
        return []

    def clear_cooldown(self) -> None:
        """清空冷却缓存（测试用）"""
        self._cooldown_cache.clear()

    def test_notification(self, severity: str = "high") -> AlertMessage:
        """发送一条测试告警"""
        msg = AlertMessage(
            finding_id="test_alert_0000",
            category="test_alert",
            severity=severity,
            description="这是一条测试告警，用于验证通知配置",
            session_id="test-session",
            affected_tests=["test_example"],
        )
        for notifier in self.notifiers:
            try:
                notifier.send(msg)
            except Exception as e:
                logger.warning("测试通知发送失败 [%s]: %s", notifier.name, e)
        return msg
