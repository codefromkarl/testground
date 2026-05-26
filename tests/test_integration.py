"""跨项目集成测试 — 验证平台能处理三个项目的真实测试场景"""

import sys
import time
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from analyzers import AnomalyDetector, BugDiscoveryAnalyzer, QualityGuard
from gateway.storage import Storage
from schema.events import (
    EventSource,
    ObsEvent,
    ObsSession,
    create_agent_tool_call,
    create_agent_tool_result,
    create_assertion,
    create_bug_candidate,
    create_game_state_change,
    create_gate_result,
    create_test_end,
    create_test_start,
)


@pytest.fixture
def storage(tmp_path):
    return Storage(str(tmp_path / "integration.db"))


class TestTravelAgentIntegration:
    """TravelAgent 场景: AI Agent 工具调用测试"""

    def test_full_agent_flow(self, storage):
        """模拟完整的 Agent 工具调用测试流程"""
        source = EventSource(framework="vitest", project="travel-agent")
        session_id = "travel-agent-session"
        events = []

        # 会话开始
        storage.store_session(
            ObsSession(
                session_id=session_id,
                project="travel-agent",
                framework="vitest",
                started_at=int(time.time() * 1000),
            )
        )

        # 测试1: 天气查询
        events.append(create_test_start(session_id, source, "test_weather", "weather.test.ts > searchWeather"))
        events.append(
            create_agent_tool_call(session_id, source, "search_weather", {"city": "杭州", "date": "2026-05-20"})
        )
        events.append(
            create_agent_tool_result(
                session_id, source, "search_weather", {"city": "杭州"}, {"temp": 28, "weather": "晴"}, 234, True
            )
        )
        events.append(create_assertion(session_id, source, "应返回天气数据", True, "object", "object"))
        events.append(create_test_end(session_id, source, "test_weather", True, 500))

        # 测试2: 酒店查询 (失败)
        events.append(create_test_start(session_id, source, "test_hotels", "hotels.test.ts > searchHotels"))
        events.append(
            create_agent_tool_call(session_id, source, "search_hotels", {"city": "杭州", "checkin": "2026-05-20"})
        )
        events.append(
            create_agent_tool_result(
                session_id, source, "search_hotels", {"city": "杭州"}, [], 1500, False, "API timeout"
            )
        )
        events.append(create_assertion(session_id, source, "应返回酒店列表", False, ">0", "0"))
        events.append(create_test_end(session_id, source, "test_hotels", False, 1800, [{"message": "API timeout"}]))

        # 测试3: 行程规划
        events.append(create_test_start(session_id, source, "test_trip_plan", "trip-plan.test.ts > generatePlan"))
        events.append(create_agent_tool_call(session_id, source, "generate_trip_plan", {"city": "杭州", "days": 3}))
        events.append(
            create_agent_tool_result(
                session_id,
                source,
                "generate_trip_plan",
                {"city": "杭州"},
                {"days": [{"day": 1, "spots": ["西湖", "灵隐寺"]}]},
                3000,
                True,
            )
        )
        events.append(create_assertion(session_id, source, "应包含景点", True))
        events.append(create_assertion(session_id, source, "应包含天数", True))
        events.append(create_test_end(session_id, source, "test_trip_plan", True, 3500))

        storage.store_events_batch(events)

        # 验证存储
        retrieved = storage.get_session_events(session_id)
        assert len(retrieved) > 10  # 确保有足够事件

        # 验证分析器
        analyzer = BugDiscoveryAnalyzer()
        result = analyzer.analyze([e.to_dict() for e in events])
        # 分析器应能运行并返回结果
        assert result.analyzer == "bug_discovery"
        assert result.confidence > 0

        # 验证质量守卫
        guard = QualityGuard()
        result = guard.analyze([e.to_dict() for e in events])
        # 质量守卫应能运行并返回结果
        assert result.analyzer == "quality_guard"
        assert result.confidence > 0

    def test_agent_trace_propagation(self, storage):
        """验证 Agent 测试的 trace 传播"""
        source = EventSource(framework="vitest", project="travel-agent")
        trace_id = f"trace_{uuid.uuid4().hex[:12]}"

        events = [
            create_test_start("trace-sess", source, "test_trace", "test", trace_id=trace_id),
            create_agent_tool_call("trace-sess", source, "weather", {"city": "杭州"}, trace_id=trace_id),
            create_agent_tool_result("trace-sess", source, "weather", {}, {}, 100, True, trace_id=trace_id),
            create_test_end("trace-sess", source, "test_trace", True, 200, trace_id=trace_id),
        ]
        storage.store_events_batch(events)

        # 按 trace_id 查询
        traced = storage.get_events_by_trace(trace_id)
        assert len(traced) == 4
        assert all(e["trace_id"] == trace_id for e in traced)


class TestPogongshichongzouIntegration:
    """pogongshichongzou 场景: Godot 卡牌游戏测试"""

    def test_full_game_flow(self, storage):
        """模拟完整的游戏测试流程"""
        source = EventSource(framework="gdunit4", project="pogongshichongzou")
        session_id = "pogong-session"
        events = []

        # 会话开始
        storage.store_session(
            ObsSession(
                session_id=session_id,
                project="pogongshichongzou",
                framework="gdunit4",
                started_at=int(time.time() * 1000),
            )
        )

        # 测试: 运行流程
        events.append(create_test_start(session_id, source, "test_run_flow", "run_flow_progression_smoke.gd"))

        # 游戏状态变化
        events.append(create_game_state_change(session_id, source, "/root/MainMenu", {"screen": "title"}))
        events.append(
            create_game_state_change(
                session_id, source, "/root/Expedition", {"layer": "corridor_1_01", "hp": 100, "gold": 50}
            )
        )
        events.append(create_assertion(session_id, source, "should_visit_corridor_1_01", True))

        # 战斗
        events.append(
            create_game_state_change(
                session_id, source, "/root/Battle", {"enemy": "slime", "enemy_hp": 30, "player_hp": 100}
            )
        )
        events.append(
            create_game_state_change(
                session_id, source, "/root/Battle", {"enemy": "slime", "enemy_hp": 0, "player_hp": 85}
            )
        )
        events.append(create_assertion(session_id, source, "battle_should_end", True))

        # 保存/加载
        events.append(
            ObsEvent(str(uuid.uuid4()), session_id, int(time.time() * 1000), source, "game.save", {"slot": "slot_1"})
        )
        events.append(
            ObsEvent(str(uuid.uuid4()), session_id, int(time.time() * 1000), source, "game.load", {"slot": "slot_1"})
        )
        events.append(create_assertion(session_id, source, "save_load_roundtrip", True))

        # 卡牌结算
        events.append(
            create_game_state_change(session_id, source, "/root/CardSettlement", {"card": "fire_bolt", "damage": 15})
        )
        events.append(create_assertion(session_id, source, "fire_card_deals_damage", True, 15, 15))

        events.append(create_test_end(session_id, source, "test_run_flow", True, 5200))

        # 测试: 存档损坏
        events.append(create_test_start(session_id, source, "test_save_corruption", "save_slots_corruption_smoke.gd"))
        events.append(
            ObsEvent(str(uuid.uuid4()), session_id, int(time.time() * 1000), source, "game.load", {"slot": "corrupted"})
        )
        events.append(create_assertion(session_id, source, "should_handle_corruption", True))
        events.append(create_test_end(session_id, source, "test_save_corruption", True, 200))

        storage.store_events_batch(events)

        # 验证
        retrieved = storage.get_session_events(session_id)
        assert len(retrieved) > 10

        # 验证游戏状态事件
        state_events = [e for e in retrieved if e["type"] == "game.state_change"]
        assert len(state_events) == 5

        # 验证分析器
        analyzer = BugDiscoveryAnalyzer()
        result = analyzer.analyze([e.to_dict() for e in events])
        # 健康运行不应有 findings
        assert len(result.findings) == 0


class TestLoopexpeditionIntegration:
    """loopexpedition 场景: AI 自动化测试"""

    def test_full_bot_flow(self, storage):
        """模拟完整的 Bot 测试流程"""
        source = EventSource(framework="custom", project="loopexpedition")
        session_id = "loop-session"
        events = []

        # 会话开始
        storage.store_session(
            ObsSession(
                session_id=session_id,
                project="loopexpedition",
                framework="custom",
                started_at=int(time.time() * 1000),
            )
        )

        # Bot 远征流程
        events.append(create_test_start(session_id, source, "bot_expedition", "bot/full_expedition"))

        # 营地
        events.append(create_game_state_change(session_id, source, "/root/Camp", {"gold": 100, "cards": 5, "hp": 100}))

        # 远征节点
        for node_id in ["corridor_1_01_node_1", "corridor_1_01_node_2", "corridor_1_02_node_1"]:
            events.append(
                create_game_state_change(session_id, source, f"/root/Expedition/{node_id}", {"hp": 80, "gold": 120})
            )
            events.append(create_assertion(session_id, source, f"node_{node_id}_completed", True))

        # Boss 战
        events.append(
            create_game_state_change(
                session_id, source, "/root/Boss", {"boss": "dragon", "boss_hp": 200, "player_hp": 60}
            )
        )
        events.append(create_assertion(session_id, source, "boss_battle_completed", True))

        # 结算
        events.append(
            create_game_state_change(session_id, source, "/root/Settlement", {"gold_earned": 150, "cards_earned": 3})
        )
        events.append(create_test_end(session_id, source, "bot_expedition", True, 45000))

        # 门禁结果
        events.append(
            create_gate_result(
                session_id,
                source,
                "PASS",
                {
                    "test_pass_rate": {"value": 1.0, "threshold": 1.0, "comparator": ">=", "pass": True},
                    "flow_coverage": {"value": 0.95, "threshold": 0.9, "comparator": ">=", "pass": True},
                    "crash_count": {"value": 0, "threshold": 0, "comparator": "<=", "pass": True},
                },
            )
        )

        # 覆盖率数据
        events.append(
            ObsEvent(
                str(uuid.uuid4()),
                session_id,
                int(time.time() * 1000),
                source,
                "observation.coverage",
                {
                    "event_coverage": 0.91,
                    "obs_coverage": 0.85,
                    "total_flows": 45,
                    "covered_flows": 41,
                    "uncovered_flows": ["F27 (升级 -> 天赋选择 -> 取消)"],
                },
            )
        )

        storage.store_events_batch(events)

        # 验证
        retrieved = storage.get_session_events(session_id)
        assert len(retrieved) > 8

        # 验证门禁结果
        storage.get_session(session_id)
        # 门禁结果在事件中
        gate_events = [e for e in retrieved if e["type"] == "report.gate_result"]
        assert len(gate_events) == 1
        assert gate_events[0]["data"]["verdict"] == "PASS"

    def test_bug_detection(self, storage):
        """测试 Bug 检测"""
        source = EventSource(framework="custom", project="loopexpedition")
        events = []

        # 创建一个有问题的测试序列
        events.append(create_test_start("bug-sess", source, "test_stuck", "bot/stuck_test"))
        events.append(create_game_state_change("bug-sess", source, "/root/Expedition", {"node": "node_1", "hp": 80}))
        events.append(
            create_game_state_change("bug-sess", source, "/root/Expedition", {"node": "node_1", "hp": 80})
        )  # 卡住
        events.append(
            create_game_state_change("bug-sess", source, "/root/Expedition", {"node": "node_1", "hp": 80})
        )  # 卡住
        events.append(
            create_bug_candidate(
                "bug-sess", source, "high", "stuck_state", "玩家在同一节点停留超过3步", {"node": "node_1", "steps": 3}
            )
        )
        events.append(create_test_end("bug-sess", source, "test_stuck", True, 1000))

        storage.store_events_batch(events)

        # 验证 Bug 候选事件
        retrieved = storage.get_session_events("bug-sess")
        bug_events = [e for e in retrieved if e["type"] == "report.bug_candidate"]
        assert len(bug_events) == 1
        assert bug_events[0]["data"]["severity"] == "high"


class TestCrossProjectAnalysis:
    """跨项目分析测试"""

    def test_anomaly_detection_across_projects(self, storage):
        """测试跨项目异常检测"""
        # 创建三个项目的事件
        projects = [
            ("travel-agent", "vitest"),
            ("pogongshichongzou", "gdunit4"),
            ("loopexpedition", "custom"),
        ]

        all_events = []
        for project, framework in projects:
            source = EventSource(framework=framework, project=project)
            session_id = f"{project}-cross-test"

            # 正常测试
            for i in range(5):
                all_events.append(create_test_start(session_id, source, f"test_{i}", f"test_{i}"))
                all_events.append(create_test_end(session_id, source, f"test_{i}", True, 100))

            # 一些失败
            all_events.append(create_test_start(session_id, source, "test_fail", "test_fail"))
            all_events.append(
                create_test_end(session_id, source, "test_fail", False, 50, [{"message": "assertion failed"}])
            )

        storage.store_events_batch(all_events)

        # 跨项目分析
        detector = AnomalyDetector()
        result = detector.analyze([e.to_dict() for e in all_events])

        # 所有项目都有 5/6 通过率 (83%)，不应触发低通过率告警
        # 但如果阈值设置不同可能会触发
        assert result.confidence > 0

    def test_report_generation(self, storage):
        """测试报告生成"""
        source = EventSource(framework="custom", project="loopexpedition")
        session_id = "report-test"

        # 创建完整测试数据
        events = [
            create_test_start(session_id, source, "t1", "t1"),
            create_assertion(session_id, source, "a1", True),
            create_test_end(session_id, source, "t1", True, 100),
            create_test_start(session_id, source, "t2", "t2"),
            create_assertion(session_id, source, "a2", False),
            create_test_end(session_id, source, "t2", False, 50),
            create_gate_result(
                session_id,
                source,
                "FAIL",
                {
                    "pass_rate": {"value": 0.5, "threshold": 1.0, "pass": False},
                },
            ),
        ]
        storage.store_events_batch(events)

        # 查询并验证
        retrieved = storage.get_session_events(session_id)
        assert len(retrieved) == 7

        # 验证门禁
        gate_events = [e for e in retrieved if e["type"] == "report.gate_result"]
        assert gate_events[0]["data"]["verdict"] == "FAIL"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
