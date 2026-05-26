"""通知渠道 — 告警的多种投递方式

支持:
- ConsoleNotifier: 控制台彩色输出
- WebhookNotifier: HTTP webhook（Slack/飞书兼容格式）
- FileNotifier: 写入告警日志文件
"""

from __future__ import annotations

import json
import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List
from urllib import request as _urlrequest
from urllib.error import URLError

logger = logging.getLogger(__name__)


# ─── 告警消息结构 ─────────────────────────────────────────


@dataclass
class AlertMessage:
    """告警消息"""

    finding_id: str
    category: str
    severity: str
    description: str
    session_id: str
    affected_tests: List[str] = field(default_factory=list)
    affected_projects: List[str] = field(default_factory=list)
    suggested_fix: str = ""
    confidence: float = 0.0
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "finding_id": self.finding_id,
            "category": self.category,
            "severity": self.severity,
            "description": self.description,
            "session_id": self.session_id,
            "affected_tests": self.affected_tests,
            "affected_projects": self.affected_projects,
            "suggested_fix": self.suggested_fix,
            "confidence": self.confidence,
            "timestamp": self.timestamp,
        }


# ─── 通知渠道基类 ─────────────────────────────────────────


class Notifier(ABC):
    """通知渠道抽象基类"""

    @abstractmethod
    def send(self, message: AlertMessage) -> bool:
        """发送告警消息。返回 True 表示成功。"""
        ...

    @property
    @abstractmethod
    def name(self) -> str:
        """渠道名称"""
        ...


# ─── 控制台通知 ───────────────────────────────────────────


_SEVERITY_ICONS = {
    "critical": "🔴",
    "high": "🟠",
    "medium": "🟡",
    "low": "🟢",
}

_SEVERITY_COLORS = {
    "critical": "\033[91m",  # 红
    "high": "\033[93m",      # 黄
    "medium": "\033[96m",    # 青
    "low": "\033[92m",       # 绿
}
_RESET = "\033[0m"


class ConsoleNotifier(Notifier):
    """控制台输出通知"""

    @property
    def name(self) -> str:
        return "console"

    def send(self, message: AlertMessage) -> bool:
        icon = _SEVERITY_ICONS.get(message.severity, "⚪")
        color = _SEVERITY_COLORS.get(message.severity, "")
        ts = time.strftime("%H:%M:%S", time.localtime(message.timestamp))

        print(f"\n{color}{'=' * 60}{_RESET}")
        print(f"{color}{icon} [{message.severity.upper()}] 告警: {message.category}{_RESET}")
        print(f"  时间: {ts}")
        print(f"  Finding: {message.finding_id}")
        print(f"  描述: {message.description}")
        if message.affected_tests:
            print(f"  影响测试: {', '.join(message.affected_tests[:5])}")
        if message.suggested_fix:
            print(f"  建议修复: {message.suggested_fix}")
        print(f"  会话: {message.session_id}")
        print(f"{color}{'=' * 60}{_RESET}\n")
        return True


# ─── Webhook 通知 ─────────────────────────────────────────


class WebhookNotifier(Notifier):
    """HTTP Webhook 通知 — 兼容 Slack Incoming Webhook 和飞书机器人

    Args:
        url: Webhook URL
        webhook_type: "slack" | "feishu" | "auto"（自动检测）
        timeout: HTTP 超时秒数
    """

    def __init__(self, url: str, webhook_type: str = "auto", timeout: int = 10):
        self.url = url
        self.timeout = timeout
        self.webhook_type = self._detect_type(url) if webhook_type == "auto" else webhook_type

    @property
    def name(self) -> str:
        return f"webhook({self.webhook_type})"

    @staticmethod
    def _detect_type(url: str) -> str:
        if "hooks.slack.com" in url:
            return "slack"
        if "open.feishu.cn" in url or "larksuite.com" in url:
            return "feishu"
        return "slack"  # 默认 Slack 格式

    def send(self, message: AlertMessage) -> bool:
        payload = self._format_payload(message)
        try:
            data = json.dumps(payload).encode("utf-8")
            req = _urlrequest.Request(
                self.url,
                data=data,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with _urlrequest.urlopen(req, timeout=self.timeout) as resp:
                if resp.status < 300:
                    return True
                logger.warning("Webhook 返回 %d: %s", resp.status, resp.read().decode())
                return False
        except (URLError, OSError) as e:
            logger.warning("Webhook 发送失败: %s", e)
            return False

    def _format_payload(self, msg: AlertMessage) -> Dict[str, Any]:
        if self.webhook_type == "feishu":
            return self._format_feishu(msg)
        return self._format_slack(msg)

    def _format_slack(self, msg: AlertMessage) -> Dict[str, Any]:
        """Slack Incoming Webhook 格式"""
        severity_colors = {
            "critical": "#FF0000",
            "high": "#FF8C00",
            "medium": "#FFD700",
            "low": "#36A64F",
        }
        fields = [
            {"title": "Severity", "value": msg.severity.upper(), "short": True},
            {"title": "Category", "value": msg.category, "short": True},
            {"title": "Session", "value": msg.session_id, "short": True},
            {"title": "Finding ID", "value": msg.finding_id, "short": True},
        ]
        if msg.affected_tests:
            fields.append({"title": "Affected Tests", "value": ", ".join(msg.affected_tests[:5]), "short": False})
        if msg.suggested_fix:
            fields.append({"title": "Suggested Fix", "value": msg.suggested_fix, "short": False})

        return {
            "attachments": [
                {
                    "color": severity_colors.get(msg.severity, "#808080"),
                    "title": f"[{msg.severity.upper()}] {msg.category}",
                    "text": msg.description,
                    "fields": fields,
                    "footer": "testground alert",
                    "ts": int(msg.timestamp),
                }
            ]
        }

    def _format_feishu(self, msg: AlertMessage) -> Dict[str, Any]:
        """飞书机器人消息格式"""
        severity_icons = {"critical": "🔴", "high": "🟠", "medium": "🟡", "low": "🟢"}
        icon = severity_icons.get(msg.severity, "⚪")

        elements = [
            {"tag": "div", "text": {"tag": "plain_text", "content": f"{icon} {msg.description}"}},
            {
                "tag": "div",
                "fields": [
                    {"is_short": True, "text": {"tag": "lark_md", "content": f"**Severity:** {msg.severity.upper()}"}},
                    {"is_short": True, "text": {"tag": "lark_md", "content": f"**Category:** {msg.category}"}},
                    {"is_short": True, "text": {"tag": "lark_md", "content": f"**Session:** {msg.session_id}"}},
                    {"is_short": True, "text": {"tag": "lark_md", "content": f"**Finding:** {msg.finding_id}"}},
                ],
            },
        ]
        if msg.affected_tests:
            elements.append(
                {"tag": "div", "text": {"tag": "lark_md", "content": f"**Affected Tests:** {', '.join(msg.affected_tests[:5])}"}}
            )
        if msg.suggested_fix:
            elements.append(
                {"tag": "div", "text": {"tag": "lark_md", "content": f"**Fix:** {msg.suggested_fix}"}}
            )

        return {
            "msg_type": "interactive",
            "card": {
                "header": {
                    "title": {"tag": "plain_text", "content": f"[{msg.severity.upper()}] 告警"},
                    "template": {"critical": "red", "high": "orange", "medium": "yellow", "low": "green"}.get(
                        msg.severity, "blue"
                    ),
                },
                "elements": elements,
            },
        }


# ─── 文件通知 ─────────────────────────────────────────────


class FileNotifier(Notifier):
    """文件日志通知 — 写入 JSONL 格式的告警日志

    Args:
        path: 日志文件路径
    """

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    @property
    def name(self) -> str:
        return f"file({self.path})"

    def send(self, message: AlertMessage) -> bool:
        try:
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(message.to_dict(), ensure_ascii=False) + "\n")
            return True
        except OSError as e:
            logger.warning("文件写入失败: %s", e)
            return False

    def read_history(self, limit: int = 100) -> List[Dict[str, Any]]:
        """读取告警历史"""
        if not self.path.exists():
            return []
        entries = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return entries[-limit:]
