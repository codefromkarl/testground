"""CI E2E smoke test script.

Usage: python scripts/ci_e2e_smoke.py [--gateway-url URL] [--project NAME]
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

# Ensure project root is on sys.path
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main() -> int:
    parser = argparse.ArgumentParser(description="EventBridge E2E smoke test")
    parser.add_argument("--gateway-url", default="http://localhost:8900")
    parser.add_argument("--project", default="e2e_test")
    args = parser.parse_args()

    return asyncio.run(_run(args.gateway_url, args.project))


async def _run(gateway_url: str, project: str) -> int:
    from drivers.godot.event_bridge import EventBridge

    print("=== EventBridge E2E 测试 ===")
    print(f"Gateway: {gateway_url}")
    print(f"Project: {project}")

    class MockDriver:
        async def screenshot(self, fn=None):
            p = Path("test_screenshots") / (fn or "test.png")
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(b"")
            return p

    bridge = EventBridge(MockDriver(), gateway_url=gateway_url, project=project)

    try:
        async with bridge:
            sid = await bridge.start_session(project)
            print(f"✅ Session 创建: {sid}")

            await bridge.report_test_start("smoke")
            await bridge.report_test_end("smoke", True, 50)
            print("✅ 测试事件: start + end")

            await bridge.report_bench_result("build_health", 80.0, True)
            print("✅ Bench 事件")

            await bridge.end_session(gate_result={"passed": True})
            print("✅ Session 结束")

            print(f"\n✅ E2E 通过 (sent={bridge.sent_count}, errors={bridge.error_count})")
            return 0
    except Exception as e:
        print(f"❌ E2E 失败: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
