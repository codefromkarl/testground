"""CLI 工具 — 导入测试报告、查询数据、运行分析

用法:
    python -m cli import-report <path>     # 导入 loopexpedition 测试报告
    python -m cli import-events <path>     # 导入事件 JSON 文件
    python -m cli sessions                 # 列出会话
    python -m cli timeline <session_id>    # 查看时间线
    python -m cli analyze <session_id>     # 运行 AI 分析（传统模式）
    python -m cli pipeline <session_id>    # 运行分析流水线（多窄 Agent）
    python -m cli stats <project>          # 项目统计
    python -m cli run <project>            # 运行 Driver + Bridge 完整工作流
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

from analyzers import AnomalyDetector, BugDiscoveryAnalyzer, QualityGuard, SemanticEvaluator
from analyzers.alerts import AlertManager, AlertRule
from analyzers.notifiers import WebhookNotifier
from analyzers.pipeline import AnalysisPipeline, PipelineConfig, PipelineState
from gateway.storage import Storage
from schema.events import EventSource, ObsEvent, create_gate_result


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
        events.append(
            create_gate_result(
                session_id,
                source,
                report["gate_result"].get("verdict", "UNKNOWN"),
                report["gate_result"].get("rules", {}),
            )
        )

    # 导入各阶段结果
    for phase_name, phase_data in report.get("phases", {}).items():
        events.append(
            ObsEvent(
                event_id=str(uuid.uuid4()),
                session_id=session_id,
                timestamp=now,
                source=EventSource(framework="custom", project=args.project, suite=phase_name),
                type="report.summary",
                data={"phase": phase_name, **phase_data},
            )
        )

    # 导入覆盖率数据
    coverage = report.get("phases", {}).get("coverage", {})
    if coverage:
        events.append(
            ObsEvent(
                event_id=str(uuid.uuid4()),
                session_id=session_id,
                timestamp=now,
                source=source,
                type="observation.coverage",
                data=coverage,
            )
        )

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
        events.append(
            ObsEvent(
                event_id=e.get("event_id", str(uuid.uuid4())),
                session_id=e.get("session_id", "imported"),
                timestamp=e.get("timestamp", int(time.time() * 1000)),
                source=EventSource(**e.get("source", {"framework": "custom", "project": "unknown"})),
                type=e["type"],
                data=e.get("data", {}),
                trace_id=e.get("trace_id"),
            )
        )

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
    """运行 AI 分析（传统模式 — 4 个独立分析器）"""
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


def cmd_pipeline(args: argparse.Namespace) -> None:
    """运行分析流水线（多窄 Agent 架构）"""
    storage = Storage(args.db)
    events = storage.get_session_events(args.session_id, limit=10000)

    if not events:
        print(f"会话 {args.session_id} 无事件")
        return

    db_path = Path(args.db).parent / "pipeline_state.db"
    state = PipelineState(db_path)
    config = PipelineConfig(
        use_llm=not args.no_llm,
        max_tokens=args.max_tokens,
        enable_feedback=not args.no_feedback,
    )
    pipeline = AnalysisPipeline(state=state, config=config)

    print(f"🔄 运行分析流水线: {args.session_id}")
    print(f"   模式: {'LLM' if config.use_llm else '规则引擎'}")
    print(f"   事件数: {len(events)}")
    print()

    result = pipeline.run(events, session_id=args.session_id)

    # 输出报告
    print(f"{'=' * 60}")
    print("📊 分析结果")
    print(f"{'=' * 60}")
    print(f"状态: {result.status}")
    print(f"耗时: {result.duration_ms}ms")
    print(f"质量分: {result.quality_score:.0f}/100")
    print()

    if result.confirmed_findings:
        print(f"已确认问题 ({len(result.confirmed_findings)} 个):")
        for f in result.confirmed_findings:
            severity = f.get("severity", "info")
            icon = {"critical": "🔴", "high": "🟠", "medium": "🟡", "low": "🟢"}.get(severity, "⚪")
            print(f"  {icon} [{f.get('category')}] {f.get('description', '')[:70]}")
    else:
        print("✅ 未发现已确认问题")

    if result.rejected_count > 0:
        print(f"\n已拒绝: {result.rejected_count} 个（对抗验证推翻）")

    if result.recommendations:
        print("\n建议:")
        for r in result.recommendations:
            print(f"  💡 {r}")

    if result.cost_summary:
        print(f"\nToken 消耗: {result.cost_summary}")

    state.close()


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


def cmd_alert_test(args: argparse.Namespace) -> None:
    """测试告警配置"""
    notifiers = []
    from analyzers.notifiers import ConsoleNotifier, FileNotifier

    if args.no_console:
        # 不添加 console，但仍可能有其他渠道
        pass
    else:
        notifiers.append(ConsoleNotifier())

    if args.webhook:
        webhook_type = args.webhook_type if args.webhook_type != "auto" else "auto"
        notifiers.append(WebhookNotifier(args.webhook, webhook_type=webhook_type))
        print(f"✅ Webhook 渠道: {args.webhook} ({webhook_type})")

    if not notifiers:
        print("❌ 未配置任何通知渠道")
        sys.exit(1)

    manager = AlertManager(notifiers=notifiers)
    severity = args.severity

    print(f"🔔 发送测试告警 (severity={severity})...")
    msg = manager.test_notification(severity=severity)
    print(f"✅ 测试告警已发送: {msg.finding_id}")

    if args.webhook:
        print("   请检查 webhook 端是否收到消息")


def cmd_alert_history(args: argparse.Namespace) -> None:
    """查看告警历史"""
    log_path = args.log or "alerts.jsonl"
    from analyzers.notifiers import FileNotifier

    notifier = FileNotifier(log_path)
    history = notifier.read_history(limit=args.limit)

    if not history:
        print(f"暂无告警历史 (日志文件: {log_path})")
        return

    print(f"告警历史 ({len(history)} 条, 最近 {args.limit} 条):")
    print()
    for entry in history:
        severity = entry.get("severity", "unknown")
        icon = {"critical": "🔴", "high": "🟠", "medium": "🟡", "low": "🟢"}.get(severity, "⚪")
        ts = entry.get("timestamp", 0)
        time_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts)) if ts else "unknown"
        print(f"  {icon} [{severity.upper()}] {entry.get('category', 'unknown')}")
        print(f"     时间: {time_str}")
        print(f"     描述: {entry.get('description', '')[:80]}")
        print(f"     Finding: {entry.get('finding_id', 'N/A')}")
        print(f"     Session: {entry.get('session_id', 'N/A')}")
        print()


def cmd_report(args: argparse.Namespace) -> None:
    """生成测试报告"""
    from analyzers.report import ReportGenerator

    storage = Storage(args.db)
    generator = ReportGenerator(storage)

    fmt = args.format
    output_dir = Path(args.output) if args.output else None

    try:
        path = generator.generate(args.session_id, format=fmt, output_dir=output_dir)
        print(f"✅ 报告已生成: {path}")
        print(f"   格式: {fmt}")
        print(f"   大小: {path.stat().st_size} bytes")
    except Exception as e:
        print(f"❌ 生成失败: {e}")
        sys.exit(1)


def cmd_record(args: argparse.Namespace) -> None:
    """录制游戏操作序列"""
    import asyncio

    async def run_record():
        from drivers.godot.driver import DriverConfig, GodotDriver
        from drivers.godot.recorder import GameRecorder

        # 连接 Godot
        config = DriverConfig(host=args.host, port=args.port)
        driver = GodotDriver(config=config)

        try:
            await driver.connect()
        except ConnectionError as e:
            print(f"❌ 无法连接 Godot: {e}")
            sys.exit(1)

        # 创建录制器
        recorder = GameRecorder(
            driver=driver,
            output_dir=args.output_dir,
            auto_screenshot=args.auto_screenshot,
        )

        # 开始录制
        metadata = {}
        if args.project:
            metadata["project"] = args.project
        if args.scene:
            metadata["start_scene"] = args.scene

        session_id = await recorder.start_recording(
            session_id=args.session_id,
            metadata=metadata,
        )
        print(f"✅ 录制已开始: {session_id}")
        print(f"   输出目录: {args.output_dir}")
        print(f"   自动截图: {'是' if args.auto_screenshot else '否'}")
        print()
        print("🎮 在 Godot 中进行游戏操作...")
        print("   按 Ctrl+C 停止录制")

        # 等待用户中断
        try:
            while True:
                await asyncio.sleep(0.1)
        except KeyboardInterrupt:
            pass

        # 停止录制
        result = await recorder.stop_recording()
        print(f"\n✅ 录制完成:")
        print(f"   会话 ID: {result.session_id}")
        print(f"   操作数: {result.action_count}")
        print(f"   耗时: {result.duration_s:.1f}s")
        print(f"   文件: {result.json_path}")

        await driver.close()

    try:
        asyncio.run(run_record())
    except KeyboardInterrupt:
        print("\n⏹ 录制中断")


def cmd_replay(args: argparse.Namespace) -> None:
    """回放录制的操作序列"""
    import asyncio

    async def run_replay():
        from drivers.godot.driver import DriverConfig, GodotDriver
        from drivers.godot.replayer import GameReplayer

        # 加载录制
        replayer = GameReplayer(
            recording_dir=str(Path(args.recording).parent),
            verify_screenshots=args.verify_screenshots and not args.no_verify,
            verify_threshold=args.threshold,
        )

        try:
            replayer.load_recording(args.recording)
        except (FileNotFoundError, ValueError) as e:
            print(f"❌ 加载录制失败: {e}")
            sys.exit(1)

        # 连接 Godot
        config = DriverConfig(host=args.host, port=args.port)
        if args.output_dir:
            config.screenshot_dir = args.output_dir
        driver = GodotDriver(config=config)

        try:
            await driver.connect()
        except ConnectionError as e:
            print(f"❌ 无法连接 Godot: {e}")
            sys.exit(1)

        # 回放
        result = await replayer.replay(
            driver=driver,
            speed=args.speed,
            verify=not args.no_verify,
        )

        # 输出结果
        print(f"\n{'='*60}")
        print("📊 回放结果")
        print(f"{'='*60}")
        print(f"会话 ID: {result.session_id}")
        print(f"总步骤: {result.total_steps}")
        print(f"通过: {result.passed_steps}")
        print(f"失败: {result.failed_steps}")
        print(f"通过率: {result.pass_rate:.1%}")
        print(f"耗时: {result.duration_s:.1f}s")

        if result.passed:
            print("\n✅ 回放通过")
        else:
            print("\n❌ 回放失败")
            # 显示失败步骤
            for step in result.steps:
                if not step.passed:
                    print(f"   步骤 #{step.index}: {step.action_type} — {step.error}")

        await driver.close()

    try:
        asyncio.run(run_replay())
    except KeyboardInterrupt:
        print("\n⏹ 回放中断")


def cmd_run(args: argparse.Namespace) -> None:
    """运行 Driver + Bridge 完整工作流演示

    演示:
    1. 启动 EventBridge 连接 Gateway
    2. 创建 session
    3. 模拟测试操作并报告事件
    4. 结束 session 并查看结果
    """
    import asyncio

    from drivers.godot.event_bridge import EventBridge

    async def run_demo():
        # 创建 Driver（无需实际连接 Godot）
        from drivers.godot.driver import DriverConfig, GodotDriver

        config = DriverConfig(
            host=args.host,
            port=args.port,
            project_type=args.project_type,
        )
        driver = GodotDriver(config=config)

        # 创建 Bridge
        bridge = EventBridge(
            driver=driver,
            gateway_url=args.gateway,
            project=args.project,
            framework=args.framework,
        )

        async with bridge:
            # 创建 session
            sid = await bridge.start_session(
                args.project,
                metadata={"cli_run": True, "driver": "godot"},
            )
            print(f"✅ Session 创建: {sid}")

            # 模拟测试流程
            tests = [
                ("test_scene_load", True, 250),
                ("test_click_button", True, 80),
                ("test_visual_check", True, 150),
                ("test_battle_flow", False, 500),
            ]

            for name, passed, duration in tests:
                await bridge.report_test_start(name)
                await bridge.report_test_end(name, passed, duration)
                status = "✅" if passed else "❌"
                print(f"  {status} {name} ({duration}ms)")

            # 模拟评估
            await bridge.report_bench_result("build_health", 92.0, True)
            await bridge.report_bench_result("visual_usability", 75.0, True)
            print("📊 评估: build_health=92, visual_usability=75")

            # 模拟游戏事件
            await bridge.report_game_event(
                "game.state_change",
                {"scene": "res://battle.tscn", "state": {"turn": 5}},
            )
            print("🎮 游戏状态事件已报告")

            # 结束 session
            gate = {
                "verdict": "PASS" if all(t[1] for t in tests) else "FAIL",
                "rules": {"pass_rate": {"value": 0.75, "threshold": 1.0}},
            }
            await bridge.end_session(gate_result=gate)
            print(f"\n🏁 Session 结束: {sid}")
            print(f"   事件发送: {bridge.sent_count}")
            print(f"   发送错误: {bridge.error_count}")
            print(f"   测试总数: {bridge._test_count}")
            print(f"   通过/失败: {bridge._passed_count}/{bridge._failed_count}")

            # 演示调试报告
            print("\n🔧 演示调试事件:")
            await bridge.report_debug_match(
                entry_id="entry-GDSCRIPT-001",
                error_code="GDSCRIPT_ERROR",
                error_message="Parse Error: Unexpected token",
            )
            await bridge.report_debug_repair(
                entry_id="entry-GDSCRIPT-001",
                fix_description="修复缩进问题",
                error_code="GDSCRIPT_ERROR",
            )
            print("   debug.match + debug.repair 已报告")

    try:
        asyncio.run(run_demo())
    except KeyboardInterrupt:
        print("\n⏹ 用户中断")
    except Exception as e:
        print(f"\n❌ 错误: {e}")
        if args.verbose:
            import traceback
            traceback.print_exc()


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
    p = sub.add_parser("analyze", help="运行 AI 分析（传统模式）")
    p.add_argument("session_id", help="会话 ID")

    # pipeline
    p = sub.add_parser("pipeline", help="运行分析流水线（多窄 Agent）")
    p.add_argument("session_id", help="会话 ID")
    p.add_argument("--no-llm", action="store_true", help="强制使用规则引擎")
    p.add_argument("--no-feedback", action="store_true", help="禁用反馈循环")
    p.add_argument("--max-tokens", type=int, default=100000, help="Token 预算上限")

    # stats
    p = sub.add_parser("stats", help="项目统计")
    p.add_argument("project", help="项目名称")
    p.add_argument("--days", type=int, default=7, help="统计天数")

    # report
    p = sub.add_parser("report", help="生成测试报告")
    p.add_argument("session_id", help="会话 ID")
    p.add_argument("--format", choices=["html", "json", "md"], default="html", help="报告格式")
    p.add_argument("--output", "-o", help="输出目录（默认 ./reports）")

    # run — Driver + Bridge 工作流演示
    p = sub.add_parser("run", help="运行 Driver + Bridge 完整工作流")
    p.add_argument("project", help="项目名称")
    p.add_argument("--host", default="127.0.0.1", help="Godot 主机")
    p.add_argument("--port", type=int, default=19090, help="Godot 端口")
    p.add_argument("--project-type", default="auto", choices=["auto", "loopexpedition", "pogongshichongzou"], help="项目类型")
    p.add_argument("--gateway", default="http://localhost:8900", help="Gateway URL")
    p.add_argument("--framework", default="godot_driver", help="框架标识")
    p.add_argument("--verbose", action="store_true", help="详细输出")

    # alert-test — 测试告警配置
    p = sub.add_parser("alert-test", help="测试告警配置")
    p.add_argument("--severity", default="high", choices=["low", "medium", "high", "critical"], help="测试告警级别")
    p.add_argument("--webhook", help="Webhook URL")
    p.add_argument("--webhook-type", default="auto", choices=["auto", "slack", "feishu"], help="Webhook 类型")
    p.add_argument("--no-console", action="store_true", help="禁用控制台输出")

    # alert-history — 查看告警历史
    p = sub.add_parser("alert-history", help="查看告警历史")
    p.add_argument("--log", help="告警日志文件路径")
    p.add_argument("--limit", type=int, default=50, help="最大条数")

    # record — 录制游戏操作
    p = sub.add_parser("record", help="录制游戏操作序列")
    p.add_argument("--session-id", help="会话 ID（默认自动生成）")
    p.add_argument("--output-dir", default="recordings", help="录制输出目录")
    p.add_argument("--host", default="127.0.0.1", help="Godot 主机")
    p.add_argument("--port", type=int, default=19090, help="Godot 端口")
    p.add_argument("--auto-screenshot", action="store_true", help="每个操作自动截图")
    p.add_argument("--project", default="", help="项目名称（元数据）")
    p.add_argument("--scene", default="", help="初始场景（元数据）")

    # replay — 回放录制的操作
    p = sub.add_parser("replay", help="回放录制的操作序列")
    p.add_argument("recording", help="录制 JSON 文件路径")
    p.add_argument("--speed", type=float, default=1.0, help="回放速度（1.0=原速，2.0=2倍速）")
    p.add_argument("--no-verify", action="store_true", help="跳过截图验证")
    p.add_argument("--verify-screenshots", action="store_true", help="启用截图对比验证")
    p.add_argument("--threshold", type=float, default=0.8, help="截图匹配阈值 (0-1)")
    p.add_argument("--host", default="127.0.0.1", help="Godot 主机")
    p.add_argument("--port", type=int, default=19090, help="Godot 端口")
    p.add_argument("--output-dir", help="回放截图保存目录")

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
    elif args.command == "pipeline":
        cmd_pipeline(args)
    elif args.command == "stats":
        cmd_stats(args)
    elif args.command == "alert-test":
        cmd_alert_test(args)
    elif args.command == "alert-history":
        cmd_alert_history(args)
    elif args.command == "run":
        cmd_run(args)
    elif args.command == "report":
        cmd_report(args)
    elif args.command == "record":
        cmd_record(args)
    elif args.command == "replay":
        cmd_replay(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
