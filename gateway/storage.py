"""SQLite 存储层

存储和查询测试事件、会话、分析结果。
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from typing import Any, Dict, Generator, List, Optional

from schema.events import AnalysisResult, ObsEvent, ObsSession

# ─── 数据库初始化 ──────────────────────────────────────────

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS test_events (
    event_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    timestamp INTEGER NOT NULL,
    framework TEXT NOT NULL,
    project TEXT NOT NULL,
    file TEXT,
    test_name TEXT,
    type TEXT NOT NULL,
    data TEXT NOT NULL,
    parent_event_id TEXT,
    trace_id TEXT,
    span_id TEXT
);

CREATE INDEX IF NOT EXISTS idx_events_session ON test_events(session_id);
CREATE INDEX IF NOT EXISTS idx_events_timestamp ON test_events(timestamp);
CREATE INDEX IF NOT EXISTS idx_events_type ON test_events(type);
CREATE INDEX IF NOT EXISTS idx_events_project ON test_events(project);
CREATE INDEX IF NOT EXISTS idx_events_trace ON test_events(trace_id);

CREATE TABLE IF NOT EXISTS test_sessions (
    session_id TEXT PRIMARY KEY,
    project TEXT NOT NULL,
    framework TEXT NOT NULL,
    started_at INTEGER NOT NULL,
    ended_at INTEGER,
    total_tests INTEGER,
    passed_tests INTEGER,
    failed_tests INTEGER,
    duration_ms INTEGER,
    gate_result TEXT,
    metadata TEXT
);

CREATE INDEX IF NOT EXISTS idx_sessions_project ON test_sessions(project);
CREATE INDEX IF NOT EXISTS idx_sessions_started ON test_sessions(started_at);

CREATE TABLE IF NOT EXISTS ai_analyses (
    analysis_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    timestamp INTEGER NOT NULL,
    analyzer TEXT NOT NULL,
    result TEXT NOT NULL,
    confidence REAL,
    summary TEXT
);

CREATE INDEX IF NOT EXISTS idx_analyses_session ON ai_analyses(session_id);
"""


class Storage:
    """SQLite 存储层"""

    def __init__(self, db_path: str = "test_observability.db") -> None:
        self.db_path = db_path
        self._in_memory = db_path == ":memory:"
        self._conn: Optional[sqlite3.Connection] = None
        self._init_db()

    def _init_db(self) -> None:
        """初始化数据库表"""
        with self._connect() as conn:
            conn.executescript(SCHEMA_SQL)

    @contextmanager
    def _connect(self) -> Generator[sqlite3.Connection, None, None]:
        """获取数据库连接"""
        if self._in_memory:
            # 内存数据库使用单一连接
            if self._conn is None:
                self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
                self._conn.row_factory = sqlite3.Row
            conn = self._conn
            try:
                yield conn
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        else:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            try:
                yield conn
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()

    # ─── 事件操作 ──────────────────────────────────────────

    def store_event(self, event: ObsEvent) -> None:
        """存储单个事件"""
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO test_events
                (event_id, session_id, timestamp, framework, project, file,
                 test_name, type, data, parent_event_id, trace_id, span_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.event_id,
                    event.session_id,
                    event.timestamp,
                    event.source.framework,
                    event.source.project,
                    event.source.file,
                    event.source.test_name,
                    event.type,
                    json.dumps(event.data, ensure_ascii=False),
                    event.parent_event_id,
                    event.trace_id,
                    event.span_id,
                ),
            )

    def store_events_batch(self, events: List[ObsEvent]) -> int:
        """批量存储事件，返回存储数量"""
        with self._connect() as conn:
            conn.executemany(
                """
                INSERT OR REPLACE INTO test_events
                (event_id, session_id, timestamp, framework, project, file,
                 test_name, type, data, parent_event_id, trace_id, span_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        e.event_id,
                        e.session_id,
                        e.timestamp,
                        e.source.framework,
                        e.source.project,
                        e.source.file,
                        e.source.test_name,
                        e.type,
                        json.dumps(e.data, ensure_ascii=False),
                        e.parent_event_id,
                        e.trace_id,
                        e.span_id,
                    )
                    for e in events
                ],
            )
        return len(events)

    def get_session_events(
        self,
        session_id: str,
        event_type: Optional[str] = None,
        limit: int = 1000,
    ) -> List[Dict[str, Any]]:
        """获取会话的事件列表"""
        with self._connect() as conn:
            if event_type:
                cursor = conn.execute(
                    """
                    SELECT * FROM test_events
                    WHERE session_id = ? AND type = ?
                    ORDER BY timestamp
                    LIMIT ?
                    """,
                    (session_id, event_type, limit),
                )
            else:
                cursor = conn.execute(
                    """
                    SELECT * FROM test_events
                    WHERE session_id = ?
                    ORDER BY timestamp
                    LIMIT ?
                    """,
                    (session_id, limit),
                )
            return [self._row_to_event_dict(row) for row in cursor.fetchall()]

    def get_events_by_trace(self, trace_id: str) -> List[Dict[str, Any]]:
        """按 trace_id 查询事件"""
        with self._connect() as conn:
            cursor = conn.execute(
                "SELECT * FROM test_events WHERE trace_id = ? ORDER BY timestamp",
                (trace_id,),
            )
            return [self._row_to_event_dict(row) for row in cursor.fetchall()]

    def get_events_by_project(
        self,
        project: str,
        since: Optional[int] = None,
        limit: int = 1000,
    ) -> List[Dict[str, Any]]:
        """按项目查询事件"""
        with self._connect() as conn:
            if since:
                cursor = conn.execute(
                    """
                    SELECT * FROM test_events
                    WHERE project = ? AND timestamp >= ?
                    ORDER BY timestamp DESC
                    LIMIT ?
                    """,
                    (project, since, limit),
                )
            else:
                cursor = conn.execute(
                    """
                    SELECT * FROM test_events
                    WHERE project = ?
                    ORDER BY timestamp DESC
                    LIMIT ?
                    """,
                    (project, limit),
                )
            return [self._row_to_event_dict(row) for row in cursor.fetchall()]

    # ─── 会话操作 ──────────────────────────────────────────

    def store_session(self, session: ObsSession) -> None:
        """存储测试会话"""
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO test_sessions
                (session_id, project, framework, started_at, ended_at,
                 total_tests, passed_tests, failed_tests, duration_ms,
                 gate_result, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session.session_id,
                    session.project,
                    session.framework,
                    session.started_at,
                    session.ended_at,
                    session.total_tests,
                    session.passed_tests,
                    session.failed_tests,
                    session.duration_ms,
                    json.dumps(session.gate_result, ensure_ascii=False) if session.gate_result else None,
                    json.dumps(session.metadata, ensure_ascii=False) if session.metadata else None,
                ),
            )

    def get_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        """获取会话信息"""
        with self._connect() as conn:
            cursor = conn.execute(
                "SELECT * FROM test_sessions WHERE session_id = ?",
                (session_id,),
            )
            row = cursor.fetchone()
            if row:
                return self._row_to_session_dict(row)
            return None

    def get_recent_sessions(self, project: Optional[str] = None, limit: int = 20) -> List[Dict[str, Any]]:
        """获取最近的会话"""
        with self._connect() as conn:
            if project:
                cursor = conn.execute(
                    """
                    SELECT * FROM test_sessions
                    WHERE project = ?
                    ORDER BY started_at DESC
                    LIMIT ?
                    """,
                    (project, limit),
                )
            else:
                cursor = conn.execute(
                    """
                    SELECT * FROM test_sessions
                    ORDER BY started_at DESC
                    LIMIT ?
                    """,
                    (limit,),
                )
            return [self._row_to_session_dict(row) for row in cursor.fetchall()]

    # ─── 分析结果操作 ──────────────────────────────────────

    def store_analysis(self, analysis: AnalysisResult) -> None:
        """存储 AI 分析结果"""
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO ai_analyses
                (analysis_id, session_id, timestamp, analyzer, result, confidence, summary)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    analysis.analysis_id,
                    analysis.session_id,
                    analysis.timestamp,
                    analysis.analyzer,
                    json.dumps(analysis.to_dict(), ensure_ascii=False),
                    analysis.confidence,
                    analysis.summary,
                ),
            )

    def get_session_analyses(self, session_id: str) -> List[Dict[str, Any]]:
        """获取会话的分析结果"""
        with self._connect() as conn:
            cursor = conn.execute(
                """
                SELECT * FROM ai_analyses
                WHERE session_id = ?
                ORDER BY timestamp
                """,
                (session_id,),
            )
            results = []
            for row in cursor.fetchall():
                result = json.loads(row["result"])
                results.append(result)
            return results

    # ─── 统计查询 ──────────────────────────────────────────

    def get_project_stats(self, project: str, days: int = 7) -> Dict[str, Any]:
        """获取项目统计"""
        import time

        since = int((time.time() - days * 86400) * 1000)
        with self._connect() as conn:
            # 会话统计
            cursor = conn.execute(
                """
                SELECT COUNT(*) as total_sessions,
                       SUM(CASE WHEN ended_at IS NOT NULL THEN 1 ELSE 0 END) as completed,
                       AVG(duration_ms) as avg_duration
                FROM test_sessions
                WHERE project = ? AND started_at >= ?
                """,
                (project, since),
            )
            session_stats = dict(cursor.fetchone())

            # 事件统计
            cursor = conn.execute(
                """
                SELECT type, COUNT(*) as count
                FROM test_events
                WHERE project = ? AND timestamp >= ?
                GROUP BY type
                ORDER BY count DESC
                """,
                (project, since),
            )
            event_stats = {row["type"]: row["count"] for row in cursor.fetchall()}

            # 失败率
            total_tests = event_stats.get("test.end", 0) + event_stats.get("test.fail", 0)
            failed_tests = event_stats.get("test.fail", 0)

            return {
                "project": project,
                "period_days": days,
                "sessions": session_stats,
                "events": event_stats,
                "pass_rate": 1 - (failed_tests / total_tests) if total_tests > 0 else 1.0,
            }

    # ─── 辅助方法 ──────────────────────────────────────────

    @staticmethod
    def _row_to_event_dict(row: sqlite3.Row) -> Dict[str, Any]:
        """将数据库行转换为事件字典"""
        return {
            "event_id": row["event_id"],
            "session_id": row["session_id"],
            "timestamp": row["timestamp"],
            "source": {
                "framework": row["framework"],
                "project": row["project"],
                "file": row["file"],
                "test_name": row["test_name"],
            },
            "type": row["type"],
            "data": json.loads(row["data"]),
            "parent_event_id": row["parent_event_id"],
            "trace_id": row["trace_id"],
            "span_id": row["span_id"],
        }

    @staticmethod
    def _row_to_session_dict(row: sqlite3.Row) -> Dict[str, Any]:
        """将数据库行转换为会话字典"""
        d = {
            "session_id": row["session_id"],
            "project": row["project"],
            "framework": row["framework"],
            "started_at": row["started_at"],
        }
        if row["ended_at"]:
            d["ended_at"] = row["ended_at"]
        if row["total_tests"] is not None:
            d["total_tests"] = row["total_tests"]
        if row["passed_tests"] is not None:
            d["passed_tests"] = row["passed_tests"]
        if row["failed_tests"] is not None:
            d["failed_tests"] = row["failed_tests"]
        if row["duration_ms"] is not None:
            d["duration_ms"] = row["duration_ms"]
        if row["gate_result"]:
            d["gate_result"] = json.loads(row["gate_result"])
        if row["metadata"]:
            d["metadata"] = json.loads(row["metadata"])
        return d
