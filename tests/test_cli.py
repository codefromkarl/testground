"""CLI 测试"""

import json
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.medium

sys.path.insert(0, str(Path(__file__).parent.parent))

from cli import (
    cmd_analyze,
    cmd_import_events,
    cmd_import_report,
    cmd_sessions,
    cmd_stats,
    cmd_timeline,
)
from gateway.storage import Storage


@pytest.fixture
def db(tmp_path):
    return str(tmp_path / "cli_test.db")


@pytest.fixture
def storage(db):
    return Storage(db)


@pytest.fixture
def sample_report(tmp_path):
    """创建样例 loopexpedition 测试报告"""
    report = {
        "run_id": "test_run_001",
        "timestamp": "2026-05-24T10:00:00",
        "verdict": "PASS",
        "gate_result": {
            "verdict": "PASS",
            "rules": {
                "test_pass_rate": {"value": 1.0, "threshold": 1.0, "comparator": ">=", "pass": True},
                "flow_coverage": {"value": 0.95, "threshold": 0.9, "comparator": ">=", "pass": True},
            },
        },
        "phases": {
            "gdunit4": {"total": 50, "passed": 50, "failed": 0, "duration_s": 120},
            "bot": {"total_files": 10, "passed_files": 10, "crash_detected": False},
            "coverage": {
                "total_flows": 45,
                "covered": 43,
                "rate": 0.956,
            },
        },
        "duration_s": 300,
    }
    path = tmp_path / "test_report.json"
    path.write_text(json.dumps(report))
    return str(path)


class TestImportReport:
    def test_import_report(self, db, sample_report, capsys):
        args = type("Args", (), {"path": sample_report, "project": "loopexpedition", "db": db})()
        cmd_import_report(args)
        output = capsys.readouterr().out
        assert "导入完成" in output
        assert "门禁: PASS" in output

    def test_import_report_creates_events(self, db, sample_report):
        args = type("Args", (), {"path": sample_report, "project": "loopexpedition", "db": db})()
        cmd_import_report(args)
        storage = Storage(db)
        # import_report 只创建事件，不创建会话
        events = storage.get_events_by_project("loopexpedition", limit=100)
        assert len(events) > 0

    def test_import_nonexistent(self, db, capsys):
        args = type("Args", (), {"path": "/nonexistent.json", "project": "test", "db": db})()
        with pytest.raises(SystemExit):
            cmd_import_report(args)


class TestImportEvents:
    def test_import_events_list(self, db, tmp_path, capsys):
        events = [
            {
                "session_id": "test-sess",
                "source": {"framework": "vitest", "project": "test"},
                "type": "test.start",
                "data": {"test_name": "t1"},
            },
            {
                "session_id": "test-sess",
                "source": {"framework": "vitest", "project": "test"},
                "type": "test.end",
                "data": {"test_name": "t1", "passed": True, "duration_ms": 100},
            },
        ]
        path = tmp_path / "events.json"
        path.write_text(json.dumps(events))
        args = type("Args", (), {"path": str(path), "db": db})()
        cmd_import_events(args)
        output = capsys.readouterr().out
        assert "2 个事件" in output


class TestSessions:
    def test_list_sessions_empty(self, db, capsys):
        args = type("Args", (), {"project": None, "limit": 10, "db": db})()
        cmd_sessions(args)
        output = capsys.readouterr().out
        assert "暂无会话" in output

    def test_list_sessions_with_data(self, db, storage, capsys):
        import time

        from schema.events import ObsSession

        storage.store_session(
            ObsSession(
                session_id="test-1",
                project="travel-agent",
                framework="vitest",
                started_at=int(time.time() * 1000),
            )
        )
        args = type("Args", (), {"project": None, "limit": 10, "db": db})()
        cmd_sessions(args)
        output = capsys.readouterr().out
        assert "travel-agent" in output


class TestTimeline:
    def test_timeline_empty(self, db, capsys):
        args = type("Args", (), {"session_id": "empty", "type": None, "limit": 100, "db": db})()
        cmd_timeline(args)
        output = capsys.readouterr().out
        assert "无事件" in output

    def test_timeline_with_events(self, db, storage, capsys):
        from schema.events import EventSource, create_test_end, create_test_start

        source = EventSource(framework="vitest", project="test")
        storage.store_event(create_test_start("tl-sess", source, "t1", "t1"))
        storage.store_event(create_test_end("tl-sess", source, "t1", True, 100))
        args = type("Args", (), {"session_id": "tl-sess", "type": None, "limit": 100, "db": db})()
        cmd_timeline(args)
        output = capsys.readouterr().out
        assert "test.start" in output
        assert "test.end" in output


class TestAnalyze:
    def test_analyze_empty(self, db, capsys):
        args = type("Args", (), {"session_id": "empty", "db": db})()
        cmd_analyze(args)
        output = capsys.readouterr().out
        assert "无事件" in output

    def test_analyze_with_events(self, db, storage, capsys):
        from schema.events import EventSource, create_assertion, create_test_end, create_test_start

        source = EventSource(framework="vitest", project="test")
        storage.store_event(create_test_start("a-sess", source, "t1", "t1"))
        storage.store_event(create_assertion("a-sess", source, "a1", True))
        storage.store_event(create_test_end("a-sess", source, "t1", True, 100))
        args = type("Args", (), {"session_id": "a-sess", "db": db})()
        cmd_analyze(args)
        output = capsys.readouterr().out
        assert "bug_discovery" in output
        assert "quality_guard" in output


class TestStats:
    def test_stats_empty(self, db, capsys):
        args = type("Args", (), {"project": "empty", "days": 7, "db": db})()
        cmd_stats(args)
        output = capsys.readouterr().out
        assert "empty" in output

    def test_stats_with_data(self, db, storage, capsys):
        from schema.events import EventSource, create_test_end, create_test_start

        source = EventSource(framework="vitest", project="stats-test")
        storage.store_event(create_test_start("s-sess", source, "t1", "t1"))
        storage.store_event(create_test_end("s-sess", source, "t1", True, 100))
        args = type("Args", (), {"project": "stats-test", "days": 7, "db": db})()
        cmd_stats(args)
        output = capsys.readouterr().out
        assert "stats-test" in output
        assert "test.end" in output


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
