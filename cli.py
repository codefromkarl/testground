"""CLI 工具 — 导入测试报告、查询数据、运行分析

用法:
    python -m cli import-report <path>     # 导入 loopexpedition 测试报告
    python -m cli import-events <path>     # 导入事件 JSON 文件
    python -m cli sessions                 # 列出会话
    python -m cli timeline <session_id>    # 查看时间线
    python -m cli analyze <session_id>     # 运行 AI 分析
    python -m cli stats <project>          # 项目统计
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import uuid
from pathlib import Path

# 确保项目根目录在 path 中
_root = Path(__file__).parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from schema.events import EventSource, TestEvent, create_gate_result
from gateway.storage import Storage
from analyzers import BugDiscoveryAnalyzer, QualityGuard, AnomalyDetector, SemanticEvaluator


def cmd_import_report(args: argparse.Namespace) -> None:
    """导入 loopexpedition 测试报告"""
    path = Path(args.path)
    if not path.exists():
        print(f"❌ 文件不存在: {path}")
        sys.exit(1)

    report = json.loads(path.read_text())
    storage = Storage(args.db)
    session_id = f"import-{path.stem}-{uuid.uuid4().hex[:8]}"
    source = EventSource(framework="custom", project=args.project)
    now = int(time.time() * 1000)

    events = []

    # 导入门禁结果
    if "gate_result" in report:
        events.append(create_gate_result(session_id, source, 
            report["gate_result"].get("verdict", "UNKNOWN"),
            report["gate_result"].get("rules", {})))

    # 导入各阶段结果
    for phase_name, phase_data in report.get("phases", {}).items():
        events.append(TestEvent(
            event_id=str(uuid.uuid4()),
            session_id=session_id,
            timestamp=now,
            source=EventSource(framework="custom", project=args.project, suite=phase_name),
            type="report.summary",
            data={"phase": phase_name, **phase_data},
        ))

    # 导入覆盖率数据
    coverage = report.get("phases", {}).get("coverage", {})
    if coverage:
        events.append(TestEvent(
            event_id=str(uuid.uuid4()),
            session_id=session_id,
            timestamp=now,
            source=source,
            type="observation.coverage",
            data=coverage,
        ))

    storage.store_events_batch(events)
    print(f"✅ 导入完成: {session_id}")
    print(f"   事件数: {len(events)}")
    print(f"   门禁: {report.get('gate_result', {}).get('verdict', 'N/A')}")
    print(f"   耗时: {report.get('duration_s', 'N/A')}s")


def cmd_import_events(args: argparse.Namespace) -> None:
    """导入事件 JSON 文件"""
    path = Path(args.path)
    if not path.exists():
        print(f"❌ 文件不存在: {path}")
        sys.exit(1)

    data = json.loads(path.read_text())
    events_data = data if isinstance(data, list) else data.get("events", [data])
    storage = Storage(args.db)

    events = []
    for e in events_data:
        events.append(TestEvent(
            event_id=e.get("event_id", str(uuid.uuid4())),
            session_id=e.get("session_id", "imported"),
            timestamp=e.get("timestamp", int(time.time() * 1000)),
            source=EventSource(**e.get("source", {"framework": "custom", "project": "unknown"})),
            type=e["type"],
            data=e.get("data", {}),
            trace_id=e.get("trace_id"),
        ))

    count = storage.store_events_batch(events)
    print(f"✅ 导入完成: {count} 个事件")


def cmd_sessions(args: argparse.Namespace) -> None:
    """列出会话"""
    storage = Storage(args.db)
    sessions = storage.get_recent_sessions(project=args.project, limit=args.limit)

    if not sessions:
        print("暂无会话")
        return

    print(f"{'会话 ID':<40} {'项目':<20} {'框架':<10} {'开始时间'}")
    print("-" * 90)
    for s in sessions:
        start = time.strftime("%Y-%m-%d %H:%M", time.localtime(s["started_at"] / 1000))
        print(f"{s['session_id']:<40} {s['project']:<20} {s['framework']:<10} {start}")


def cmd_timeline(args: argparse.Namespace) -> None:
    """查看时间线"""
    storage = Storage(args.db)
    events = storage.get_session_events(args.session_id, event_type=args.type, limit=args.limit)

    if not events:
        print(f"会话 {args.session_id} 无事件")
        return

    print(f"会话: {args.session_id} ({len(events)} 事件)")
    print()
    for e in events:
        ts = time.strftime("%H:%M:%S", time.localtime(e["timestamp"] / 1000))
        etype = e["type"]
        data = e["data"]
        name = data.get("test_name") or data.get("tool_name") or data.get("assertion_name") or ""
        status = ""
        if "passed" in data:
            status = "✅" if data["passed"] else "❌"
        elif "success" in data:
            status = "✅" if data["success"] else "❌"
        print(f"  [{ts}] {status} {etype:<25} {name}")


def cmd_analyze(args: argparse.Namespace) -> None:
    """运行 AI 分析"""
    storage = Storage(args.db)
    events = storage.get_session_events(args.session_id, limit=10000)

    if not events:
        print(f"会话 {args.session_id} 无事件")
        return

    print(f"分析会话: {args.session_id} ({len(events)} 事件)")
    print()

    analyzers = [
        BugDiscoveryAnalyzer(),
        QualityGuard(),
        AnomalyDetector(),
        SemanticEvaluator(),
    ]

    for analyzer in analyzers:
        result = analyzer.analyze(events)
        print(f"📊 {result.analyzer}")
        print(f"   摘要: {result.summary}")
        if result.findings:
            for f in result.findings:
                severity = f.get("severity", "info")
                icon = {"high": "🔴", "medium": "🟡", "low": "🟢"}.get(severity, "⚪")
                print(f"   {icon} [{f.get('category')}] {f.get('description', '')[:60]}")
        if result.recommendations:
            for r in result.recommendations:
                print(f"   💡 {r}")
        print()


def cmd_stats(args: argparse.Namespace) -> None:
    """项目统计"""
    storage = Storage(args.db)
    stats = storage.get_project_stats(args.project, days=args.days)

    print(f"项目: {stats['project']}")
    print(f"统计周期: {stats['period_days']} 天")
    print(f"通过率: {stats['pass_rate']:.1%}")
    print()
    print("事件分布:")
    for etype, count in sorted(stats["events"].items(), key=lambda x: -x[1]):
        print(f"  {etype:<30} {count}")


def main() -> None:
    parser = argparse.ArgumentParser(description="测试观测平台 CLI")
    parser.add_argument("--db", default="test_observability.db", help="数据库路径")
    sub = parser.add_subparsers(dest="command")

    # import-report
    p = sub.add_parser("import-report", help="导入 loopexpedition 测试报告")
    p.add_argument("path", help="报告文件路径")
    p.add_argument("--project", default="loopexpedition", help="项目名称")

    # import-events
    p = sub.add_parser("import-events", help="导入事件 JSON 文件")
    p.add_argument("path", help="事件文件路径")

    # sessions
    p = sub.add_parser("sessions", help="列出会话")
    p.add_argument("--project", help="按项目过滤")
    p.add_argument("--limit", type=int, default=20, help="最大数量")

    # timeline
    p = sub.add_parser("timeline", help="查看时间线")
    p.add_argument("session_id", help="会话 ID")
    p.add_argument("--type", help="按事件类型过滤")
    p.add_argument("--limit", type=int, default=100, help="最大数量")

    # analyze
    p = sub.add_parser("analyze", help="运行 AI 分析")
    p.add_argument("session_id", help="会话 ID")

    # stats
    p = sub.add_parser("stats", help="项目统计")
    p.add_argument("project", help="项目名称")
    p.add_argument("--days", type=int, default=7, help="统计天数")

    args = parser.parse_args()

    if args.command == "import-report":
        cmd_import_report(args)
    elif args.command == "import-events":
        cmd_import_events(args)
    elif args.command == "sessions":
        cmd_sessions(args)
    elif args.command == "timeline":
        cmd_timeline(args)
    elif args.command == "analyze":
        cmd_analyze(args)
    elif args.command == "stats":
        cmd_stats(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
