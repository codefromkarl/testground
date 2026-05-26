"""游戏状态追踪器 — 记录状态快照、计算深度 diff

用法:
    tracker = GameStateTracker()
    tracker.start_tracking("session-1")
    tracker.record_state({"hp": 100, "pos": {"x": 10, "y": 20}})
    tracker.record_state({"hp": 80,  "pos": {"x": 15, "y": 20}, "shield": True})
    diff = tracker.stop_tracking()
"""

from __future__ import annotations

import copy
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class ChangeType(str, Enum):
    """变更类型"""

    ADDED = "added"
    REMOVED = "removed"
    MODIFIED = "modified"


@dataclass
class FieldChange:
    """单个字段的变更"""

    path: str
    change_type: ChangeType
    old_value: Any = None
    new_value: Any = None

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "path": self.path,
            "change_type": self.change_type.value,
        }
        if self.old_value is not None:
            d["old_value"] = self.old_value
        if self.new_value is not None:
            d["new_value"] = self.new_value
        return d


@dataclass
class StateSnapshot:
    """状态快照"""

    snapshot_id: str
    session_id: str
    timestamp: int
    state: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "snapshot_id": self.snapshot_id,
            "session_id": self.session_id,
            "timestamp": self.timestamp,
            "state": self.state,
        }


@dataclass
class StateDiff:
    """两次状态之间的差异"""

    diff_id: str
    session_id: str
    from_snapshot: Optional[Dict[str, Any]] = None
    to_snapshot: Optional[Dict[str, Any]] = None
    from_timestamp: int = 0
    to_timestamp: int = 0
    changes: List[FieldChange] = field(default_factory=list)
    summary: Dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "diff_id": self.diff_id,
            "session_id": self.session_id,
            "from_timestamp": self.from_timestamp,
            "to_timestamp": self.to_timestamp,
            "from_state": self.from_snapshot,
            "to_state": self.to_snapshot,
            "changes": [c.to_dict() for c in self.changes],
            "summary": self.summary,
        }


@dataclass
class TimelineEntry:
    """时间线条目"""

    entry_id: str
    session_id: str
    timestamp: int
    state: Dict[str, Any]
    changes_from_previous: List[FieldChange] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "entry_id": self.entry_id,
            "session_id": self.session_id,
            "timestamp": self.timestamp,
            "state": self.state,
            "changes": [c.to_dict() for c in self.changes_from_previous],
        }


def deep_diff(
    old: Dict[str, Any],
    new: Dict[str, Any],
    prefix: str = "",
) -> List[FieldChange]:
    """计算两个嵌套字典之间的深度差异

    Args:
        old: 旧状态
        new: 新状态
        prefix: 字段路径前缀（递归用）

    Returns:
        变更列表
    """
    changes: List[FieldChange] = []

    all_keys = set(old.keys()) | set(new.keys())

    for key in sorted(all_keys):
        path = f"{prefix}.{key}" if prefix else key
        old_val = old.get(key, _SENTINEL)
        new_val = new.get(key, _SENTINEL)

        # key 只在 new 中
        if old_val is _SENTINEL:
            changes.append(FieldChange(path=path, change_type=ChangeType.ADDED, new_value=new_val))
            continue

        # key 只在 old 中
        if new_val is _SENTINEL:
            changes.append(FieldChange(path=path, change_type=ChangeType.REMOVED, old_value=old_val))
            continue

        # 两边都有，递归比较
        if isinstance(old_val, dict) and isinstance(new_val, dict):
            changes.extend(deep_diff(old_val, new_val, prefix=path))
        elif old_val != new_val:
            changes.append(
                FieldChange(
                    path=path,
                    change_type=ChangeType.MODIFIED,
                    old_value=old_val,
                    new_value=new_val,
                )
            )

    return changes


_SENTINEL = object()


class GameStateTracker:
    """游戏状态追踪器

    追踪 session 期间的游戏状态变化，支持:
    - 记录状态快照
    - 计算深度 diff（嵌套字典）
    - 生成 timeline 时间线
    """

    def __init__(self) -> None:
        # session_id -> list of snapshots
        self._sessions: Dict[str, List[StateSnapshot]] = {}
        # session_id -> tracking active
        self._active: Dict[str, bool] = {}

    def start_tracking(self, session_id: str) -> None:
        """开始追踪指定 session 的状态变化"""
        self._sessions[session_id] = []
        self._active[session_id] = True

    def record_state(self, session_id: str, state: Dict[str, Any]) -> StateSnapshot:
        """记录一个状态快照

        Args:
            session_id: 会话 ID
            state: 当前游戏状态（支持嵌套字典）

        Returns:
            创建的快照对象

        Raises:
            RuntimeError: 如果 session 未在追踪
        """
        if not self._active.get(session_id):
            raise RuntimeError(f"Session {session_id} is not being tracked")

        snapshot = StateSnapshot(
            snapshot_id=str(uuid.uuid4()),
            session_id=session_id,
            timestamp=int(time.time() * 1000),
            state=copy.deepcopy(state),
        )
        self._sessions[session_id].append(snapshot)
        return snapshot

    def stop_tracking(self, session_id: str) -> StateDiff:
        """停止追踪并返回首尾状态差异

        Returns:
            第一个快照和最后一个快照之间的差异

        Raises:
            RuntimeError: 如果 session 未在追踪
        """
        if session_id not in self._active:
            raise RuntimeError(f"Session {session_id} is not being tracked")

        snapshots = self._sessions.get(session_id, [])
        self._active[session_id] = False

        if len(snapshots) == 0:
            return StateDiff(
                diff_id=str(uuid.uuid4()),
                session_id=session_id,
            )

        if len(snapshots) == 1:
            first = snapshots[0]
            return StateDiff(
                diff_id=str(uuid.uuid4()),
                session_id=session_id,
                from_snapshot=first.state,
                to_snapshot=first.state,
                from_timestamp=first.timestamp,
                to_timestamp=first.timestamp,
                changes=[],
                summary={"added": 0, "removed": 0, "modified": 0, "total": 0},
            )

        first = snapshots[0]
        last = snapshots[-1]
        changes = deep_diff(first.state, last.state)

        return self._build_diff(session_id, first, last, changes)

    def get_diff_between(
        self,
        session_id: str,
        from_index: int = 0,
        to_index: int = -1,
    ) -> StateDiff:
        """获取两个快照索引之间的差异

        Args:
            from_index: 起始快照索引
            to_index: 结束快照索引（-1 表示最后一个）

        Returns:
            两个快照之间的差异
        """
        snapshots = self._sessions.get(session_id, [])
        if not snapshots:
            raise RuntimeError(f"No snapshots for session {session_id}")

        if to_index == -1:
            to_index = len(snapshots) - 1

        if from_index < 0 or from_index >= len(snapshots):
            raise IndexError(f"from_index {from_index} out of range")
        if to_index < 0 or to_index >= len(snapshots):
            raise IndexError(f"to_index {to_index} out of range")

        first = snapshots[from_index]
        last = snapshots[to_index]
        changes = deep_diff(first.state, last.state)

        return self._build_diff(session_id, first, last, changes)

    def get_snapshots(self, session_id: str) -> List[StateSnapshot]:
        """获取 session 的所有快照"""
        return list(self._sessions.get(session_id, []))

    def get_timeline(self, session_id: str) -> List[TimelineEntry]:
        """获取状态时间线

        每个条目包含当前状态和相对于上一个状态的变更。
        """
        snapshots = self._sessions.get(session_id, [])
        if not snapshots:
            return []

        entries: List[TimelineEntry] = []
        for i, snap in enumerate(snapshots):
            changes: List[FieldChange] = []
            if i > 0:
                changes = deep_diff(snapshots[i - 1].state, snap.state)

            entries.append(
                TimelineEntry(
                    entry_id=snap.snapshot_id,
                    session_id=session_id,
                    timestamp=snap.timestamp,
                    state=snap.state,
                    changes_from_previous=changes,
                )
            )

        return entries

    def is_tracking(self, session_id: str) -> bool:
        """检查 session 是否正在追踪"""
        return self._active.get(session_id, False)

    @staticmethod
    def _build_diff(
        session_id: str,
        from_snap: StateSnapshot,
        to_snap: StateSnapshot,
        changes: List[FieldChange],
    ) -> StateDiff:
        """构建 StateDiff 对象"""
        summary = {"added": 0, "removed": 0, "modified": 0, "total": len(changes)}
        for c in changes:
            if c.change_type == ChangeType.ADDED:
                summary["added"] += 1
            elif c.change_type == ChangeType.REMOVED:
                summary["removed"] += 1
            elif c.change_type == ChangeType.MODIFIED:
                summary["modified"] += 1

        return StateDiff(
            diff_id=str(uuid.uuid4()),
            session_id=session_id,
            from_snapshot=from_snap.state,
            to_snapshot=to_snap.state,
            from_timestamp=from_snap.timestamp,
            to_timestamp=to_snap.timestamp,
            changes=changes,
            summary=summary,
        )
