"""报告模板 — HTML / Markdown / JSON 格式化

所有模板使用字符串格式化，不依赖 Jinja2 等外部模板引擎。
HTML 模板内嵌 CSS，单文件即可打开。
"""

from __future__ import annotations

import html as html_lib
import json
from typing import Any, Dict, List


# ─── 辅助函数 ─────────────────────────────────────────────


def _severity_icon(severity: str) -> str:
    """severity → emoji"""
    return {"critical": "🔴", "high": "🟠", "medium": "🟡", "low": "🟢"}.get(severity, "⚪")


def _severity_label(severity: str) -> str:
    """severity → 中文标签"""
    return {"critical": "严重", "high": "高", "medium": "中", "low": "低"}.get(severity, "未知")


def _score_color(score: float) -> str:
    """质量分 → CSS 颜色"""
    if score >= 80:
        return "#22c55e"
    elif score >= 60:
        return "#f59e0b"
    else:
        return "#ef4444"


def _score_bar_html(score: float, max_score: float = 100) -> str:
    """生成质量分进度条 HTML"""
    pct = min(score / max_score * 100, 100)
    color = _score_color(score)
    return (
        f'<div style="background:#1e293b;border-radius:8px;height:28px;width:100%;overflow:hidden;margin:8px 0">'
        f'<div style="background:{color};height:100%;width:{pct:.1f}%;border-radius:8px;'
        f'display:flex;align-items:center;justify-content:center;color:#fff;font-weight:700;font-size:14px">'
        f'{score:.0f}/{max_score:.0f}</div></div>'
    )


# ─── HTML 模板 ────────────────────────────────────────────


def render_html(data: Dict[str, Any]) -> str:
    """渲染 HTML 报告（内嵌 CSS，单文件可用）"""
    title = html_lib.escape(data.get("title", "测试报告"))
    session_id = html_lib.escape(data.get("session_id", ""))
    generated_at = html_lib.escape(data.get("generated_at", ""))
    summary = html_lib.escape(data.get("summary", ""))

    quality_score = data.get("quality_score", 0)
    findings = data.get("findings", [])
    bench_scores = data.get("bench_scores", {})
    event_stats = data.get("event_stats", {})
    recommendations = data.get("recommendations", [])
    session_info = data.get("session_info", {})

    # ── findings 表格
    findings_rows = ""
    for f in findings:
        sev = f.get("severity", "info")
        icon = _severity_icon(sev)
        cat = html_lib.escape(str(f.get("category", "")))
        desc = html_lib.escape(str(f.get("description", "")))
        affected = ", ".join(html_lib.escape(t) for t in f.get("affected_tests", []))
        confidence = f.get("confidence", 0)
        findings_rows += (
            f"<tr>"
            f'<td>{icon} {_severity_label(sev)}</td>'
            f"<td>{cat}</td>"
            f"<td>{desc}</td>"
            f"<td>{affected}</td>"
            f"<td>{confidence:.0%}</td>"
            f"</tr>\n"
        )

    findings_section = ""
    if findings:
        findings_section = f"""
<h2>🔍 发现的问题 ({len(findings)})</h2>
<table>
<thead><tr><th>严重度</th><th>分类</th><th>描述</th><th>影响测试</th><th>置信度</th></tr></thead>
<tbody>{findings_rows}</tbody>
</table>"""
    else:
        findings_section = "<h2>🔍 发现的问题</h2><p>✅ 未发现问题</p>"

    # ── bench 评分
    bench_section = ""
    if bench_scores:
        bench_items = ""
        dim_labels = {
            "build_health": "🏗️ 构建健康",
            "visual_usability": "🎨 视觉可用性",
            "intent_alignment": "🎯 意图对齐",
        }
        for dim, score in bench_scores.items():
            label = dim_labels.get(dim, html_lib.escape(dim))
            bench_items += f"<div class='bench-item'><span class='bench-label'>{label}</span>{_score_bar_html(score)}<span class='bench-score'>{score:.0f}</span></div>"
        bench_section = f"<h2>📊 Bench 三维评分</h2><div class='bench-grid'>{bench_items}</div>"

    # ── 事件统计
    event_stats_section = ""
    if event_stats:
        rows = ""
        for etype, count in sorted(event_stats.items(), key=lambda x: -x[1]):
            rows += f"<tr><td><code>{html_lib.escape(str(etype))}</code></td><td>{count}</td></tr>\n"
        event_stats_section = f"""
<h2>📈 事件统计</h2>
<table>
<thead><tr><th>事件类型</th><th>数量</th></tr></thead>
<tbody>{rows}</tbody>
</table>"""

    # ── 建议
    recs_section = ""
    if recommendations:
        items = "".join(f"<li>{html_lib.escape(r)}</li>" for r in recommendations)
        recs_section = f"<h2>💡 建议</h2><ul>{items}</ul>"

    # ── 会话信息
    session_section = ""
    if session_info:
        items = ""
        for k, v in session_info.items():
            items += f"<tr><td>{html_lib.escape(str(k))}</td><td>{html_lib.escape(str(v))}</td></tr>\n"
        session_section = f"<h2>📋 会话信息</h2><table><tbody>{items}</tbody></table>"

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title}</title>
<style>
  :root {{ --bg: #0f172a; --card: #1e293b; --text: #e2e8f0; --muted: #94a3b8;
           --accent: #38bdf8; --border: #334155; }}
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
         background: var(--bg); color: var(--text); line-height: 1.6; padding: 24px; }}
  .container {{ max-width: 960px; margin: 0 auto; }}
  h1 {{ font-size: 1.8rem; margin-bottom: 4px; }}
  h2 {{ font-size: 1.2rem; margin: 28px 0 12px; padding-bottom: 6px; border-bottom: 1px solid var(--border); }}
  .meta {{ color: var(--muted); font-size: 0.85rem; margin-bottom: 20px; }}
  .summary {{ background: var(--card); padding: 16px; border-radius: 8px; margin-bottom: 20px; }}
  .score-box {{ display: inline-block; background: var(--card); border: 2px solid {_score_color(quality_score)};
                border-radius: 12px; padding: 12px 24px; text-align: center; margin: 8px 0 16px; }}
  .score-num {{ font-size: 2.4rem; font-weight: 800; color: {_score_color(quality_score)}; }}
  .score-label {{ font-size: 0.8rem; color: var(--muted); }}
  table {{ width: 100%; border-collapse: collapse; font-size: 0.9rem; }}
  th, td {{ padding: 8px 10px; text-align: left; border-bottom: 1px solid var(--border); }}
  th {{ background: var(--card); color: var(--muted); font-weight: 600; }}
  tr:hover {{ background: rgba(56,189,248,0.04); }}
  code {{ background: var(--card); padding: 2px 6px; border-radius: 4px; font-size: 0.85rem; }}
  ul {{ padding-left: 20px; }}
  li {{ margin: 6px 0; }}
  .bench-grid {{ display: flex; flex-direction: column; gap: 12px; }}
  .bench-item {{ display: flex; align-items: center; gap: 12px; background: var(--card);
                 padding: 12px 16px; border-radius: 8px; }}
  .bench-label {{ min-width: 140px; font-weight: 600; }}
  .bench-score {{ font-weight: 700; min-width: 48px; text-align: right; }}
  @media (max-width: 640px) {{
    .bench-item {{ flex-direction: column; align-items: flex-start; }}
    table {{ font-size: 0.8rem; }}
  }}
</style>
</head>
<body>
<div class="container">
  <h1>{title}</h1>
  <div class="meta">Session: {session_id} · 生成时间: {generated_at}</div>

  <div class="score-box">
    <div class="score-num">{quality_score:.0f}</div>
    <div class="score-label">质量分 / 100</div>
  </div>

  <div class="summary">{html_lib.escape(summary)}</div>

  {findings_section}
  {bench_section}
  {event_stats_section}
  {recs_section}
  {session_section}
</div>
</body>
</html>"""


# ─── Markdown 模板 ────────────────────────────────────────


def render_markdown(data: Dict[str, Any]) -> str:
    """渲染 Markdown 报告"""
    title = data.get("title", "测试报告")
    session_id = data.get("session_id", "")
    generated_at = data.get("generated_at", "")
    summary = data.get("summary", "")
    quality_score = data.get("quality_score", 0)
    findings = data.get("findings", [])
    bench_scores = data.get("bench_scores", {})
    event_stats = data.get("event_stats", {})
    recommendations = data.get("recommendations", [])
    session_info = data.get("session_info", {})

    lines = [
        f"# {title}",
        "",
        f"- **Session**: `{session_id}`",
        f"- **生成时间**: {generated_at}",
        f"- **质量分**: {quality_score:.0f} / 100",
        "",
        "## 📝 摘要",
        "",
        summary,
        "",
    ]

    # Findings
    if findings:
        lines += [f"## 🔍 发现的问题 ({len(findings)})", ""]
        lines += ["| 严重度 | 分类 | 描述 | 影响测试 | 置信度 |", "| --- | --- | --- | --- | --- |"]
        for f in findings:
            sev = f.get("severity", "info")
            icon = _severity_icon(sev)
            cat = f.get("category", "")
            desc = f.get("description", "")
            affected = ", ".join(f.get("affected_tests", [])) or "-"
            conf = f.get("confidence", 0)
            lines.append(f"| {icon} {_severity_label(sev)} | {cat} | {desc} | {affected} | {conf:.0%} |")
        lines.append("")
    else:
        lines += ["## 🔍 发现的问题", "", "✅ 未发现问题", ""]

    # Bench scores
    if bench_scores:
        dim_labels = {
            "build_health": "🏗️ 构建健康",
            "visual_usability": "🎨 视觉可用性",
            "intent_alignment": "🎯 意图对齐",
        }
        lines += ["## 📊 Bench 三维评分", ""]
        for dim, score in bench_scores.items():
            label = dim_labels.get(dim, dim)
            bar = "█" * int(score / 5) + "░" * (20 - int(score / 5))
            lines.append(f"- **{label}**: {bar} {score:.0f}/100")
        lines.append("")

    # Event stats
    if event_stats:
        lines += ["## 📈 事件统计", ""]
        lines += ["| 事件类型 | 数量 |", "| --- | --- |"]
        for etype, count in sorted(event_stats.items(), key=lambda x: -x[1]):
            lines.append(f"| `{etype}` | {count} |")
        lines.append("")

    # Recommendations
    if recommendations:
        lines += ["## 💡 建议", ""]
        for r in recommendations:
            lines.append(f"- {r}")
        lines.append("")

    # Session info
    if session_info:
        lines += ["## 📋 会话信息", ""]
        for k, v in session_info.items():
            lines.append(f"- **{k}**: {v}")
        lines.append("")

    return "\n".join(lines)


# ─── JSON 格式 ───────────────────────────────────────────


def render_json(data: Dict[str, Any]) -> str:
    """渲染 JSON 报告"""
    return json.dumps(data, indent=2, ensure_ascii=False)
