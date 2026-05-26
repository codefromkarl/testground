#!/usr/bin/env python3
"""Loop Expedition — Godot 游戏测试运行器

集成 Airtest 视觉断言 + OpenGame Debug Protocol + OpenGame-Bench
利用 loopexpedition 完善的 godot_e2e 插件进行深度测试

用法:
    python scripts/test_loop.py --project ~/Develop/playground/loopexpedition/godot

前置:
    1. 启动游戏: GODOT_E2E=1 godot --path godot/
    2. 启动网关: make gateway
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from drivers.godot.bench import GameBench
from drivers.godot.debug_protocol import DebugProtocol, create_seed_protocol
from drivers.godot.driver import DriverConfig, GodotDriver
from drivers.godot.visual import TemplateMatch, VisualAsserter


async def run_expedition_test(driver: GodotDriver, asserter: VisualAsserter, protocol: DebugProtocol) -> dict:
    """运行远征模式 E2E 测试"""
    results = {"tests": [], "visual_assertions": [], "debug_matches": []}

    # Test 1: 主场景加载
    print("\n[Test 1] 验证主场景")
    scene = await driver.get_scene()
    scene_ok = bool(scene)
    results["tests"].append({"name": "main_scene", "passed": scene_ok, "scene": scene})
    print(f"  {'✓' if scene_ok else '✗'} 场景: {scene}")

    # Test 2: 获取场景树
    print("\n[Test 2] 场景树检查")
    tree = await driver.get_tree()
    tree_ok = bool(tree)
    results["tests"].append({"name": "scene_tree", "passed": tree_ok})
    print(f"  {'✓' if tree_ok else '✗'} 场景树获取: {tree_ok}")

    # Test 3: Observability 启动
    print("\n[Test 3] 启动 Observability")
    await driver.start_state_diff()
    await driver.start_input_trace()
    results["tests"].append({"name": "observability", "passed": True})
    print("  ✓ 状态差异 + 输入追踪已启动")

    # Test 4: 查找远征入口按钮 (通过 e2e_role meta)
    print("\n[Test 4] 查找远征入口")
    expedition_btns = await driver.find_by_meta("expedition_start")
    if expedition_btns:
        print(f"  ✓ 找到远征入口: {expedition_btns}")

        # 点击开始远征
        await driver.click_node(expedition_btns[0])
        await driver.wait_process_frames(120)

        # 等待远征场景
        expedition_scene = await driver.wait_for_scene("res://scenes/expedition/", timeout=10.0)
        results["tests"].append(
            {
                "name": "enter_expedition",
                "passed": expedition_scene,
            }
        )
        print(f"  {'✓' if expedition_scene else '✗'} 进入远征: {expedition_scene}")
    else:
        print("  ⚠ 未找到远征入口按钮")
        results["tests"].append({"name": "expedition_btn", "passed": False})

    # Test 5: 截图视觉检查
    print("\n[Test 5] 视觉断言")
    screenshot = await driver.screenshot("loop_expedition_state.png")
    print(f"  截图: {screenshot}")

    template_dir = Path("test_templates/loop")
    if template_dir.exists():
        for tpl_file in template_dir.glob("*.png"):
            result = asserter.exists(
                screenshot,
                TemplateMatch(template_path=str(tpl_file), threshold=0.75),
            )
            results["visual_assertions"].append(
                {
                    "template": tpl_file.name,
                    "matched": result.matched,
                    "confidence": result.confidence,
                }
            )
            status = "✓" if result.matched else "✗"
            print(f"  {status} {tpl_file.name}: confidence={result.confidence:.3f}")

    # Test 6: 停止 Observability 并收集
    print("\n[Test 6] 收集 Observability 数据")
    state_diff = await driver.stop_state_diff()
    input_trace = await driver.stop_input_trace()
    results["tests"].append(
        {
            "name": "observability_collect",
            "passed": bool(state_diff or input_trace),
        }
    )
    print(f"  状态差异: {'有' if state_diff else '无'}")
    print(f"  输入追踪: {'有' if input_trace else '无'}")

    # Test 7: Debug Protocol 匹配
    print("\n[Test 7] 调试协议检查")
    entry = protocol.find_match("EXPEDITION_ERROR", "not initialized", "runtime")
    if entry:
        results["debug_matches"].append(
            {
                "entry_id": entry.id,
                "root_cause": entry.root_cause,
            }
        )
        print(f"  ✓ 匹配已知问题: {entry.root_cause}")

    # Test 8: 因果标注测试
    print("\n[Test 8] 因果标注")
    cause_result = await driver.trigger_cause_test("expedition_lifecycle")
    print(f"  因果标注结果: {cause_result}")

    return results


def run_bench(project_path: str) -> dict:
    """运行 OpenGame-Bench 三维评估"""
    print("\n=== OpenGame-Bench 评估 ===")
    bench = GameBench(project_path, "loopexpedition")
    result = bench.evaluate(run_headless=False)
    print(f"  Build Health: {result.build_health.score:.0f}/100")
    print(f"  Visual Usability: {result.visual_usability.score:.0f}/100")
    print(f"  Intent Alignment: {result.intent_alignment.score:.0f}/100")
    print(f"  总分: {result.total_score:.1f}/100 {'✓ 通过' if result.passed else '✗ 未通过'}")
    return result.to_dict()


async def main():
    parser = argparse.ArgumentParser(description="Loop Expedition 测试")
    parser.add_argument("--project", default="~/Develop/playground/loopexpedition/godot", help="Godot 项目路径")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=0, help="0=自动检测")
    parser.add_argument("--bench-only", action="store_true")
    parser.add_argument("--init-protocol", action="store_true")
    args = parser.parse_args()

    project_path = Path(args.project).expanduser()

    if args.init_protocol:
        protocol = create_seed_protocol("loopexpedition")
        path = protocol.save()
        print(f"种子协议已创建: {path} ({len(protocol.entries)} 条目)")
        return

    if args.bench_only:
        result = run_bench(str(project_path))
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return

    protocol = DebugProtocol.load_or_create("loopexpedition")
    print(f"调试协议: v{protocol.version} | {len(protocol.entries)} 条目")

    # 运行评估
    bench_result = run_bench(str(project_path))

    # 连接驱动
    print(f"\n=== 连接 godot_e2e ({args.host}) ===")
    try:
        asserter = VisualAsserter()
    except ImportError:
        print("⚠ opencv 未安装，跳过视觉断言")
        asserter = None

    config = DriverConfig(host=args.host, port=args.port or 19090, project_type="loopexpedition")
    async with GodotDriver(config=config) as driver:
        results = await run_expedition_test(driver, asserter, protocol)

    protocol.save()
    output = {"bench": bench_result, "tests": results}
    output_path = Path("test_results/loop_test_result.json")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(output, indent=2, ensure_ascii=False))
    print(f"\n结果已保存: {output_path}")


if __name__ == "__main__":
    asyncio.run(main())
