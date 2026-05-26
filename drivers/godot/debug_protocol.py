"""活体调试协议 — 借鉴 OpenGame Debug Skill 的 DebugProtocol P

OpenGame 核心思路:
  1. DebugEntry = (FailureSignature, rootCause, verifiedFix)
  2. 每次调试循环: Run → Diagnose → Repair → Verify → Record
  3. ProtocolRule = 从重复 DebugEntry 泛化的可复用规则
  4. Evolve = 每次会话后进化协议

本模块适配到 Godot 游戏测试场景:
  - 从 test_fail 事件 + 截图中提取 FailureSignature
  - 维护跨会话的 DebugProtocol JSON
  - 支持主动式 (proactive) 预验证和反应式 (reactive) 诊断
"""

from __future__ import annotations

import json
import re
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# ─── 协议存储路径 ──────────────────────────────────────────

PROTOCOL_DIR = Path(__file__).parent.parent.parent / "debug_protocols"


def protocol_dir() -> Path:
    """获取协议存储目录"""
    PROTOCOL_DIR.mkdir(parents=True, exist_ok=True)
    return PROTOCOL_DIR


# ─── 数据结构 (映射 OpenGame Debug Skill types.ts) ─────────


@dataclass
class FailureSignature:
    """错误指纹 — 快速匹配

    映射 OpenGame:
      stage ↔ FailureStage (build | test | runtime)
      error_code ↔ errorCode (e.g. "TS2339", "GDSCRIPT_ERROR")
      message_pattern ↔ messagePattern (正则化的错误消息)
      file_context ↔ fileContext (可选, 缩小匹配范围)
    """

    stage: str  # "build" | "test" | "runtime"
    error_code: str  # e.g. "GDSCRIPT_ERROR", "ASSERT_FAIL", "SCENE_CRASH"
    message_pattern: str  # 正则模式，如 "Property '(.+)' does not exist"
    file_context: Optional[str] = None  # e.g. "scripts/battle/*.gd"

    def matches(self, error_code: str, message: str, stage: str = "", file_path: str = "") -> bool:
        """检查是否匹配给定错误"""
        if self.error_code != error_code:
            return False
        if stage and self.stage != stage:
            return False
        if not re.search(self.message_pattern, message):
            return False
        if self.file_context and file_path:
            # 简单 glob 匹配
            pattern = self.file_context.replace("*", ".*")
            if not re.search(pattern, file_path):
                return False
        return True

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "stage": self.stage,
            "error_code": self.error_code,
            "message_pattern": self.message_pattern,
        }
        if self.file_context:
            d["file_context"] = self.file_context
        return d

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "FailureSignature":
        return cls(
            stage=d["stage"],
            error_code=d["error_code"],
            message_pattern=d["message_pattern"],
            file_context=d.get("file_context"),
        )


@dataclass
class DebugEntry:
    """调试条目 — 协议的原子单位

    映射 OpenGame DebugEntry:
      id ↔ 唯一标识
      kind ↔ reactive (失败后匹配) | proactive (预验证)
      signature ↔ FailureSignature
      root_cause ↔ 根因分析
      fix ↔ 验证过的修复
      occurrences ↔ 匹配次数
    """

    id: str
    kind: str  # "reactive" | "proactive"
    signature: FailureSignature
    root_cause: str
    tags: List[str] = field(default_factory=list)
    fix_type: str = "edit"  # "edit" | "shell" | "config" | "delete" | "create"
    fix_description: str = ""
    fix_patch: str = ""
    occurrences: int = 0
    contributing_projects: List[str] = field(default_factory=list)
    created_at: str = ""
    last_matched_at: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "signature": self.signature.to_dict(),
            "root_cause": self.root_cause,
            "tags": self.tags,
            "fix_type": self.fix_type,
            "fix_description": self.fix_description,
            "fix_patch": self.fix_patch,
            "occurrences": self.occurrences,
            "contributing_projects": self.contributing_projects,
            "created_at": self.created_at,
            "last_matched_at": self.last_matched_at,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "DebugEntry":
        return cls(
            id=d["id"],
            kind=d["kind"],
            signature=FailureSignature.from_dict(d["signature"]),
            root_cause=d["root_cause"],
            tags=d.get("tags", []),
            fix_type=d.get("fix_type", "edit"),
            fix_description=d.get("fix_description", ""),
            fix_patch=d.get("fix_patch", ""),
            occurrences=d.get("occurrences", 0),
            contributing_projects=d.get("contributing_projects", []),
            created_at=d.get("created_at", ""),
            last_matched_at=d.get("last_matched_at", ""),
        )


@dataclass
class ProtocolRule:
    """从重复条目泛化的可复用规则

    映射 OpenGame ProtocolRule:
      name ↔ 规则名
      preconditions ↔ 适用条件
      action ↔ 触发动作 (flag | fix | block)
      checks ↔ 验证检查列表
    """

    id: str
    name: str
    description: str
    preconditions: List[str] = field(default_factory=list)
    action: str = "flag"  # "flag" | "fix" | "block"
    checks: List[Dict[str, Any]] = field(default_factory=list)
    derived_from: List[str] = field(default_factory=list)
    prevention_count: int = 0
    created_at: str = ""
    updated_at: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "preconditions": self.preconditions,
            "action": self.action,
            "checks": self.checks,
            "derived_from": self.derived_from,
            "prevention_count": self.prevention_count,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "ProtocolRule":
        return cls(
            id=d["id"],
            name=d["name"],
            description=d["description"],
            preconditions=d.get("preconditions", []),
            action=d.get("action", "flag"),
            checks=d.get("checks", []),
            derived_from=d.get("derived_from", []),
            prevention_count=d.get("prevention_count", 0),
            created_at=d.get("created_at", ""),
            updated_at=d.get("updated_at", ""),
        )


@dataclass
class DebugIteration:
    """单次调试循环迭代"""

    iteration: int
    timestamp: str
    stage: str
    passed: bool
    raw_error: str = ""
    matched_entry_id: Optional[str] = None
    new_entry_id: Optional[str] = None
    repair_action: str = ""
    duration_ms: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "iteration": self.iteration,
            "timestamp": self.timestamp,
            "stage": self.stage,
            "passed": self.passed,
            "raw_error": self.raw_error,
            "matched_entry_id": self.matched_entry_id,
            "new_entry_id": self.new_entry_id,
            "repair_action": self.repair_action,
            "duration_ms": self.duration_ms,
        }


@dataclass
class DebugTrace:
    """完整调试会话日志"""

    project_path: str
    started_at: str
    completed_at: str = ""
    success: bool = False
    total_iterations: int = 0
    max_iterations: int = 10
    iterations: List[DebugIteration] = field(default_factory=list)
    new_entries: List[str] = field(default_factory=list)
    matched_entries: List[str] = field(default_factory=list)
    total_duration_ms: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "project_path": self.project_path,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "success": self.success,
            "total_iterations": self.total_iterations,
            "max_iterations": self.max_iterations,
            "iterations": [it.to_dict() for it in self.iterations],
            "new_entries": self.new_entries,
            "matched_entries": self.matched_entries,
            "total_duration_ms": self.total_duration_ms,
        }


# ─── 调试协议 — 核心管理类 ────────────────────────────────


class DebugProtocol:
    """活体调试协议 P

    借鉴 OpenGame Algorithm 1:
      REPEAT
        Run → 检查 → Diagnose → Repair → Record
      UNTIL 可构建且可运行
      Evolve P

    用法:
        protocol = DebugProtocol.load_or_create("pogongshichongzou")

        # 查找匹配的修复
        entry = protocol.find_match("GDSCRIPT_ERROR", error_msg, "test")
        if entry:
            print(f"已知修复: {entry.fix_description}")

        # 记录新发现
        protocol.record_entry(DebugEntry(...))

        # 保存
        protocol.save()
    """

    def __init__(self, project_name: str):
        self.project_name = project_name
        self.version: int = 1
        self.entries: List[DebugEntry] = []
        self.rules: List[ProtocolRule] = []
        self.created_at = time.strftime("%Y-%m-%dT%H:%M:%S")
        self.updated_at = self.created_at

    # ─── 查找匹配 ───────────────────────────────────────

    def find_match(self, error_code: str, message: str, stage: str = "", file_path: str = "") -> Optional[DebugEntry]:
        """在协议中查找匹配的修复条目 (反应式)"""
        for entry in self.entries:
            if entry.kind != "reactive":
                continue
            if entry.signature.matches(error_code, message, stage, file_path):
                entry.occurrences += 1
                entry.last_matched_at = time.strftime("%Y-%m-%dT%H:%M:%S")
                if self.project_name not in entry.contributing_projects:
                    entry.contributing_projects.append(self.project_name)
                return entry
        return None

    def check_proactive(self, context: Dict[str, Any]) -> List[DebugEntry]:
        """主动式预验证 (在运行前检查已知问题模式)"""
        violations = []
        for entry in self.entries:
            if entry.kind != "proactive":
                continue
            context.get("file_path", "")
            for check in entry.tags:
                if check in str(context):
                    violations.append(entry)
        return violations

    # ─── 记录条目 ────────────────────────────────────────

    def record_entry(self, entry: DebugEntry) -> None:
        """记录新的调试条目"""
        self.entries.append(entry)
        self.updated_at = time.strftime("%Y-%m-%dT%H:%M:%S")
        self._try_generalize()

    def create_entry(
        self,
        stage: str,
        error_code: str,
        message: str,
        root_cause: str,
        fix_description: str,
        fix_patch: str = "",
        fix_type: str = "edit",
        tags: List[str] = None,
        file_context: Optional[str] = None,
    ) -> DebugEntry:
        """创建并记录新的调试条目"""
        # 正则化消息模式
        msg_pattern = self._normalize_message(message)

        entry = DebugEntry(
            id=f"entry-{error_code}-{uuid.uuid4().hex[:8]}",
            kind="reactive",
            signature=FailureSignature(
                stage=stage,
                error_code=error_code,
                message_pattern=msg_pattern,
                file_context=file_context,
            ),
            root_cause=root_cause,
            tags=tags or [],
            fix_type=fix_type,
            fix_description=fix_description,
            fix_patch=fix_patch,
            contributing_projects=[self.project_name],
            created_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
            last_matched_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
        )
        self.record_entry(entry)
        return entry

    # ─── 规则泛化 ───────────────────────────────────────

    def _try_generalize(self) -> None:
        """尝试从重复条目泛化为规则 (借鉴 OpenGame Generalizer)"""
        # 按 error_code 分组
        code_groups: Dict[str, List[DebugEntry]] = {}
        for entry in self.entries:
            code_groups.setdefault(entry.signature.error_code, []).append(entry)

        for code, group in code_groups.items():
            if len(group) >= 3:  # 同类错误出现 3 次以上
                # 检查是否已有对应规则
                existing = any(r.name == f"{code}_auto_rule" for r in self.rules)
                if not existing:
                    rule = ProtocolRule(
                        id=f"rule-{code}-{uuid.uuid4().hex[:8]}",
                        name=f"{code}_auto_rule",
                        description=f"自动泛化规则: {code} 已出现 {len(group)} 次",
                        preconditions=[f"error_code == {code}"],
                        action="flag",
                        checks=[
                            {
                                "target": "file",
                                "query": code,
                                "violation_message": f"检测到已知问题模式: {code}",
                            }
                        ],
                        derived_from=[e.id for e in group],
                        created_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
                        updated_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
                    )
                    self.rules.append(rule)

    # ─── 持久化 ─────────────────────────────────────────

    def save(self) -> Path:
        """保存协议到 JSON"""
        pdir = protocol_dir()
        filepath = pdir / f"{self.project_name}_protocol.json"
        data = {
            "project_name": self.project_name,
            "version": self.version,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "entries": [e.to_dict() for e in self.entries],
            "rules": [r.to_dict() for r in self.rules],
        }
        filepath.write_text(json.dumps(data, indent=2, ensure_ascii=False))
        return filepath

    @classmethod
    def load_or_create(cls, project_name: str) -> "DebugProtocol":
        """加载或创建协议"""
        pdir = protocol_dir()
        filepath = pdir / f"{project_name}_protocol.json"
        if filepath.exists():
            return cls._load(filepath)
        return cls(project_name)

    @classmethod
    def _load(cls, filepath: Path) -> "DebugProtocol":
        data = json.loads(filepath.read_text())
        proto = cls(data["project_name"])
        proto.version = data.get("version", 1)
        proto.created_at = data.get("created_at", "")
        proto.updated_at = data.get("updated_at", "")
        proto.entries = [DebugEntry.from_dict(e) for e in data.get("entries", [])]
        proto.rules = [ProtocolRule.from_dict(r) for r in data.get("rules", [])]
        return proto

    # ─── 统计 ───────────────────────────────────────────

    def stats(self) -> Dict[str, Any]:
        return {
            "project": self.project_name,
            "version": self.version,
            "entries": len(self.entries),
            "rules": len(self.rules),
            "total_occurrences": sum(e.occurrences for e in self.entries),
            "top_codes": self._top_error_codes(5),
        }

    def _top_error_codes(self, n: int) -> List[Tuple[str, int]]:
        code_counts: Dict[str, int] = {}
        for e in self.entries:
            code_counts[e.signature.error_code] = code_counts.get(e.signature.error_code, 0) + e.occurrences + 1
        sorted_codes = sorted(code_counts.items(), key=lambda x: -x[1])
        return sorted_codes[:n]

    # ─── 工具方法 ───────────────────────────────────────

    @staticmethod
    def _normalize_message(message: str) -> str:
        """将具体错误消息正则化为匹配模式"""
        # 替换具体变量名为通配符
        msg = re.sub(r"'[^']+'", "'(.+)'", message)
        msg = re.sub(r'"[^"]+"', '"(.+)"', msg)
        # 替换数字
        msg = re.sub(r"\b\d+\b", r"\\d+", msg)
        # 替换文件路径
        msg = re.sub(r"res://[^\s]+", r"res://[^\\s]+", msg)
        return msg


# ─── 预置的 Godot 常见错误模板 ─────────────────────────────


def create_seed_protocol(project_name: str) -> DebugProtocol:
    """创建包含常见 Godot 错误的种子协议"""
    proto = DebugProtocol(project_name)

    # GDScript 常见错误
    proto.create_entry(
        stage="build",
        error_code="GDSCRIPT_ERROR",
        message="Parse Error: Unexpected token",
        root_cause="GDScript 语法错误，通常是缺少冒号、括号不匹配或缩进问题",
        fix_description="检查语法：缩进一致性、冒号和括号配对",
        tags=["syntax", "gdscript"],
    )

    proto.create_entry(
        stage="runtime",
        error_code="NODE_NOT_FOUND",
        message="Node not found:",
        root_cause="场景树中不存在指定路径的节点",
        fix_description="确认节点路径正确，检查场景文件中节点是否已添加",
        tags=["scene_tree", "node_path"],
    )

    proto.create_entry(
        stage="runtime",
        error_code="INVALID_METHOD",
        message="Invalid call. Nonexistent function",
        root_cause="调用了不存在的方法，可能是拼写错误或 API 变更",
        fix_description="检查方法名拼写，确认对应 Godot 版本 API",
        tags=["api", "method"],
    )

    proto.create_entry(
        stage="runtime",
        error_code="SCENE_LOAD_FAIL",
        message="Failed loading resource:",
        root_cause="场景或资源文件路径错误，或资源未正确导入",
        fix_description="确认资源路径存在且导入设置正确",
        tags=["resource", "scene"],
    )

    proto.create_entry(
        stage="test",
        error_code="ASSERT_FAIL",
        message="Assertion failed",
        root_cause="测试断言失败",
        fix_description="检查断言条件，确认预期值和实际值",
        tags=["test", "assertion"],
    )

    # 破宫特有
    proto.create_entry(
        stage="runtime",
        error_code="RUN_GRAPH_ERROR",
        message="Run graph node not found",
        root_cause="规则书 run graph 中引用了不存在的节点 ID",
        fix_description="检查 manifest JSON 中的 layer 和节点定义",
        tags=["pogongshichongzou", "run_graph", "manifest"],
        file_context="assets/manifests/*.json",
    )

    # Loop Expedition 特有
    proto.create_entry(
        stage="runtime",
        error_code="EXPEDITION_ERROR",
        message="Expedition orchestrator not initialized",
        root_cause="远征协调器未正确初始化就调用了远征操作",
        fix_description="确认 ExpeditionMode.configure() 已调用",
        tags=["loopexpedition", "expedition", "orchestrator"],
    )

    return proto
