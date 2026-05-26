"""报告生成器 — 从 session 数据生成测试报告

支持 HTML / JSON / Markdown 三种格式。
数据来源: Storage 中的事件 + 分析结果 + 会话信息。
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from .report_templates import render_html, render_json, render_markdown

logger = logging.getLogger(__name__)


class ReportGenerator:
    """测试报告生成器。

    用法:
        from gateway.storage import Storage
        storage = Storage("test_observability.db")
        gen = ReportGenerator(storage)

        # 生成 HTML 报告
        path = gen.generate("session-id", format="html")

        # 生成 JSON 报告到指定目录
        path = gen.generate("session-id", format="json", output_dir=Path("./reports"))
    """

    def __init__(self, storage: Any, pipeline_state_db: Optional[str] = None):
        """
        Args:
            storage: Storage 实例（gateway.storage.Storage）
            pipeline_state_db: PipelineState 数据库路径（可选，用于获取 pipeline 分析结果）
        """
        self.storage = storage
        self.pipeline_state_db = pipeline_state_db

    def generate(
        self,
        session_id: str,
        format: str = "html",
        output_dir: Optional[Path] = None,
    ) -> Path:
        """生成报告文件。

        Args:
            session_id: 测试会话 ID
            format: 输出格式 — 'html' / 'json' / 'md'
            output_dir: 输出目录（默认 ./reports）

        Returns:
            生成的报告文件路径
        """
        # 收集数据
        data = self._collect_data(session_id)

        # 渲染
        format = format.lower()
        if format == "html":
            content = render_html(data)
            ext = "html"
        elif format == "json":
            content = render_json(data)
            ext = "json"
        elif format in ("md", "markdown"):
            content = render_markdown(data)
            ext = "md"
        else:
            raise ValueError(f"Unsupported format: {format!r}. Use 'html', 'json', or 'md'.")

        # 写入文件
        out_dir = output_dir or Path("reports")
        out_dir.mkdir(parents=True, exist_ok=True)

        ts = time.strftime("%Y%m%d_%H%M%S")
        filename = f"report_{session_id[:24]}_{ts}.{ext}"
        filepath = out_dir / filename

        filepath.write_text(content, encoding="utf-8")
        logger.info("报告已生成: %s", filepath)
        return filepath

    def generate_string(
        self,
        session_id: str,
        format: str = "html",
    ) -> str:
        """生成报告内容字符串（不写文件）。

        Args:
            session_id: 测试会话 ID
            format: 输出格式 — 'html' / 'json' / 'md'

        Returns:
            渲染后的报告内容
        """
        data = self._collect_data(session_id)
        format = format.lower()
        if format == "html":
            return render_html(data)
        elif format == "json":
            return render_json(data)
        elif format in ("md", "markdown"):
            return render_markdown(data)
        else:
            raise ValueError(f"Unsupported format: {format!r}")

    def _collect_data(self, session_id: str) -> Dict[str, Any]:
        """从存储中收集报告所需数据"""
        # 会话信息
        session = self.storage.get_session(session_id) or {}
        session_info = {
            "session_id": session_id,
            "project": session.get("project", "unknown"),
            "framework": session.get("framework", "unknown"),
        }
        if session.get("started_at"):
            session_info["started_at"] = time.strftime(
                "%Y-%m-%d %H:%M:%S", time.localtime(session["started_at"] / 1000)
            )
        if session.get("ended_at"):
            session_info["ended_at"] = time.strftime(
                "%Y-%m-%d %H:%M:%S", time.localtime(session["ended_at"] / 1000)
            )
        if session.get("duration_ms"):
            session_info["duration_ms"] = f"{session['duration_ms']}ms"
        if session.get("gate_result"):
            gr = session["gate_result"]
            session_info["gate_verdict"] = gr.get("verdict", "N/A")

        # 事件列表
        events = self.storage.get_session_events(session_id, limit=50000)

        # 事件统计
        event_stats: Dict[str, int] = {}
        for e in events:
            t = e["type"]
            event_stats[t] = event_stats.get(t, 0) + 1

        # 分析结果（从 ai_analyses 表）
        analyses = self.storage.get_session_analyses(session_id)

        # Pipeline 结果（从 PipelineState）
        pipeline_findings, pipeline_score, pipeline_recs = self._get_pipeline_results(session_id, events)

        # 合并 findings
        all_findings: List[Dict[str, Any]] = []

        # 从 analyses 表
        for analysis in analyses:
            for f in analysis.get("findings", []):
                all_findings.append(f)

        # 从 pipeline
        all_findings.extend(pipeline_findings)

        # 去重（按 finding_id）
        seen_ids: set = set()
        unique_findings: List[Dict[str, Any]] = []
        for f in all_findings:
            fid = f.get("finding_id", f.get("id", ""))
            if fid and fid in seen_ids:
                continue
            if fid:
                seen_ids.add(fid)
            unique_findings.append(f)

        # 质量分
        quality_score = pipeline_score
        if quality_score == 0 and analyses:
            # 从 analyses 中取平均 confidence * 100
            confidences = [a.get("confidence", 0) for a in analyses]
            if confidences:
                quality_score = sum(confidences) / len(confidences) * 100

        # 建议
        recommendations = list(pipeline_recs)
        for analysis in analyses:
            for r in analysis.get("recommendations", []):
                if r not in recommendations:
                    recommendations.append(r)

        # Bench 评分（从 bench.* 事件提取）
        bench_scores = self._extract_bench_scores(events)

        # 摘要
        total = len(events)
        confirmed = len(unique_findings)
        summary = (
            f"分析了 {total} 个事件，发现 {confirmed} 个问题，质量分 {quality_score:.0f}/100。"
            if total > 0
            else "暂无事件数据。"
        )

        return {
            "title": "测试质量分析报告",
            "session_id": session_id,
            "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "summary": summary,
            "quality_score": quality_score,
            "findings": unique_findings,
            "bench_scores": bench_scores,
            "event_stats": event_stats,
            "recommendations": recommendations,
            "session_info": session_info,
        }

    def _get_pipeline_results(
        self, session_id: str, events: List[Dict[str, Any]]
    ) -> tuple:
        """从 PipelineState 获取分析结果（如果可用）。

        Returns:
            (findings, quality_score, recommendations)
        """
        if not self.pipeline_state_db:
            # 尝试默认路径
            db_path = Path("pipeline_state.db")
            if not db_path.exists():
                return [], 0, []
        else:
            db_path = Path(self.pipeline_state_db)
            if not db_path.exists():
                return [], 0, []

        try:
            from .pipeline.state import PipelineState

            state = PipelineState(db_path)

            # 找到该 session 最近的 completed run
            import sqlite3

            conn = sqlite3.connect(str(db_path))
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT run_id FROM pipeline_runs WHERE session_id = ? AND status = 'completed' ORDER BY rowid DESC LIMIT 1",
                (session_id,),
            ).fetchone()
            conn.close()

            if not row:
                state.close()
                return [], 0, []

            run_id = row["run_id"]
            confirmed = state.get_confirmed_findings(run_id)
            run = state.get_run(run_id)

            findings = [f.raw_json for f in confirmed]

            # 从 run 的 report 字段获取 quality_score 和 recommendations
            quality_score = 0.0
            recommendations: List[str] = []
            if run and run.get("report"):
                report = run["report"]
                quality_score = report.get("metrics", {}).get("quality_score", 0)
                recommendations = report.get("recommendations", [])

            state.close()
            return findings, quality_score, recommendations

        except Exception as e:
            logger.debug("无法读取 PipelineState: %s", e)
            return [], 0, []

    def _extract_bench_scores(self, events: List[Dict[str, Any]]) -> Dict[str, float]:
        """从 bench.* 事件中提取三维评分"""
        scores: Dict[str, float] = {}
        for e in events:
            if e["type"].startswith("bench."):
                dim = e.get("data", {}).get("dimension", e["type"].replace("bench.", ""))
                score = e.get("data", {}).get("score", None)
                if score is not None:
                    # 取最新分数
                    scores[dim] = float(score)

        # 也检查 game.bench_result 事件
        for e in events:
            if e["type"] == "game.bench_result":
                data = e.get("data", {})
                for dim in ("build_health", "visual_usability", "intent_alignment"):
                    if dim in data:
                        scores[dim] = float(data[dim])

        return scores
