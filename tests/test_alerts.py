"""告警系统测试 — 规则匹配、通知渠道、去重、冷却期、Pipeline 集成"""

import json
import sys
import time

import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))

from analyzers.alerts import DEFAULT_RULES, SEVERITY_ORDER, AlertManager, AlertRule
from analyzers.notifiers import AlertMessage, ConsoleNotifier, FileNotifier, Notifier, WebhookNotifier

pytestmark = pytest.mark.medium

# ─── 测试辅助 ─────────────────────────────────────────────


def make_finding(
    finding_id: str = "f_0001",
    category: str = "flaky_test",
    severity: str = "high",
    description: str = "测试问题",
    affected_tests: list | None = None,
    suggested_fix: str = "",
    confidence: float = 0.9,
) -> dict:
    return {
        "finding_id": finding_id,
        "category": category,
        "severity": severity,
        "description": description,
        "affected_tests": affected_tests or ["test_a"],
        "affected_projects": ["proj-a"],
        "suggested_fix": suggested_fix,
        "confidence": confidence,
    }


class MockNotifier(Notifier):
    """记录发送消息的 Mock 通知渠道"""

    def __init__(self, name: str = "mock", fail: bool = False):
        self._name = name
        self.fail = fail
        self.messages: list[AlertMessage] = []

    @property
    def name(self) -> str:
        return self._name

    def send(self, message: AlertMessage) -> bool:
        if self.fail:
            raise RuntimeError("mock failure")
        self.messages.append(message)
        return True


# ─── AlertRule 测试 ────────────────────────────────────────


class TestAlertRule:
    def test_severity_threshold(self):
        """severity_threshold 只触发 >= 该级别的 finding"""
        rule = AlertRule(name="test", severity_threshold="high")
        assert rule.matches(make_finding(severity="critical"))
        assert rule.matches(make_finding(severity="high"))
        assert not rule.matches(make_finding(severity="medium"))
        assert not rule.matches(make_finding(severity="low"))

    def test_category_condition(self):
        """condition 按 category 前缀匹配"""
        rule = AlertRule(name="test", condition=["flaky", "coverage"], severity_threshold="low")
        assert rule.matches(make_finding(category="flaky_test"))
        assert rule.matches(make_finding(category="coverage_gap"))
        assert not rule.matches(make_finding(category="performance_regression"))

    def test_empty_condition_matches_all(self):
        """空 condition = 全匹配"""
        rule = AlertRule(name="test", condition=[], severity_threshold="low")
        assert rule.matches(make_finding(category="anything"))
        assert rule.matches(make_finding(category=""))

    def test_disabled_rule_never_matches(self):
        """enabled=False 的规则不匹配"""
        rule = AlertRule(name="test", severity_threshold="low", enabled=False)
        assert not rule.matches(make_finding(severity="critical"))

    def test_severity_order_values(self):
        """严重级别排序正确"""
        assert SEVERITY_ORDER["low"] < SEVERITY_ORDER["medium"] < SEVERITY_ORDER["high"] < SEVERITY_ORDER["critical"]


# ─── 通知渠道测试 ─────────────────────────────────────────


class TestConsoleNotifier:
    def test_send_prints_to_stdout(self, capsys):
        """ConsoleNotifier 输出到 stdout"""
        notifier = ConsoleNotifier()
        msg = AlertMessage(
            finding_id="f_001",
            category="flaky_test",
            severity="high",
            description="测试",
            session_id="s1",
        )
        result = notifier.send(msg)
        assert result is True
        output = capsys.readouterr().out
        assert "flaky_test" in output
        assert "HIGH" in output

    def test_name(self):
        assert ConsoleNotifier().name == "console"


class TestFileNotifier:
    def test_send_writes_jsonl(self, tmp_path):
        """FileNotifier 写入 JSONL 格式"""
        log_file = tmp_path / "alerts.jsonl"
        notifier = FileNotifier(log_file)
        msg = AlertMessage(
            finding_id="f_001",
            category="flaky_test",
            severity="high",
            description="测试",
            session_id="s1",
        )
        result = notifier.send(msg)
        assert result is True

        lines = log_file.read_text().strip().splitlines()
        assert len(lines) == 1
        entry = json.loads(lines[0])
        assert entry["finding_id"] == "f_001"
        assert entry["category"] == "flaky_test"

    def test_read_history(self, tmp_path):
        """read_history 读取并解析日志"""
        log_file = tmp_path / "alerts.jsonl"
        notifier = FileNotifier(log_file)

        for i in range(5):
            msg = AlertMessage(
                finding_id=f"f_{i:04d}",
                category="test",
                severity="medium",
                description=f"msg {i}",
                session_id="s1",
            )
            notifier.send(msg)

        history = notifier.read_history(limit=3)
        assert len(history) == 3
        assert history[0]["finding_id"] == "f_0002"  # 最近 3 条

    def test_name(self, tmp_path):
        notifier = FileNotifier(tmp_path / "log.jsonl")
        assert "file(" in notifier.name


class TestWebhookNotifier:
    def test_slack_format(self):
        """Slack payload 格式正确"""
        notifier = WebhookNotifier("https://example.com/hook", webhook_type="slack")
        msg = AlertMessage(
            finding_id="f_001",
            category="flaky_test",
            severity="high",
            description="测试问题",
            session_id="s1",
            affected_tests=["test_a"],
            suggested_fix="修复建议",
        )
        payload = notifier._format_payload(msg)

        assert "attachments" in payload
        att = payload["attachments"][0]
        assert att["title"] == "[HIGH] flaky_test"
        assert att["text"] == "测试问题"
        assert any(f["title"] == "Severity" for f in att["fields"])

    def test_feishu_format(self):
        """飞书 payload 格式正确"""
        notifier = WebhookNotifier("https://open.feishu.cn/hook", webhook_type="feishu")
        msg = AlertMessage(
            finding_id="f_001",
            category="flaky_test",
            severity="critical",
            description="严重问题",
            session_id="s1",
        )
        payload = notifier._format_payload(msg)

        assert payload["msg_type"] == "interactive"
        assert "card" in payload
        assert payload["card"]["header"]["template"] == "red"

    def test_auto_detect_slack(self):
        """auto 模式检测 Slack URL"""
        notifier = WebhookNotifier("https://hooks.slack.com/services/T/B/x")
        assert notifier.webhook_type == "slack"

    def test_auto_detect_feishu(self):
        """auto 模式检测飞书 URL"""
        notifier = WebhookNotifier("https://open.feishu.cn/open-apis/bot/v2/hook/xxx")
        assert notifier.webhook_type == "feishu"

    @patch("analyzers.notifiers._urlrequest.urlopen")
    def test_send_success(self, mock_urlopen):
        """Webhook 发送成功"""
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        notifier = WebhookNotifier("https://example.com/hook")
        msg = AlertMessage(
            finding_id="f_001",
            category="test",
            severity="high",
            description="msg",
            session_id="s1",
        )
        result = notifier.send(msg)
        assert result is True
        mock_urlopen.assert_called_once()

    @patch("analyzers.notifiers._urlrequest.urlopen")
    def test_send_failure(self, mock_urlopen):
        """Webhook 发送失败返回 False"""
        from urllib.error import URLError

        mock_urlopen.side_effect = URLError("connection refused")
        notifier = WebhookNotifier("https://example.com/hook")
        msg = AlertMessage(
            finding_id="f_001",
            category="test",
            severity="high",
            description="msg",
            session_id="s1",
        )
        result = notifier.send(msg)
        assert result is False


# ─── AlertManager 测试 ────────────────────────────────────


class TestAlertManager:
    def test_basic_notify(self):
        """基本通知流程"""
        mock = MockNotifier()
        manager = AlertManager(
            rules=[AlertRule(name="all", severity_threshold="low")],
            notifiers=[mock],
        )
        findings = [make_finding(severity="high")]
        sent = manager.check_and_notify(findings, session_id="s1")

        assert len(sent) == 1
        assert sent[0].finding_id == "f_0001"
        assert len(mock.messages) == 1

    def test_severity_filter(self):
        """低级别 finding 不触发高阈值规则"""
        mock = MockNotifier()
        manager = AlertManager(
            rules=[AlertRule(name="high-only", severity_threshold="high")],
            notifiers=[mock],
        )
        findings = [make_finding(severity="low"), make_finding(severity="medium")]
        sent = manager.check_and_notify(findings, session_id="s1")

        assert len(sent) == 0
        assert len(mock.messages) == 0

    def test_dedup_same_finding_id(self):
        """同一 finding_id 不重复告警（冷却期内）"""
        mock = MockNotifier()
        manager = AlertManager(
            rules=[AlertRule(name="test", severity_threshold="low", cooldown_s=60)],
            notifiers=[mock],
        )

        findings = [make_finding(finding_id="dup_001")]
        sent1 = manager.check_and_notify(findings, session_id="s1")
        assert len(sent1) == 1

        # 立即再次检查，应被去重
        sent2 = manager.check_and_notify(findings, session_id="s1")
        assert len(sent2) == 0
        assert len(mock.messages) == 1

    def test_cooldown_expiry(self):
        """冷却期过后，同一 finding_id 可以再次告警"""
        mock = MockNotifier()
        manager = AlertManager(
            rules=[AlertRule(name="test", severity_threshold="low", cooldown_s=0.1)],
            notifiers=[mock],
        )

        findings = [make_finding(finding_id="cd_001")]
        manager.check_and_notify(findings, session_id="s1")

        # 等待冷却期过
        time.sleep(0.15)
        sent = manager.check_and_notify(findings, session_id="s1")
        assert len(sent) == 1
        assert len(mock.messages) == 2

    def test_different_finding_ids_not_deduped(self):
        """不同 finding_id 不互相影响"""
        mock = MockNotifier()
        manager = AlertManager(
            rules=[AlertRule(name="test", severity_threshold="low", cooldown_s=60)],
            notifiers=[mock],
        )

        findings = [make_finding(finding_id="a_001"), make_finding(finding_id="b_001")]
        sent = manager.check_and_notify(findings, session_id="s1")
        assert len(sent) == 2

    def test_channel_routing(self):
        """channels 过滤：只发送到指定渠道"""
        console = MockNotifier(name="console")
        webhook = MockNotifier(name="webhook")
        manager = AlertManager(
            rules=[AlertRule(name="webhook-only", severity_threshold="low", channels=["webhook"])],
            notifiers=[console, webhook],
        )

        findings = [make_finding(severity="critical")]
        manager.check_and_notify(findings, session_id="s1")

        assert len(console.messages) == 0
        assert len(webhook.messages) == 1

    def test_multiple_rules_different_channels(self):
        """多个规则匹配同一 finding 时，合并所有渠道"""
        mock_console = MockNotifier(name="console")
        mock_webhook = MockNotifier(name="webhook")
        manager = AlertManager(
            rules=[
                AlertRule(name="rule-a", condition=["flaky"], severity_threshold="low", channels=["console"]),
                AlertRule(name="rule-b", condition=["flaky"], severity_threshold="low", channels=["webhook"]),
            ],
            notifiers=[mock_console, mock_webhook],
        )

        findings = [make_finding(category="flaky_test", severity="medium")]
        manager.check_and_notify(findings, session_id="s1")

        assert len(mock_console.messages) == 1
        assert len(mock_webhook.messages) == 1

    def test_notifier_exception_doesnt_crash(self):
        """通知渠道异常不影响其他渠道"""
        fail = MockNotifier(name="fail", fail=True)
        ok = MockNotifier(name="ok")
        manager = AlertManager(
            rules=[AlertRule(name="test", severity_threshold="low")],
            notifiers=[fail, ok],
        )

        findings = [make_finding()]
        sent = manager.check_and_notify(findings, session_id="s1")

        assert len(sent) == 1  # 仍然算 sent（至少 ok 通了）
        assert len(ok.messages) == 1

    def test_file_notifier_integration(self, tmp_path):
        """FileNotifier 集成到 AlertManager"""
        log_file = tmp_path / "alerts.jsonl"
        manager = AlertManager(
            rules=[AlertRule(name="test", severity_threshold="low")],
            notifiers=[],
            alert_log_path=log_file,
        )

        findings = [make_finding(finding_id="file_001")]
        manager.check_and_notify(findings, session_id="s1")

        history = manager.get_alert_history()
        assert len(history) == 1
        assert history[0]["finding_id"] == "file_001"

    def test_clear_cooldown(self):
        """clear_cooldown 允许立即重新告警"""
        mock = MockNotifier()
        manager = AlertManager(
            rules=[AlertRule(name="test", severity_threshold="low", cooldown_s=600)],
            notifiers=[mock],
        )

        findings = [make_finding(finding_id="clr_001")]
        manager.check_and_notify(findings, session_id="s1")
        assert len(mock.messages) == 1

        # 冷却期内
        manager.check_and_notify(findings, session_id="s1")
        assert len(mock.messages) == 1

        # 清空冷却
        manager.clear_cooldown()
        manager.check_and_notify(findings, session_id="s1")
        assert len(mock.messages) == 2

    def test_test_notification(self, capsys):
        """test_notification 发送测试告警"""
        manager = AlertManager()
        msg = manager.test_notification(severity="critical")
        assert msg.finding_id == "test_alert_0000"
        assert msg.severity == "critical"

    def test_empty_findings(self):
        """空 findings 不报错"""
        mock = MockNotifier()
        manager = AlertManager(notifiers=[mock])
        sent = manager.check_and_notify([], session_id="s1")
        assert len(sent) == 0

    def test_default_rules(self):
        """默认规则包含 critical/high/flaky"""
        assert len(DEFAULT_RULES) == 3
        names = {r.name for r in DEFAULT_RULES}
        assert "critical-alerts" in names
        assert "high-severity" in names
        assert "flaky-tests" in names


# ─── Pipeline 集成测试 ────────────────────────────────────


class TestPipelineIntegration:
    def test_check_with_pipeline_findings(self):
        """模拟 Pipeline 输出的 confirmed_findings 格式"""
        mock = MockNotifier()
        manager = AlertManager(
            rules=[AlertRule(name="all", severity_threshold="medium")],
            notifiers=[mock],
        )

        # 模拟 Pipeline 的 confirmed_findings 输出格式
        pipeline_findings = [
            {
                "finding_id": "flaky_1234",
                "category": "flaky_test",
                "severity": "high",
                "description": "测试 test_login 既有通过又有失败",
                "evidence": {"event_ids": ["e1", "e2"], "snippet": "pass=3, fail=2"},
                "affected_tests": ["test_login"],
                "confidence": 0.9,
            },
            {
                "finding_id": "scene_slow_5678",
                "category": "scene_anomaly",
                "severity": "medium",
                "description": "场景 res://main.tscn 加载耗时 8000ms",
                "evidence": {"event_ids": ["e3"], "snippet": "duration_ms=8000"},
                "affected_tests": [],
                "confidence": 0.85,
            },
            {
                "finding_id": "low_severity_0001",
                "category": "coverage_gap",
                "severity": "low",
                "description": "低优先级问题",
                "evidence": {"event_ids": [], "snippet": ""},
                "affected_tests": [],
                "confidence": 0.7,
            },
        ]

        sent = manager.check_and_notify(pipeline_findings, session_id="pipeline-run-1")

        # medium 阈值：high 和 medium 应触发，low 不触发
        assert len(sent) == 2
        ids = {m.finding_id for m in sent}
        assert "flaky_1234" in ids
        assert "scene_slow_5678" in ids
        assert "low_severity_0001" not in ids

    def test_cooldown_across_sessions(self):
        """冷却期跨 session 生效"""
        mock = MockNotifier()
        manager = AlertManager(
            rules=[AlertRule(name="test", severity_threshold="low", cooldown_s=60)],
            notifiers=[mock],
        )

        findings = [make_finding(finding_id="cross_001")]

        manager.check_and_notify(findings, session_id="session-a")
        sent = manager.check_and_notify(findings, session_id="session-b")

        # 即使 session 不同，同一 finding_id 仍被去重
        assert len(sent) == 0
        assert len(mock.messages) == 1


# ─── Notifier 基类测试 ────────────────────────────────────


class TestNotifierBase:
    def test_alert_message_to_dict(self):
        """AlertMessage.to_dict 包含所有字段"""
        msg = AlertMessage(
            finding_id="f_001",
            category="test",
            severity="high",
            description="desc",
            session_id="s1",
            affected_tests=["t1"],
            suggested_fix="fix",
        )
        d = msg.to_dict()
        assert d["finding_id"] == "f_001"
        assert d["category"] == "test"
        assert d["affected_tests"] == ["t1"]
        assert d["suggested_fix"] == "fix"
        assert "timestamp" in d
