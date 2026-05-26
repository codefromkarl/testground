"""注入演示数据到观测网关 — 用于 Timeline 展示"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import httpx

GATEWAY = "http://localhost:8900"


def main():
    client = httpx.Client(base_url=GATEWAY, timeout=5.0)

    # 检查网关是否可用
    try:
        client.get("/health")
    except httpx.RequestError:
        print("❌ 网关未启动，请先运行 make gateway")
        sys.exit(1)

    print("🚀 注入演示数据...")

    # 创建会话
    sessions = [
        {"session_id": "demo-travel", "project": "travel-agent", "framework": "vitest"},
        {"session_id": "demo-pogong", "project": "pogongshichongzou", "framework": "gdunit4"},
        {"session_id": "demo-loop", "project": "loopexpedition", "framework": "custom"},
    ]

    for s in sessions:
        client.post("/sessions", json=s)
        print(f"  ✅ 会话: {s['session_id']}")

    now = int(time.time() * 1000)

    # TravelAgent 事件
    travel_events = [
        {
            "session_id": "demo-travel",
            "source": {"framework": "vitest", "project": "travel-agent", "file": "weather.test.ts"},
            "type": "test.start",
            "data": {"test_name": "searchWeather", "full_name": "searchWeatherTool > 应返回天气数据"},
        },
        {
            "session_id": "demo-travel",
            "source": {"framework": "vitest", "project": "travel-agent"},
            "type": "agent.tool_call",
            "data": {"tool_name": "search_weather", "input": {"city": "杭州"}},
        },
        {
            "session_id": "demo-travel",
            "source": {"framework": "vitest", "project": "travel-agent"},
            "type": "agent.tool_result",
            "data": {
                "tool_name": "search_weather",
                "output": {"temp": 28, "weather": "晴"},
                "success": True,
                "duration_ms": 234,
            },
        },
        {
            "session_id": "demo-travel",
            "source": {"framework": "vitest", "project": "travel-agent"},
            "type": "assert.pass",
            "data": {"assertion_name": "应有正确的工具名称", "passed": True},
        },
        {
            "session_id": "demo-travel",
            "source": {"framework": "vitest", "project": "travel-agent"},
            "type": "test.end",
            "data": {"test_name": "searchWeather", "passed": True, "duration_ms": 312},
        },
        {
            "session_id": "demo-travel",
            "source": {"framework": "vitest", "project": "travel-agent", "file": "hotels.test.ts"},
            "type": "test.start",
            "data": {"test_name": "searchHotels", "full_name": "searchHotelsTool > 应返回酒店列表"},
        },
        {
            "session_id": "demo-travel",
            "source": {"framework": "vitest", "project": "travel-agent"},
            "type": "agent.tool_call",
            "data": {"tool_name": "search_hotels", "input": {"city": "杭州", "checkin": "2026-05-20"}},
        },
        {
            "session_id": "demo-travel",
            "source": {"framework": "vitest", "project": "travel-agent"},
            "type": "agent.tool_result",
            "data": {"tool_name": "search_hotels", "output": [], "success": True, "duration_ms": 1500},
        },
        {
            "session_id": "demo-travel",
            "source": {"framework": "vitest", "project": "travel-agent"},
            "type": "assert.fail",
            "data": {"assertion_name": "应返回至少1个酒店", "passed": False, "expected": ">0", "actual": "0"},
        },
        {
            "session_id": "demo-travel",
            "source": {"framework": "vitest", "project": "travel-agent"},
            "type": "test.fail",
            "data": {
                "test_name": "searchHotels",
                "passed": False,
                "duration_ms": 1800,
                "errors": [{"message": "expected length > 0, got 0"}],
            },
        },
        {
            "session_id": "demo-travel",
            "source": {"framework": "vitest", "project": "travel-agent", "file": "transport.test.ts"},
            "type": "test.start",
            "data": {"test_name": "searchTransport", "full_name": "searchTransportTool > 应返回交通方案"},
        },
        {
            "session_id": "demo-travel",
            "source": {"framework": "vitest", "project": "travel-agent"},
            "type": "test.end",
            "data": {"test_name": "searchTransport", "passed": True, "duration_ms": 450},
        },
    ]

    # pogongshichongzou 事件
    pogong_events = [
        {
            "session_id": "demo-pogong",
            "source": {"framework": "gdunit4", "project": "pogongshichongzou", "file": "run_flow_progression_smoke.gd"},
            "type": "test.start",
            "data": {"test_name": "test_run_flow", "full_name": "run_flow_progression_smoke"},
        },
        {
            "session_id": "demo-pogong",
            "source": {"framework": "gdunit4", "project": "pogongshichongzou"},
            "type": "game.state_change",
            "data": {"scene_path": "/root/MainMenu", "state": {"screen": "title"}},
        },
        {
            "session_id": "demo-pogong",
            "source": {"framework": "gdunit4", "project": "pogongshichongzou"},
            "type": "game.state_change",
            "data": {"scene_path": "/root/Expedition", "state": {"layer": "corridor_1_01", "hp": 100}},
        },
        {
            "session_id": "demo-pogong",
            "source": {"framework": "gdunit4", "project": "pogongshichongzou"},
            "type": "assert.pass",
            "data": {"assertion_name": "should_visit_corridor_1_01", "passed": True},
        },
        {
            "session_id": "demo-pogong",
            "source": {"framework": "gdunit4", "project": "pogongshichongzou"},
            "type": "game.save",
            "data": {"slot": "slot_1"},
        },
        {
            "session_id": "demo-pogong",
            "source": {"framework": "gdunit4", "project": "pogongshichongzou"},
            "type": "game.load",
            "data": {"slot": "slot_1"},
        },
        {
            "session_id": "demo-pogong",
            "source": {"framework": "gdunit4", "project": "pogongshichongzou"},
            "type": "assert.pass",
            "data": {"assertion_name": "save_load_roundtrip", "passed": True},
        },
        {
            "session_id": "demo-pogong",
            "source": {"framework": "gdunit4", "project": "pogongshichongzou"},
            "type": "test.end",
            "data": {"test_name": "test_run_flow", "passed": True, "duration_ms": 5200},
        },
        {
            "session_id": "demo-pogong",
            "source": {
                "framework": "gdunit4",
                "project": "pogongshichongzou",
                "file": "all_card_settlement_contract.gd",
            },
            "type": "test.start",
            "data": {"test_name": "test_card_settlement", "full_name": "all_card_settlement_contract"},
        },
        {
            "session_id": "demo-pogong",
            "source": {"framework": "gdunit4", "project": "pogongshichongzou"},
            "type": "assert.pass",
            "data": {"assertion_name": "fire_card_deals_damage", "passed": True},
        },
        {
            "session_id": "demo-pogong",
            "source": {"framework": "gdunit4", "project": "pogongshichongzou"},
            "type": "test.end",
            "data": {"test_name": "test_card_settlement", "passed": True, "duration_ms": 890},
        },
    ]

    # loopexpedition 事件
    loop_events = [
        {
            "session_id": "demo-loop",
            "source": {"framework": "custom", "project": "loopexpedition"},
            "type": "test.start",
            "data": {"test_name": "bot_expedition_run", "full_name": "TestBot 全流程远征"},
        },
        {
            "session_id": "demo-loop",
            "source": {"framework": "custom", "project": "loopexpedition"},
            "type": "game.state_change",
            "data": {"scene_path": "/root/Camp", "state": {"gold": 100, "cards": 5}},
        },
        {
            "session_id": "demo-loop",
            "source": {"framework": "custom", "project": "loopexpedition"},
            "type": "game.state_change",
            "data": {"scene_path": "/root/Expedition", "state": {"node": "corridor_1_01_node_1", "hp": 80}},
        },
        {
            "session_id": "demo-loop",
            "source": {"framework": "custom", "project": "loopexpedition"},
            "type": "game.state_change",
            "data": {"scene_path": "/root/Battle", "state": {"enemy": "slime", "enemy_hp": 30}},
        },
        {
            "session_id": "demo-loop",
            "source": {"framework": "custom", "project": "loopexpedition"},
            "type": "assert.pass",
            "data": {"assertion_name": "battle_should_end", "passed": True},
        },
        {
            "session_id": "demo-loop",
            "source": {"framework": "custom", "project": "loopexpedition"},
            "type": "game.state_change",
            "data": {"scene_path": "/root/Loot", "state": {"gold_gained": 25, "card_gained": "fire_bolt"}},
        },
        {
            "session_id": "demo-loop",
            "source": {"framework": "custom", "project": "loopexpedition"},
            "type": "observation.anomaly",
            "data": {
                "severity": "medium",
                "category": "stuck_state",
                "description": "玩家在同一节点停留超过5步",
                "evidence": {"node": "corridor_1_01_node_2", "steps": 7},
            },
        },
        {
            "session_id": "demo-loop",
            "source": {"framework": "custom", "project": "loopexpedition"},
            "type": "game.state_change",
            "data": {"scene_path": "/root/Boss", "state": {"boss": "dragon", "boss_hp": 200, "player_hp": 60}},
        },
        {
            "session_id": "demo-loop",
            "source": {"framework": "custom", "project": "loopexpedition"},
            "type": "test.end",
            "data": {"test_name": "bot_expedition_run", "passed": True, "duration_ms": 45000},
        },
        {
            "session_id": "demo-loop",
            "source": {"framework": "custom", "project": "loopexpedition"},
            "type": "report.gate_result",
            "data": {
                "verdict": "PASS",
                "rules": {
                    "test_pass_rate": {"value": 1.0, "threshold": 1.0, "comparator": ">=", "pass": True},
                    "flow_coverage": {"value": 0.95, "threshold": 0.9, "comparator": ">=", "pass": True},
                },
            },
        },
        {
            "session_id": "demo-loop",
            "source": {"framework": "custom", "project": "loopexpedition"},
            "type": "observation.coverage",
            "data": {"event_coverage": 0.91, "obs_coverage": 0.85, "total_flows": 45, "covered_flows": 41},
        },
    ]

    # 注入事件
    all_events = travel_events + pogong_events + loop_events
    for i, event in enumerate(all_events):
        event["event_id"] = f"demo-evt-{i:03d}"
        event["timestamp"] = now + i * 500  # 每 500ms 一个事件
        client.post("/events", json=event)

    print(f"  ✅ 注入了 {len(all_events)} 个事件")

    # 结束会话
    for s in sessions:
        client.put(
            f"/sessions/{s['session_id']}",
            json={
                "ended_at": now + len(all_events) * 500,
                "total_tests": 3,
                "passed_tests": 2,
                "failed_tests": 1,
                "duration_ms": len(all_events) * 500,
            },
        )

    print("✅ 演示数据注入完成！")
    print("   Timeline: http://localhost:8901")
    print("   API: http://localhost:8900/docs")


if __name__ == "__main__":
    main()
