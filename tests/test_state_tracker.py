"""状态追踪器测试

测试状态记录、深度 diff、时间线、Gateway API 集成。
"""

import time

import pytest
from fastapi import FastAPI

pytestmark = pytest.mark.medium
from fastapi.testclient import TestClient

from drivers.godot.state_tracker import (
    ChangeType,
    GameStateTracker,
    StateSnapshot,
    deep_diff,
)
from schema.events import ObsSession


# ─── 深度 diff 单元测试 ──────────────────────────────────


class TestDeepDiff:
    """测试嵌套字典的深度 diff"""

    def test_empty_dicts(self):
        """两个空字典应无变更"""
        changes = deep_diff({}, {})
        assert changes == []

    def test_added_fields(self):
        """新增字段"""
        changes = deep_diff({}, {"hp": 100, "name": "hero"})
        assert len(changes) == 2
        paths = {c.path for c in changes}
        assert paths == {"hp", "name"}
        for c in changes:
            assert c.change_type == ChangeType.ADDED
            assert c.old_value is None

    def test_removed_fields(self):
        """删除字段"""
        changes = deep_diff({"hp": 100, "shield": True}, {"hp": 100})
        assert len(changes) == 1
        assert changes[0].path == "shield"
        assert changes[0].change_type == ChangeType.REMOVED
        assert changes[0].old_value is True

    def test_modified_fields(self):
        """修改字段"""
        changes = deep_diff({"hp": 100}, {"hp": 80})
        assert len(changes) == 1
        assert changes[0].path == "hp"
        assert changes[0].change_type == ChangeType.MODIFIED
        assert changes[0].old_value == 100
        assert changes[0].new_value == 80

    def test_nested_dict_diff(self):
        """嵌套字典 diff"""
        old = {"player": {"hp": 100, "pos": {"x": 10, "y": 20}}}
        new = {"player": {"hp": 80, "pos": {"x": 15, "y": 20}, "shield": True}}
        changes = deep_diff(old, new)

        assert len(changes) == 3
        by_path = {c.path: c for c in changes}

        assert "player.hp" in by_path
        assert by_path["player.hp"].change_type == ChangeType.MODIFIED
        assert by_path["player.hp"].old_value == 100
        assert by_path["player.hp"].new_value == 80

        assert "player.pos.x" in by_path
        assert by_path["player.pos.x"].change_type == ChangeType.MODIFIED

        assert "player.shield" in by_path
        assert by_path["player.shield"].change_type == ChangeType.ADDED

    def test_deeply_nested_diff(self):
        """三层嵌套 diff"""
        old = {"a": {"b": {"c": {"d": 1}}}}
        new = {"a": {"b": {"c": {"d": 2}}}}
        changes = deep_diff(old, new)
        assert len(changes) == 1
        assert changes[0].path == "a.b.c.d"
        assert changes[0].old_value == 1
        assert changes[0].new_value == 2

    def test_no_change(self):
        """相同字典无变更"""
        state = {"hp": 100, "pos": {"x": 10}}
        changes = deep_diff(state, state)
        assert changes == []

    def test_mixed_changes(self):
        """混合变更：新增、删除、修改"""
        old = {"a": 1, "b": 2, "c": 3}
        new = {"a": 1, "b": 99, "d": 4}
        changes = deep_diff(old, new)

        by_path = {c.path: c for c in changes}
        assert len(by_path) == 3
        assert by_path["b"].change_type == ChangeType.MODIFIED
        assert by_path["c"].change_type == ChangeType.REMOVED
        assert by_path["d"].change_type == ChangeType.ADDED

    def test_list_values_treated_as_atomic(self):
        """列表值作为原子类型比较"""
        old = {"items": [1, 2, 3]}
        new = {"items": [1, 2, 4]}
        changes = deep_diff(old, new)
        assert len(changes) == 1
        assert changes[0].path == "items"
        assert changes[0].change_type == ChangeType.MODIFIED

    def test_type_change(self):
        """类型变更（str -> int）"""
        old = {"val": "hello"}
        new = {"val": 42}
        changes = deep_diff(old, new)
        assert len(changes) == 1
        assert changes[0].old_value == "hello"
        assert changes[0].new_value == 42


# ─── GameStateTracker 单元测试 ──────────────────────────


class TestGameStateTracker:
    """测试 GameStateTracker 核心功能"""

    def test_start_and_stop_tracking(self):
        """开始和停止追踪"""
        tracker = GameStateTracker()
        tracker.start_tracking("s1")
        assert tracker.is_tracking("s1")

        diff = tracker.stop_tracking("s1")
        assert diff.session_id == "s1"
        assert not tracker.is_tracking("s1")

    def test_record_state(self):
        """记录状态快照"""
        tracker = GameStateTracker()
        tracker.start_tracking("s1")

        snap = tracker.record_state("s1", {"hp": 100})
        assert isinstance(snap, StateSnapshot)
        assert snap.session_id == "s1"
        assert snap.state == {"hp": 100}

    def test_record_without_tracking_raises(self):
        """未追踪时记录状态应报错"""
        tracker = GameStateTracker()
        with pytest.raises(RuntimeError, match="not being tracked"):
            tracker.record_state("s1", {"hp": 100})

    def test_stop_without_tracking_raises(self):
        """未追踪时停止应报错"""
        tracker = GameStateTracker()
        with pytest.raises(RuntimeError, match="not being tracked"):
            tracker.stop_tracking("s1")

    def test_stop_with_no_snapshots(self):
        """无快照时停止应返回空 diff"""
        tracker = GameStateTracker()
        tracker.start_tracking("s1")
        diff = tracker.stop_tracking("s1")
        assert diff.changes == []
        assert diff.from_snapshot is None
        assert diff.to_snapshot is None

    def test_stop_with_single_snapshot(self):
        """单个快照时停止应返回无变更 diff"""
        tracker = GameStateTracker()
        tracker.start_tracking("s1")
        tracker.record_state("s1", {"hp": 100})
        diff = tracker.stop_tracking("s1")
        assert diff.changes == []
        assert diff.summary["total"] == 0

    def test_diff_between_first_and_last(self):
        """stop_tracking 应计算首尾差异"""
        tracker = GameStateTracker()
        tracker.start_tracking("s1")
        tracker.record_state("s1", {"hp": 100, "mp": 50})
        tracker.record_state("s1", {"hp": 80, "mp": 50})
        tracker.record_state("s1", {"hp": 60, "mp": 30, "shield": True})

        diff = tracker.stop_tracking("s1")
        assert diff.summary["modified"] >= 2  # hp, mp
        assert diff.summary["added"] == 1  # shield

    def test_get_snapshots(self):
        """获取所有快照"""
        tracker = GameStateTracker()
        tracker.start_tracking("s1")
        tracker.record_state("s1", {"hp": 100})
        tracker.record_state("s1", {"hp": 80})
        tracker.record_state("s1", {"hp": 60})

        snapshots = tracker.get_snapshots("s1")
        assert len(snapshots) == 3
        assert [s.state["hp"] for s in snapshots] == [100, 80, 60]

    def test_state_isolation(self):
        """快照状态应是深拷贝，修改原状态不影响快照"""
        tracker = GameStateTracker()
        tracker.start_tracking("s1")

        state = {"hp": 100, "pos": {"x": 10}}
        snap = tracker.record_state("s1", state)

        # 修改原始 dict
        state["hp"] = 0
        state["pos"]["x"] = 999

        assert snap.state["hp"] == 100
        assert snap.state["pos"]["x"] == 10

    def test_multiple_sessions(self):
        """多 session 独立追踪"""
        tracker = GameStateTracker()
        tracker.start_tracking("s1")
        tracker.start_tracking("s2")

        tracker.record_state("s1", {"hp": 100})
        tracker.record_state("s2", {"hp": 200})

        diff1 = tracker.stop_tracking("s1")
        diff2 = tracker.stop_tracking("s2")

        assert diff1.from_snapshot == {"hp": 100}
        assert diff2.from_snapshot == {"hp": 200}


# ─── 时间线测试 ─────────────────────────────────────────


class TestTimeline:
    """测试时间线生成"""

    def test_empty_timeline(self):
        """无快照时时间线为空"""
        tracker = GameStateTracker()
        tracker.start_tracking("s1")
        timeline = tracker.get_timeline("s1")
        assert timeline == []

    def test_single_entry_timeline(self):
        """单条快照时间线无变更"""
        tracker = GameStateTracker()
        tracker.start_tracking("s1")
        tracker.record_state("s1", {"hp": 100})

        timeline = tracker.get_timeline("s1")
        assert len(timeline) == 1
        assert timeline[0].changes_from_previous == []

    def test_multi_entry_timeline(self):
        """多条快照时间线有渐进变更"""
        tracker = GameStateTracker()
        tracker.start_tracking("s1")
        tracker.record_state("s1", {"hp": 100, "mp": 50})
        tracker.record_state("s1", {"hp": 80, "mp": 50})
        tracker.record_state("s1", {"hp": 60, "mp": 50, "shield": True})

        timeline = tracker.get_timeline("s1")
        assert len(timeline) == 3

        # 第一条无变更
        assert timeline[0].changes_from_previous == []

        # 第二条：hp 修改
        changes_1 = timeline[1].changes_from_previous
        assert len(changes_1) == 1
        assert changes_1[0].path == "hp"
        assert changes_1[0].change_type == ChangeType.MODIFIED

        # 第三条：hp 修改 + shield 新增
        changes_2 = timeline[2].changes_from_previous
        paths = {c.path for c in changes_2}
        assert "hp" in paths
        assert "shield" in paths

    def test_timeline_entry_has_state(self):
        """每个时间线条目应包含完整状态"""
        tracker = GameStateTracker()
        tracker.start_tracking("s1")
        tracker.record_state("s1", {"hp": 100})
        tracker.record_state("s1", {"hp": 80})

        timeline = tracker.get_timeline("s1")
        assert timeline[0].state == {"hp": 100}
        assert timeline[1].state == {"hp": 80}


# ─── get_diff_between 测试 ─────────────────────────────


class TestDiffBetween:
    """测试索引范围 diff"""

    def test_diff_between_indices(self):
        """指定索引范围的 diff"""
        tracker = GameStateTracker()
        tracker.start_tracking("s1")
        tracker.record_state("s1", {"hp": 100})  # 0
        tracker.record_state("s1", {"hp": 80})   # 1
        tracker.record_state("s1", {"hp": 60})   # 2

        diff = tracker.get_diff_between("s1", 0, 2)
        assert diff.summary["modified"] == 1
        changes = [c for c in diff.changes if c.path == "hp"]
        assert changes[0].old_value == 100
        assert changes[0].new_value == 60

    def test_diff_between_adjacent(self):
        """相邻快照 diff"""
        tracker = GameStateTracker()
        tracker.start_tracking("s1")
        tracker.record_state("s1", {"hp": 100})
        tracker.record_state("s1", {"hp": 80})

        diff = tracker.get_diff_between("s1", 0, 1)
        assert len(diff.changes) == 1

    def test_diff_between_no_snapshots(self):
        """无快照时报错"""
        tracker = GameStateTracker()
        tracker.start_tracking("s1")
        with pytest.raises(RuntimeError, match="No snapshots"):
            tracker.get_diff_between("s1", 0, 0)

    def test_diff_between_out_of_range(self):
        """索引越界报错"""
        tracker = GameStateTracker()
        tracker.start_tracking("s1")
        tracker.record_state("s1", {"hp": 100})
        with pytest.raises(IndexError):
            tracker.get_diff_between("s1", 0, 5)


# ─── Gateway API 集成测试 ───────────────────────────────


@pytest.fixture
def app():
    """创建测试用 FastAPI 应用"""
    app = FastAPI()

    storage = __import__("gateway.storage", fromlist=["Storage"]).Storage(db_path=":memory:")
    from drivers.godot.state_tracker import GameStateTracker

    app.state.storage = storage
    app.state.state_tracker = GameStateTracker()

    from gateway.routes.state import router
    app.include_router(router)

    # 创建测试 session
    session = ObsSession(
        session_id="test-session-1",
        project="test-project",
        framework="godot_driver",
        started_at=int(time.time() * 1000),
    )
    storage.store_session(session)

    return app


@pytest.fixture
def client(app):
    """创建测试客户端"""
    return TestClient(app)


class TestStateAPI:
    """测试 Gateway 状态 API"""

    def test_record_state(self, client):
        """POST 记录状态快照"""
        resp = client.post(
            "/sessions/test-session-1/states",
            json={"state": {"hp": 100, "pos": {"x": 10, "y": 20}}},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["snapshot"]["session_id"] == "test-session-1"
        assert data["snapshot"]["state"]["hp"] == 100
        assert data["snapshot"]["state"]["pos"]["x"] == 10

    def test_record_multiple_states(self, client):
        """多次记录状态"""
        for hp in [100, 80, 60]:
            resp = client.post(
                "/sessions/test-session-1/states",
                json={"state": {"hp": hp}},
            )
            assert resp.status_code == 200

    def test_record_state_invalid_session(self, client):
        """不存在的 session 应返回 404"""
        resp = client.post(
            "/sessions/nonexistent/states",
            json={"state": {"hp": 100}},
        )
        assert resp.status_code == 404

    def test_list_states_empty(self, client):
        """空状态列表"""
        resp = client.get("/sessions/test-session-1/states")
        assert resp.status_code == 200
        data = resp.json()
        assert data["snapshots"] == []
        assert data["count"] == 0

    def test_list_states(self, client):
        """列出状态"""
        client.post("/sessions/test-session-1/states", json={"state": {"hp": 100}})
        client.post("/sessions/test-session-1/states", json={"state": {"hp": 80}})

        resp = client.get("/sessions/test-session-1/states")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 2
        assert len(data["snapshots"]) == 2

    def test_list_states_with_pagination(self, client):
        """状态列表分页"""
        for i in range(5):
            client.post("/sessions/test-session-1/states", json={"state": {"step": i}})

        resp = client.get("/sessions/test-session-1/states?limit=2&offset=1")
        data = resp.json()
        assert data["count"] == 5
        assert len(data["snapshots"]) == 2

    def test_list_states_invalid_session(self, client):
        """不存在的 session 列出状态应返回 404"""
        resp = client.get("/sessions/nonexistent/states")
        assert resp.status_code == 404

    def test_get_state_diff(self, client):
        """GET 获取状态差异"""
        client.post(
            "/sessions/test-session-1/states",
            json={"state": {"hp": 100, "mp": 50}},
        )
        client.post(
            "/sessions/test-session-1/states",
            json={"state": {"hp": 80, "mp": 50, "shield": True}},
        )

        resp = client.get("/sessions/test-session-1/states/diff")
        assert resp.status_code == 200
        data = resp.json()
        diff = data["diff"]
        assert diff["summary"]["modified"] >= 1
        assert diff["summary"]["added"] == 1
        assert diff["from_state"]["hp"] == 100
        assert diff["to_state"]["hp"] == 80

    def test_get_state_diff_with_indices(self, client):
        """GET 指定索引范围的状态差异"""
        client.post("/sessions/test-session-1/states", json={"state": {"hp": 100}})
        client.post("/sessions/test-session-1/states", json={"state": {"hp": 80}})
        client.post("/sessions/test-session-1/states", json={"state": {"hp": 60}})

        resp = client.get("/sessions/test-session-1/states/diff?from_index=0&to_index=1")
        assert resp.status_code == 200
        diff = resp.json()["diff"]
        assert diff["from_state"]["hp"] == 100
        assert diff["to_state"]["hp"] == 80

    def test_get_state_diff_no_snapshots(self, client):
        """无快照时获取 diff 应返回 404"""
        resp = client.get("/sessions/test-session-1/states/diff")
        assert resp.status_code == 404

    def test_get_state_diff_out_of_range(self, client):
        """索引越界应返回 400"""
        client.post("/sessions/test-session-1/states", json={"state": {"hp": 100}})
        resp = client.get("/sessions/test-session-1/states/diff?from_index=0&to_index=99")
        assert resp.status_code == 400

    def test_get_state_timeline(self, client):
        """GET 获取状态时间线"""
        client.post("/sessions/test-session-1/states", json={"state": {"hp": 100, "mp": 50}})
        client.post("/sessions/test-session-1/states", json={"state": {"hp": 80, "mp": 50}})
        client.post(
            "/sessions/test-session-1/states",
            json={"state": {"hp": 60, "mp": 50, "shield": True}},
        )

        resp = client.get("/sessions/test-session-1/states/timeline")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 3
        assert len(data["timeline"]) == 3

        # 第一条无变更
        assert data["timeline"][0]["changes"] == []

        # 第二条有 hp 变更
        changes_1 = data["timeline"][1]["changes"]
        assert len(changes_1) == 1
        assert changes_1[0]["path"] == "hp"
        assert changes_1[0]["change_type"] == "modified"

        # 第三条有 hp 修改 + shield 新增
        changes_2 = data["timeline"][2]["changes"]
        paths = {c["path"] for c in changes_2}
        assert "hp" in paths
        assert "shield" in paths

    def test_get_state_timeline_empty(self, client):
        """空时间线"""
        resp = client.get("/sessions/test-session-1/states/timeline")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 0
        assert data["timeline"] == []

    def test_get_state_timeline_invalid_session(self, client):
        """不存在的 session 获取时间线应返回 404"""
        resp = client.get("/sessions/nonexistent/states/timeline")
        assert resp.status_code == 404

    def test_nested_state_diff_via_api(self, client):
        """通过 API 测试嵌套字典 diff"""
        client.post(
            "/sessions/test-session-1/states",
            json={
                "state": {
                    "player": {"hp": 100, "pos": {"x": 10, "y": 20}},
                    "enemy": {"hp": 200},
                },
            },
        )
        client.post(
            "/sessions/test-session-1/states",
            json={
                "state": {
                    "player": {"hp": 80, "pos": {"x": 15, "y": 20}, "shield": True},
                    "enemy": {"hp": 150},
                },
            },
        )

        resp = client.get("/sessions/test-session-1/states/diff")
        assert resp.status_code == 200
        diff = resp.json()["diff"]

        # 应有 player.hp, player.pos.x, player.shield, enemy.hp 共 4 处变更
        assert diff["summary"]["total"] == 4

        by_path = {c["path"]: c for c in diff["changes"]}
        assert by_path["player.hp"]["change_type"] == "modified"
        assert by_path["player.hp"]["old_value"] == 100
        assert by_path["player.hp"]["new_value"] == 80
        assert by_path["player.pos.x"]["change_type"] == "modified"
        assert by_path["player.shield"]["change_type"] == "added"
        assert by_path["enemy.hp"]["change_type"] == "modified"

    def test_full_workflow(self, client):
        """完整工作流：记录多个状态 → 查看列表 → 查看 diff → 查看 timeline"""
        # 记录 4 个状态
        states = [
            {"hp": 100, "mp": 50, "scene": "lobby"},
            {"hp": 100, "mp": 30, "scene": "lobby", "buff": "speed"},
            {"hp": 80, "mp": 30, "scene": "battle"},
            {"hp": 60, "mp": 10, "scene": "battle", "buff": "speed"},
        ]
        for state in states:
            resp = client.post(
                "/sessions/test-session-1/states",
                json={"state": state},
            )
            assert resp.status_code == 200

        # 列表
        resp = client.get("/sessions/test-session-1/states")
        assert resp.json()["count"] == 4

        # diff（首尾）
        resp = client.get("/sessions/test-session-1/states/diff")
        diff = resp.json()["diff"]
        assert diff["from_state"]["scene"] == "lobby"
        assert diff["to_state"]["scene"] == "battle"

        # diff（中间两步）
        resp = client.get("/sessions/test-session-1/states/diff?from_index=1&to_index=2")
        diff = resp.json()["diff"]
        by_path = {c["path"]: c for c in diff["changes"]}
        assert by_path["hp"]["old_value"] == 100
        assert by_path["hp"]["new_value"] == 80
        assert by_path["scene"]["old_value"] == "lobby"
        assert by_path["scene"]["new_value"] == "battle"
        assert by_path["buff"]["change_type"] == "removed"

        # timeline
        resp = client.get("/sessions/test-session-1/states/timeline")
        timeline = resp.json()["timeline"]
        assert len(timeline) == 4

        # 第一条无变更
        assert timeline[0]["changes"] == []

        # 最后一条有变更
        last_changes = timeline[3]["changes"]
        assert len(last_changes) >= 1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
