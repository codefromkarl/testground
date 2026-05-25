"""SQLite 状态管理 — 分析流水线的可查询索引

受 audit 的 StateDB 启发，但针对测试分析场景简化：
- 不需要漏洞跟踪，改为 findings 跟踪
- 增加跨 session 的历史对比能力
- 支持断点续跑
"""

from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS analysis_runs (
    run_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    started_at REAL NOT NULL,
    finished_at REAL,
    status TEXT NOT NULL DEFAULT 'running',
    config_json TEXT,
    total_cost_tokens INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS recon_outputs (
    run_id TEXT PRIMARY KEY,
    raw_json TEXT NOT NULL,
    FOREIGN KEY (run_id) REFERENCES analysis_runs(run_id)
);

CREATE TABLE IF NOT EXISTS analysis_tasks (
    task_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    agent_type TEXT NOT NULL,
    scope_hint TEXT NOT NULL,
    target_events TEXT NOT NULL,
    priority INTEGER NOT NULL DEFAULT 3,
    status TEXT NOT NULL DEFAULT 'pending',
    source TEXT NOT NULL DEFAULT 'recon',
    seeded_from TEXT,
    raw_json TEXT NOT NULL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    FOREIGN KEY (run_id) REFERENCES analysis_runs(run_id)
);

CREATE TABLE IF NOT EXISTS findings (
    finding_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    category TEXT NOT NULL,
    severity TEXT NOT NULL,
    description TEXT NOT NULL,
    evidence_json TEXT NOT NULL,
    affected_tests TEXT,
    affected_projects TEXT,
    confidence REAL,
    validation_status TEXT,
    validation_json TEXT,
    is_canonical INTEGER DEFAULT 0,
    group_id TEXT,
    raw_json TEXT NOT NULL,
    created_at REAL NOT NULL DEFAULT 0,
    FOREIGN KEY (task_id) REFERENCES analysis_tasks(task_id),
    FOREIGN KEY (run_id) REFERENCES analysis_runs(run_id)
);

CREATE TABLE IF NOT EXISTS feedback_tasks (
    feedback_id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    seed_finding_id TEXT NOT NULL,
    new_task_id TEXT NOT NULL,
    pattern_description TEXT,
    created_at REAL NOT NULL,
    FOREIGN KEY (run_id) REFERENCES analysis_runs(run_id)
);

CREATE TABLE IF NOT EXISTS costs (
    cost_id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    stage TEXT NOT NULL,
    ref_id TEXT,
    input_tokens INTEGER,
    output_tokens INTEGER,
    duration_ms INTEGER,
    created_at REAL NOT NULL,
    FOREIGN KEY (run_id) REFERENCES analysis_runs(run_id)
);

CREATE INDEX IF NOT EXISTS idx_tasks_run_status ON analysis_tasks(run_id, status);
CREATE INDEX IF NOT EXISTS idx_findings_run ON findings(run_id);
CREATE INDEX IF NOT EXISTS idx_findings_validation ON findings(validation_status);
CREATE INDEX IF NOT EXISTS idx_costs_run_stage ON costs(run_id, stage);
"""


@dataclass
class AnalysisTask:
    task_id: str
    run_id: str
    agent_type: str
    scope_hint: str
    target_events: List[str]
    priority: int
    status: str
    source: str
    seeded_from: Optional[str]
    raw_json: Dict[str, Any]


@dataclass
class Finding:
    finding_id: str
    task_id: str
    run_id: str
    category: str
    severity: str
    description: str
    evidence_json: Dict[str, Any]
    affected_tests: List[str]
    affected_projects: List[str]
    confidence: Optional[float]
    validation_status: Optional[str]
    validation_json: Optional[Dict[str, Any]]
    is_canonical: bool
    group_id: Optional[str]
    raw_json: Dict[str, Any]


class PipelineState:
    """分析流水线的 SQLite 状态存储。

    职责：
    - 记录每个分析 run 的生命周期
    - 管理 analysis tasks 的状态流转
    - 存储和查询 findings
    - 追踪成本（token 消耗）
    - 支持断点续跑
    """

    def __init__(self, db_path: Path):
        self.path = db_path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path))
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    # ─── Run 生命周期 ──────────────────────────────────────

    def create_run(self, session_id: str, run_id: str, config: Optional[Dict] = None) -> str:
        self._conn.execute(
            "INSERT INTO analysis_runs (run_id, session_id, started_at, status, config_json) VALUES (?, ?, ?, ?, ?)",
            (run_id, session_id, time.time(), "running", json.dumps(config or {})),
        )
        self._conn.commit()
        return run_id

    def finish_run(self, run_id: str, status: str = "completed") -> None:
        self._conn.execute(
            "UPDATE analysis_runs SET status = ?, finished_at = ? WHERE run_id = ?",
            (status, time.time(), run_id),
        )
        self._conn.commit()

    def get_run(self, run_id: str) -> Optional[sqlite3.Row]:
        return self._conn.execute(
            "SELECT * FROM analysis_runs WHERE run_id = ?", (run_id,)
        ).fetchone()

    def get_run_status(self, run_id: str) -> Optional[str]:
        row = self.get_run(run_id)
        return row["status"] if row else None

    # ─── Recon 输出 ────────────────────────────────────────

    def save_recon(self, run_id: str, payload: Dict[str, Any]) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO recon_outputs (run_id, raw_json) VALUES (?, ?)",
            (run_id, json.dumps(payload, ensure_ascii=False)),
        )
        self._conn.commit()

    def get_recon(self, run_id: str) -> Optional[Dict[str, Any]]:
        row = self._conn.execute(
            "SELECT raw_json FROM recon_outputs WHERE run_id = ?", (run_id,)
        ).fetchone()
        return json.loads(row["raw_json"]) if row else None

    # ─── Tasks ─────────────────────────────────────────────

    def add_task(self, run_id: str, task: Dict[str, Any], source: str = "recon") -> None:
        now = time.time()
        self._conn.execute(
            """INSERT OR IGNORE INTO analysis_tasks
            (task_id, run_id, agent_type, scope_hint, target_events,
             priority, status, source, seeded_from, raw_json, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?, ?)""",
            (
                task["task_id"],
                run_id,
                task["agent_type"],
                task["scope_hint"],
                json.dumps(task.get("target_events", [])),
                int(task.get("priority", 3)),
                source,
                task.get("seeded_from"),
                json.dumps(task, ensure_ascii=False),
                now,
                now,
            ),
        )
        self._conn.commit()

    def get_pending_tasks(self, run_id: str) -> List[AnalysisTask]:
        rows = self._conn.execute(
            "SELECT * FROM analysis_tasks WHERE run_id = ? AND status = 'pending' ORDER BY priority, created_at",
            (run_id,),
        ).fetchall()
        return [self._row_to_task(r) for r in rows]

    def update_task_status(self, task_id: str, status: str) -> None:
        self._conn.execute(
            "UPDATE analysis_tasks SET status = ?, updated_at = ? WHERE task_id = ?",
            (status, time.time(), task_id),
        )
        self._conn.commit()

    @staticmethod
    def _row_to_task(r: sqlite3.Row) -> AnalysisTask:
        return AnalysisTask(
            task_id=r["task_id"],
            run_id=r["run_id"],
            agent_type=r["agent_type"],
            scope_hint=r["scope_hint"],
            target_events=json.loads(r["target_events"]),
            priority=r["priority"],
            status=r["status"],
            source=r["source"],
            seeded_from=r["seeded_from"],
            raw_json=json.loads(r["raw_json"]),
        )

    # ─── Findings ──────────────────────────────────────────

    def add_finding(self, run_id: str, task_id: str, finding: Dict[str, Any]) -> None:
        self._conn.execute(
            """INSERT OR IGNORE INTO findings
            (finding_id, task_id, run_id, category, severity, description,
             evidence_json, affected_tests, affected_projects, confidence, raw_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                finding["finding_id"],
                task_id,
                run_id,
                finding["category"],
                finding["severity"],
                finding["description"],
                json.dumps(finding.get("evidence", {}), ensure_ascii=False),
                json.dumps(finding.get("affected_tests", [])),
                json.dumps(finding.get("affected_projects", [])),
                finding.get("confidence"),
                json.dumps(finding, ensure_ascii=False),
                time.time(),
            ),
        )
        self._conn.commit()

    def get_unvalidated_findings(self, run_id: str) -> List[Finding]:
        rows = self._conn.execute(
            "SELECT * FROM findings WHERE run_id = ? AND validation_status IS NULL",
            (run_id,),
        ).fetchall()
        return [self._row_to_finding(r) for r in rows]

    def get_confirmed_findings(self, run_id: str) -> List[Finding]:
        rows = self._conn.execute(
            "SELECT * FROM findings WHERE run_id = ? AND validation_status = 'confirmed'",
            (run_id,),
        ).fetchall()
        return [self._row_to_finding(r) for r in rows]

    def get_all_findings(self, run_id: str) -> List[Finding]:
        rows = self._conn.execute(
            "SELECT * FROM findings WHERE run_id = ? ORDER BY severity, created_at",
            (run_id,),
        ).fetchall()
        return [self._row_to_finding(r) for r in rows]

    def set_validation(self, finding_id: str, status: str, payload: Dict[str, Any]) -> None:
        self._conn.execute(
            "UPDATE findings SET validation_status = ?, validation_json = ? WHERE finding_id = ?",
            (status, json.dumps(payload, ensure_ascii=False), finding_id),
        )
        self._conn.commit()

    @staticmethod
    def _row_to_finding(r: sqlite3.Row) -> Finding:
        return Finding(
            finding_id=r["finding_id"],
            task_id=r["task_id"],
            run_id=r["run_id"],
            category=r["category"],
            severity=r["severity"],
            description=r["description"],
            evidence_json=json.loads(r["evidence_json"]),
            affected_tests=json.loads(r["affected_tests"] or "[]"),
            affected_projects=json.loads(r["affected_projects"] or "[]"),
            confidence=r["confidence"],
            validation_status=r["validation_status"],
            validation_json=json.loads(r["validation_json"]) if r["validation_json"] else None,
            is_canonical=bool(r["is_canonical"]),
            group_id=r["group_id"],
            raw_json=json.loads(r["raw_json"]),
        )

    # ─── Feedback ──────────────────────────────────────────

    def add_feedback_task(self, run_id: str, seed_finding_id: str, new_task_id: str, pattern: str) -> None:
        self._conn.execute(
            "INSERT INTO feedback_tasks (run_id, seed_finding_id, new_task_id, pattern_description, created_at) VALUES (?, ?, ?, ?, ?)",
            (run_id, seed_finding_id, new_task_id, pattern, time.time()),
        )
        self._conn.commit()

    # ─── Costs ─────────────────────────────────────────────

    def record_cost(self, run_id: str, stage: str, ref_id: Optional[str],
                    input_tokens: int = 0, output_tokens: int = 0, duration_ms: int = 0) -> None:
        self._conn.execute(
            "INSERT INTO costs (run_id, stage, ref_id, input_tokens, output_tokens, duration_ms, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (run_id, stage, ref_id, input_tokens, output_tokens, duration_ms, time.time()),
        )
        self._conn.commit()

    def total_tokens(self, run_id: str) -> int:
        row = self._conn.execute(
            "SELECT COALESCE(SUM(input_tokens + output_tokens), 0) AS total FROM costs WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        return int(row["total"]) if row else 0

    def cost_summary(self, run_id: str) -> Dict[str, Any]:
        rows = self._conn.execute(
            "SELECT stage, SUM(input_tokens) as input_tok, SUM(output_tokens) as output_tok, SUM(duration_ms) as dur FROM costs WHERE run_id = ? GROUP BY stage",
            (run_id,),
        ).fetchall()
        return {r["stage"]: {"input_tokens": r["input_tok"], "output_tokens": r["output_tok"], "duration_ms": r["dur"]} for r in rows}

    # ─── 历史对比 ──────────────────────────────────────────

    def get_previous_findings(self, session_id: str, current_run_id: str) -> List[Finding]:
        """获取同一 session 上一次 run 的 confirmed findings，用于趋势对比"""
        rows = self._conn.execute(
            """SELECT f.* FROM findings f
            JOIN analysis_runs r ON f.run_id = r.run_id
            WHERE r.session_id = ? AND f.run_id != ? AND f.validation_status = 'confirmed'
            ORDER BY r.finished_at DESC""",
            (session_id, current_run_id),
        ).fetchall()
        return [self._row_to_finding(r) for r in rows]

    # ─── 生命周期 ──────────────────────────────────────────

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "PipelineState":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()
