"""报告生成器测试 — HTML / JSON / Markdown 格式 + Gateway 端点"""

import json
import sys
import time
import uuid
from pathlib import Path

import pytest

pytestmark = pytest.mark.medium

sys.path.insert(0, str(Path(__file__).parent.parent))

from analyzers.report import ReportGenerator
from analyzers.report_templates import render_html, render_json, render_markdown
from gateway.storage import Storage
from schema.events import EventSource, ObsEvent, ObsSession


# ─── 测试数据工厂 ─────────────────────────────────────────


def _make_storage(tmp_path) -> Storage:
    """创建内存 Storage 并注入测试数据"""
    storage = Storage(":memory:")
    session_id = "test-report-session"

    # 存储会话
    session = ObsSession(
        session_id=session_id,
        project="test-game",
        framework="godot",
        started_at=int(time.time() * 1000) - 60000,
        ended_at=int(time.time() * 1000),
        total_tests=8,
        passed_tests=6,
        failed_tests=2,
        duration_ms=54000,
        gate_result={"verdict": "FAIL", "rules": {"pass_rate": {"value": 0.75}}},
    )
    storage.store_session(session)

    # 存储事件
    base_ts = int(time.time() * 1000) - 60000
    source = EventSource(framework="godot", project="test-game")

    events = [
        # test events
        ObsEvent(event_id="e1", session_id=session_id, timestamp=base_ts, source=source,
                 type="test.start", data={"test_name": "test_scene_load"}),
        ObsEvent(event_id="e2", session_id=session_id, timestamp=base_ts + 100, source=source,
                 type="test.end", data={"test_name": "test_scene_load", "duration_ms": 250}),
        ObsEvent(event_id="e3", session_id=session_id, timestamp=base_ts + 200, source=source,
                 type="test.start", data={"test_name": "test_click_button"}),
        ObsEvent(event_id="e4", session_id=session_id, timestamp=base_ts + 300, source=source,
                 type="test.end", data={"test_name": "test_click_button", "duration_ms": 80}),
        ObsEvent(event_id="e5", session_id=session_id, timestamp=base_ts + 400, source=source,
                 type="test.start", data={"test_name": "test_flaky"}),
        ObsEvent(event_id="e6", session_id=session_id, timestamp=base_ts + 500, source=source,
                 type="test.end", data={"test_name": "test_flaky", "duration_ms": 200}),
        ObsEvent(event_id="e7", session_id=session_id, timestamp=base_ts + 600, source=source,
                 type="test.start", data={"test_name": "test_flaky"}),
        ObsEvent(event_id="e8", session_id=session_id, timestamp=base_ts + 700, source=source,
                 type="test.fail", data={"test_name": "test_flaky", "duration_ms": 300}),
        ObsEvent(event_id="e9", session_id=session_id, timestamp=base_ts + 800, source=source,
                 type="test.start", data={"test_name": "test_no_assert"}),
        ObsEvent(event_id="e10", session_id=session_id, timestamp=base_ts + 900, source=source,
                 type="test.end", data={"test_name": "test_no_assert", "duration_ms": 50}),
        # bench events
        ObsEvent(event_id="e11", session_id=session_id, timestamp=base_ts + 1000, source=source,
                 type="bench.build_health", data={"dimension": "build_health", "score": 92.0}),
        ObsEvent(event_id="e12", session_id=session_id, timestamp=base_ts + 1100, source=source,
                 type="bench.visual_usability", data={"dimension": "visual_usability", "score": 75.0}),
        ObsEvent(event_id="e13", session_id=session_id, timestamp=base_ts + 1200, source=source,
                 type="bench.intent_alignment", data={"dimension": "intent_alignment", "score": 80.0}),
        # assert events
        ObsEvent(event_id="e14", session_id=session_id, timestamp=base_ts + 1300, source=source,
                 type="assert.pass", data={"test_name": "test_scene_load"}),
        ObsEvent(event_id="e15", session_id=session_id, timestamp=base_ts + 1400, source=source,
                 type="assert.fail", data={"test_name": "test_flaky"}),
    ]
    storage.store_events_batch(events)

    # 存储分析结果
    from schema.events import AnalysisResult

    analysis = AnalysisResult(
        analysis_id="analysis-test-1",
        session_id=session_id,
        timestamp=int(time.time() * 1000),
        analyzer="bug_discovery",
        findings=[
            {
                "finding_id": "flaky_001",
                "category": "flaky_test",
                "severity": "high",
                "description": "测试 test_flaky 既有通过又有失败，是 flaky test",
                "affected_tests": ["test_flaky"],
                "confidence": 0.9,
            }
        ],
        confidence=0.85,
        summary="发现 1 个 flaky test",
        recommendations=["建议修复 flaky test 以提高 CI 稳定性"],
    )
    storage.store_analysis(analysis)

    return storage, session_id


# ─── 模板渲染测试 ─────────────────────────────────────────


class TestReportTemplates:
    """模板渲染单元测试"""

    @pytest.fixture
    def sample_data(self):
        return {
            "title": "测试质量分析报告",
            "session_id": "sess-test-123",
            "generated_at": "2025-01-01 12:00:00",
            "summary": "分析了 20 个事件，发现 2 个问题，质量分 80/100。",
            "quality_score": 80.0,
            "findings": [
                {
                    "finding_id": "f1",
                    "category": "flaky_test",
                    "severity": "high",
                    "description": "test_flaky 既有通过又有失败",
                    "affected_tests": ["test_flaky"],
                    "confidence": 0.9,
                },
                {
                    "finding_id": "f2",
                    "category": "assertion_gap",
                    "severity": "medium",
                    "description": "test_no_assert 没有任何断言",
                    "affected_tests": ["test_no_assert"],
                    "confidence": 0.85,
                },
            ],
            "bench_scores": {
                "build_health": 92.0,
                "visual_usability": 75.0,
                "intent_alignment": 80.0,
            },
            "event_stats": {
                "test.start": 10,
                "test.end": 8,
                "test.fail": 2,
                "assert.pass": 5,
            },
            "recommendations": [
                "存在 flaky test，建议优先修复以提高 CI 稳定性",
                "存在无断言的测试，建议添加验证逻辑",
            ],
            "session_info": {
                "project": "test-game",
                "framework": "godot",
                "started_at": "2025-01-01 11:00:00",
            },
        }

    def test_render_html_contains_title(self, sample_data):
        html = render_html(sample_data)
        assert "测试质量分析报告" in html

    def test_render_html_contains_quality_score(self, sample_data):
        html = render_html(sample_data)
        assert "80" in html
        assert "质量分" in html

    def test_render_html_contains_findings(self, sample_data):
        html = render_html(sample_data)
        assert "test_flaky" in html
        assert "test_no_assert" in html
        assert "flaky_test" in html

    def test_render_html_contains_bench_scores(self, sample_data):
        html = render_html(sample_data)
        assert "构建健康" in html
        assert "视觉可用性" in html
        assert "92" in html

    def test_render_html_contains_event_stats(self, sample_data):
        html = render_html(sample_data)
        assert "test.start" in html
        assert "事件统计" in html

    def test_render_html_contains_recommendations(self, sample_data):
        html = render_html(sample_data)
        assert "建议" in html
        assert "flaky" in html

    def test_render_html_has_inline_css(self, sample_data):
        html = render_html(sample_data)
        assert "<style>" in html
        assert "background" in html
        assert "</style>" in html

    def test_render_html_self_contained(self, sample_data):
        """HTML 报告应该是自包含的，无外部资源引用"""
        html = render_html(sample_data)
        assert "href=" not in html or "data:" in html  # 无外部 CSS 链接
        assert "src=" not in html or "data:" in html  # 无外部 JS/图片

    def test_render_html_valid_structure(self, sample_data):
        html = render_html(sample_data)
        assert "<!DOCTYPE html>" in html
        assert "<html" in html
        assert "</html>" in html
        assert "<head>" in html
        assert "<body>" in html

    def test_render_json_valid(self, sample_data):
        json_str = render_json(sample_data)
        parsed = json.loads(json_str)
        assert parsed["title"] == "测试质量分析报告"
        assert parsed["quality_score"] == 80.0
        assert len(parsed["findings"]) == 2

    def test_render_json_pretty_printed(self, sample_data):
        json_str = render_json(sample_data)
        # 应该有缩进
        assert "\n  " in json_str

    def test_render_markdown_contains_title(self, sample_data):
        md = render_markdown(sample_data)
        assert "# 测试质量分析报告" in md

    def test_render_markdown_contains_findings_table(self, sample_data):
        md = render_markdown(sample_data)
        assert "| 严重度 |" in md
        assert "test_flaky" in md

    def test_render_markdown_contains_bench(self, sample_data):
        md = render_markdown(sample_data)
        assert "Bench 三维评分" in md
        assert "构建健康" in md

    def test_render_markdown_contains_recommendations(self, sample_data):
        md = render_markdown(sample_data)
        assert "## 💡 建议" in md
        assert "flaky" in md

    def test_render_html_no_findings(self):
        data = {
            "title": "Empty Report",
            "session_id": "s1",
            "generated_at": "2025-01-01",
            "summary": "No issues",
            "quality_score": 100,
            "findings": [],
            "bench_scores": {},
            "event_stats": {},
            "recommendations": [],
            "session_info": {},
        }
        html = render_html(data)
        assert "未发现问题" in html

    def test_render_html_escapes_xss(self):
        """HTML 报告应对特殊字符转义"""
        data = {
            "title": "<script>alert('xss')</script>",
            "session_id": "s1",
            "generated_at": "2025",
            "summary": "",
            "quality_score": 0,
            "findings": [
                {
                    "severity": "high",
                    "category": "<b>xss</b>",
                    "description": "<img onerror=alert(1)>",
                    "affected_tests": [],
                    "confidence": 0,
                }
            ],
            "bench_scores": {},
            "event_stats": {},
            "recommendations": ["<script>"],
            "session_info": {},
        }
        html = render_html(data)
        assert "<script>" not in html
        assert "&lt;script&gt;" in html


# ─── ReportGenerator 集成测试 ─────────────────────────────


class TestReportGenerator:
    """ReportGenerator 集成测试"""

    @pytest.fixture
    def setup(self, tmp_path):
        storage, session_id = _make_storage(tmp_path)
        generator = ReportGenerator(storage)
        return generator, session_id, tmp_path

    def test_generate_html_report(self, setup):
        generator, session_id, tmp_path = setup
        output_dir = tmp_path / "reports"
        path = generator.generate(session_id, format="html", output_dir=output_dir)

        assert path.exists()
        assert path.suffix == ".html"
        content = path.read_text(encoding="utf-8")
        assert "<!DOCTYPE html>" in content
        assert "测试质量分析报告" in content

    def test_generate_json_report(self, setup):
        generator, session_id, tmp_path = setup
        output_dir = tmp_path / "reports"
        path = generator.generate(session_id, format="json", output_dir=output_dir)

        assert path.exists()
        assert path.suffix == ".json"
        content = path.read_text(encoding="utf-8")
        parsed = json.loads(content)
        assert "title" in parsed
        assert "findings" in parsed

    def test_generate_markdown_report(self, setup):
        generator, session_id, tmp_path = setup
        output_dir = tmp_path / "reports"
        path = generator.generate(session_id, format="md", output_dir=output_dir)

        assert path.exists()
        assert path.suffix == ".md"
        content = path.read_text(encoding="utf-8")
        assert "# 测试质量分析报告" in content

    def test_report_contains_findings(self, setup):
        generator, session_id, tmp_path = setup
        content = generator.generate_string(session_id, format="json")
        parsed = json.loads(content)

        assert len(parsed["findings"]) >= 1
        categories = [f.get("category") for f in parsed["findings"]]
        assert "flaky_test" in categories

    def test_report_contains_quality_score(self, setup):
        generator, session_id, _ = setup
        content = generator.generate_string(session_id, format="json")
        parsed = json.loads(content)

        assert "quality_score" in parsed
        assert parsed["quality_score"] > 0

    def test_report_contains_recommendations(self, setup):
        generator, session_id, _ = setup
        content = generator.generate_string(session_id, format="json")
        parsed = json.loads(content)

        assert len(parsed["recommendations"]) >= 1

    def test_report_contains_bench_scores(self, setup):
        generator, session_id, _ = setup
        content = generator.generate_string(session_id, format="json")
        parsed = json.loads(content)

        assert "bench_scores" in parsed
        assert parsed["bench_scores"].get("build_health") == 92.0
        assert parsed["bench_scores"].get("visual_usability") == 75.0

    def test_report_contains_event_stats(self, setup):
        generator, session_id, _ = setup
        content = generator.generate_string(session_id, format="json")
        parsed = json.loads(content)

        assert "event_stats" in parsed
        assert parsed["event_stats"].get("test.start") == 5
        assert parsed["event_stats"].get("test.end") == 4
        assert parsed["event_stats"].get("test.fail") == 1

    def test_report_contains_session_info(self, setup):
        generator, session_id, _ = setup
        content = generator.generate_string(session_id, format="json")
        parsed = json.loads(content)

        info = parsed["session_info"]
        assert info["project"] == "test-game"
        assert info["framework"] == "godot"

    def test_generate_string_all_formats(self, setup):
        generator, session_id, _ = setup
        html = generator.generate_string(session_id, format="html")
        json_str = generator.generate_string(session_id, format="json")
        md = generator.generate_string(session_id, format="md")

        assert len(html) > 0
        assert len(json_str) > 0
        assert len(md) > 0

        # 内容不应完全相同
        assert html != json_str
        assert json_str != md

    def test_invalid_format_raises(self, setup):
        generator, session_id, _ = setup
        with pytest.raises(ValueError, match="Unsupported format"):
            generator.generate_string(session_id, format="pdf")

    def test_nonexistent_session(self, setup):
        generator, _, tmp_path = setup
        # 不应崩溃，但返回空报告
        content = generator.generate_string("nonexistent-session", format="json")
        parsed = json.loads(content)
        assert parsed["findings"] == []
        assert parsed["event_stats"] == {}

    def test_html_report_visual_score_display(self, setup):
        """HTML 报告应包含可视化的质量分展示"""
        generator, session_id, _ = setup
        html = generator.generate_string(session_id, format="html")

        # 应有进度条或分数框
        assert "score" in html.lower()
        # 应有内嵌样式（颜色等）
        assert "#" in html  # CSS color code


# ─── Gateway 端点测试 ─────────────────────────────────────


class TestReportGatewayEndpoint:
    """Gateway /sessions/{id}/report 端点测试"""

    @pytest.fixture
    def client(self, tmp_path):
        """创建 FastAPI 测试客户端"""
        pytest.importorskip("fastapi.testclient")
        from fastapi.testclient import TestClient

        # 准备内存数据库
        storage = Storage(":memory:")
        session_id = "gw-test-session"

        # 注入测试数据
        session = ObsSession(
            session_id=session_id,
            project="gw-test",
            framework="vitest",
            started_at=int(time.time() * 1000) - 30000,
            ended_at=int(time.time() * 1000),
        )
        storage.store_session(session)

        source = EventSource(framework="vitest", project="gw-test")
        base_ts = int(time.time() * 1000) - 30000
        events = [
            ObsEvent(event_id="gw1", session_id=session_id, timestamp=base_ts, source=source,
                     type="test.start", data={"test_name": "t1"}),
            ObsEvent(event_id="gw2", session_id=session_id, timestamp=base_ts + 100, source=source,
                     type="test.end", data={"test_name": "t1", "duration_ms": 50}),
        ]
        storage.store_events_batch(events)

        # 创建 app
        from fastapi import FastAPI
        app = FastAPI()
        app.state.storage = storage

        from gateway.routes.reports import router
        app.include_router(router)

        return TestClient(app), session_id

    def test_html_report_endpoint(self, client):
        test_client, session_id = client
        resp = test_client.get(f"/sessions/{session_id}/report?format=html")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]
        assert "<!DOCTYPE html>" in resp.text

    def test_json_report_endpoint(self, client):
        test_client, session_id = client
        resp = test_client.get(f"/sessions/{session_id}/report?format=json")
        assert resp.status_code == 200
        data = resp.json()
        assert "title" in data
        assert "findings" in data

    def test_md_report_endpoint(self, client):
        test_client, session_id = client
        resp = test_client.get(f"/sessions/{session_id}/report?format=md")
        assert resp.status_code == 200
        assert "text/markdown" in resp.headers["content-type"]
        assert "# " in resp.text

    def test_default_format_is_html(self, client):
        test_client, session_id = client
        resp = test_client.get(f"/sessions/{session_id}/report")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]

    def test_404_for_nonexistent_session(self, client):
        test_client, _ = client
        resp = test_client.get("/sessions/does-not-exist/report")
        assert resp.status_code == 404

    def test_invalid_format_rejected(self, client):
        test_client, session_id = client
        resp = test_client.get(f"/sessions/{session_id}/report?format=pdf")
        assert resp.status_code == 422  # validation error
