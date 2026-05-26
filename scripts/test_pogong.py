#!/usr/bin/env python3
"""破宫之十重奏 — Godot 游戏测试运行器

集成 Airtest 视觉断言 + OpenGame Debug Protocol + OpenGame-Bench

用法:
    python scripts/test_pogong.py --project ~/Develop/playground/pogongshichongzou/godot

前置:
    1. 启动游戏: PGC_AUTOMATION=1 godot --path godot/
    2. 启动网关: make gateway
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path

# 添加 testground 到 path
sys.path.insert(0, str(Path(__file__).parent.parent))

from drivers.godot.bench import GameBench
from drivers.godot.debug_protocol import DebugProtocol, create_seed_protocol
from drivers.godot.driver import DriverConfig, GodotDriver
from drivers.godot.visual import TemplateMatch, VisualAsserter


async def run_playthrough_test(driver: GodotDriver, asserter: VisualAsserter, protocol: DebugProtocol) -> dict:
    """运行破宫完整 playthrough 测试"""
    results = {"tests": [], "visual_assertions": [], "debug_matches": []}

    # Test 1: 主菜单场景
    print("\n[Test 1] 验证主菜单场景")
    scene_ok = await driver.wait_for_scene("res://scenes/shell/main.tscn", timeout=5.0)
    results["tests"].append({"name": "main_menu", "passed": scene_ok})
    print(f"  {'✓' if scene_ok else '✗'} 主菜单: {scene_ok}")

    # Test 2: 截图并视觉检查
    if scene_ok:
        print("\n[Test 2] 视觉断言")
        screenshot = await driver.screenshot("pogong_main_menu.png")
        print(f"  截图: {screenshot}")

        # 检查是否有模板可匹配
        template_dir = Path("test_templates/pogong")
        if template_dir.exists():
            for tpl_file in template_dir.glob("*.png"):
                result = asserter.exists(
                    screenshot,
                    TemplateMatch(template_path=str(tpl_file), threshold=0.7),
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
        else:
            print(f"  ⚠ 模板目录不存在: {template_dir} (可后续创建)")
            print("  提示: 使用 VisualAsserter.save_template_from_screenshot 裁切模板")

    # Test 3: 新建存档并开始 Run
    print("\n[Test 3] 开始新 Run")
    start_btn = await driver.find_by_meta("start_new_run")
    if start_btn:
        await driver.click_node(start_btn[0])
        await driver.wait_process_frames(60)

        # 等待角色选择或战斗场景
        battle_scene = await driver.wait_for_scene("res://scenes/battle/BattleScene.tscn", timeout=10.0)
        results["tests"].append({"name": "enter_battle", "passed": battle_scene})
        print(f"  {'✓' if battle_scene else '✗'} 进入战斗: {battle_scene}")
    else:
        print("  ⚠ 未找到开始按钮 (需要 e2e_role meta 标记)")

    # Test 4: Debug Protocol 匹配
    print("\n[Test 4] 调试协议检查")
    entry = protocol.find_match("GDSCRIPT_ERROR", "Node not found", "runtime")
    if entry:
        results["debug_matches"].append(
            {
                "entry_id": entry.id,
                "root_cause": entry.root_cause,
                "fix": entry.fix_description,
            }
        )
        print(f"  ✓ 匹配已知问题: {entry.root_cause}")

    return results


def run_bench(project_path: str) -> dict:
    """运行 OpenGame-Bench 三维评估"""
    print("\n=== OpenGame-Bench 评估 ===")
    bench = GameBench(project_path, "pogongshichongzou")
    result = bench.evaluate(run_headless=False)
    print(f"  Build Health: {result.build_health.score:.0f}/100")
    print(f"  Visual Usability: {result.visual_usability.score:.0f}/100")
    print(f"  Intent Alignment: {result.intent_alignment.score:.0f}/100")
    print(f"  总分: {result.total_score:.1f}/100 {'✓ 通过' if result.passed else '✗ 未通过'}")
    return result.to_dict()


async def main():
    parser = argparse.ArgumentParser(description="破宫之十重奏测试")
    parser.add_argument("--project", default="~/Develop/playground/pogongshichongzou/godot", help="Godot 项目路径")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=19090)
    parser.add_argument("--bench-only", action="store_true", help="仅运行评估")
    parser.add_argument("--init-protocol", action="store_true", help="初始化种子协议")
    args = parser.parse_args()

    project_path = Path(args.project).expanduser()

    # 初始化协议
    if args.init_protocol:
        protocol = create_seed_protocol("pogongshichongzou")
        path = protocol.save()
        print(f"种子协议已创建: {path} ({len(protocol.entries)} 条目)")
        return

    # 仅评估
    if args.bench_only:
        result = run_bench(str(project_path))
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return

    # 加载调试协议
    protocol = DebugProtocol.load_or_create("pogongshichongzou")
    print(f"调试协议: v{protocol.version} | {len(protocol.entries)} 条目 | {len(protocol.rules)} 规则")

    # 运行评估
    bench_result = run_bench(str(project_path))

    # 连接驱动并运行测试
    print(f"\n=== 连接 Godot ({args.host}:{args.port}) ===")
    try:
        asserter = VisualAsserter()
    except ImportError:
        print("⚠ opencv 未安装，跳过视觉断言")
        asserter = None

    config = DriverConfig(host=args.host, port=args.port, project_type="pogongshichongzou")
    async with GodotDriver(config=config) as driver:
        results = await run_playthrough_test(driver, asserter, protocol)

    # 保存结果
    protocol.save()
    output = {"bench": bench_result, "tests": results}
    output_path = Path("test_results/pogong_test_result.json")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(output, indent=2, ensure_ascii=False))
    print(f"\n结果已保存: {output_path}")


if __name__ == "__main__":
    asyncio.run(main())
