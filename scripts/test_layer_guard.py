#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_layer_guard.py — 测试分层守卫

扫描 tests/ 目录，检测分层违规：
1. e2e/visual/replay 语义的测试文件混入 unit/ 目录
2. 重型 I/O（网络、文件、数据库）出现在标为 fast 的测试中
3. 新增测试文件放置在错误层级

用法:
    python scripts/test_layer_guard.py                  # 扫描全部
    python scripts/test_layer_guard.py --staged         # 只检查 staged 文件
    python scripts/test_layer_guard.py --fix            # 自动迁移到正确目录（交互式）

违规级别:
    ERROR   — 必须修复（e2e/visual 在 unit 中，或 fast 测试有重型 I/O）
    WARNING — 建议修复（medium 测试混入 fast 目录）
"""

from __future__ import annotations

import ast
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
TEST_DIR = REPO_ROOT / "tests"

# --- 分层规则 ---

# 文件名模式 → 应该在的目录（或标记）
FILENAME_LAYER_MAP = {
    r"^test_e2e_.*\.py$": "e2e",
    r"^test_visual_.*\.py$": "visual",
    r"^test_replay_.*\.py$": "replay",
    r"^test_godot_.*\.py$": "integration/godot",
}

# 文件内容模式 → 重型特征（不应出现在 fast/unit 中）
HEAVY_IO_PATTERNS = [
    (r"asyncio\.open_connection", "网络 I/O (asyncio TCP)"),
    (r"socket\.", "网络 I/O (socket)"),
    (r"subprocess\.", "子进程调用"),
    (r"requests\.", "HTTP 请求"),
    (r"httpx\.", "HTTP 请求 (httpx)"),
    (r"sqlite3\.connect\([^:]", "SQLite 文件连接（非 :memory:）"),
    (r"Storage\([^:]", "Storage 文件连接（非 :memory:）"),
    (r"\.save\(", "文件保存操作"),
    (r"\.download\(", "文件下载"),
    (r"time\.sleep\(", "显式 sleep（可能表示等待外部资源）"),
]

# 标为 @pytest.mark.fast 但包含重型 I/O 的 AST 模式
FAST_HEAVY_AST_PATTERNS = [
    "async_for",          # async for（可能涉及网络流）
]

# 允许的例外（已知在 tests/ 根目录但合理的历史文件）
UNIT_EXCEPTIONS = {
    "test_architecture_validation.py",  # 架构验证需要完整 Pipeline
    "test_integration.py",              # 本身就是集成测试但命名不同
    "test_e2e_bridge.py",               # 历史遗留，EventBridge E2E
    "test_godot_agents.py",             # 历史遗留，Godot agent 测试
    "test_visual_framework.py",         # visual framework smoke tests（已移到 tests/visual/）
}

FAST_TEST_DIR = TEST_DIR / "unit"       # 约定：fast 测试放在 tests/unit/
E2E_TEST_DIR = TEST_DIR / "e2e"         # 约定：e2e 测试放在 tests/e2e/
VISUAL_TEST_DIR = TEST_DIR / "visual"   # 约定：visual 测试放在 tests/visual/


@dataclass
class Finding:
    file: str
    level: str  # "ERROR" or "WARNING"
    rule: str
    message: str
    suggestion: str = ""


def get_staged_py_files() -> list[str]:
    """获取 staged 的 .py 文件列表"""
    try:
        result = subprocess.run(
            ["git", "diff", "--cached", "--name-only", "--diff-filter=ACMRT"],
            capture_output=True, text=True, cwd=REPO_ROOT
        )
        return [f for f in result.stdout.strip().split("\n") if f.endswith(".py")]
    except Exception:
        return []


def _file_has_marker(filepath: Path, marker: str) -> bool:
    """检查文件是否包含指定 pytest marker"""
    try:
        content = filepath.read_text(encoding="utf-8")
        return f"@pytest.mark.{marker}" in content
    except Exception:
        return False


def scan_layer_placement(findings: list) -> None:
    """检查 tests/ 目录中是否有 e2e/visual/replay 语义的文件在错误位置"""
    for f in TEST_DIR.iterdir():
        if not f.is_file() or not f.name.startswith("test_") or not f.name.endswith(".py"):
            continue
        if f.name in UNIT_EXCEPTIONS:
            continue

        for pattern, target_layer in FILENAME_LAYER_MAP.items():
            if re.match(pattern, f.name):
                findings.append(Finding(
                    file=str(f.relative_to(REPO_ROOT)),
                    level="ERROR",
                    rule="wrong-layer",
                    message=f"文件名匹配 {target_layer} 语义，但位于 tests/ 根目录",
                    suggestion=f"移动到 tests/{target_layer}/ 或重命名文件"
                ))


def scan_fast_tests_for_heavy_io(findings: list) -> None:
    """检查标为 fast 的测试是否包含重型 I/O"""
    fast_dirs = [TEST_DIR]  # 当前项目 fast 测试在 tests/ 根目录
    if FAST_TEST_DIR.exists():
        fast_dirs.append(FAST_TEST_DIR)

    for directory in fast_dirs:
        if not directory.exists():
            continue
        for f in directory.iterdir():
            if not f.is_file() or not f.name.startswith("test_") or not f.name.endswith(".py"):
                continue
            if f.name in UNIT_EXCEPTIONS:
                continue

            # 检查是否有 @pytest.mark.fast 或 @pytest.mark.medium
            is_fast = _file_has_marker(f, "fast")
            is_medium = _file_has_marker(f, "medium")

            try:
                content = f.read_text(encoding="utf-8")
            except Exception:
                continue

            for pattern, description in HEAVY_IO_PATTERNS:
                if re.search(pattern, content):
                    level = "ERROR" if is_fast else ("WARNING" if not is_medium else "INFO")
                    if level == "INFO":
                        continue
                    findings.append(Finding(
                        file=str(f.relative_to(REPO_ROOT)),
                        level=level,
                        rule="heavy-io-in-fast",
                        message=f"{'fast' if is_fast else '未标记'} 测试中有重型 I/O: {description}",
                        suggestion="添加 @pytest.mark.medium 或 @pytest.mark.slow，或将 I/O 移到 fixture"
                    ))
                    break  # 每个文件只报一次


def scan_staged_placement(findings: list) -> None:
    """检查 staged 文件是否放置在正确层级"""
    staged_files = get_staged_py_files()
    for sf in staged_files:
        if "/tests/" not in sf:
            continue

        basename = os.path.basename(sf)
        current_dir = os.path.dirname(sf)

        for pattern, target_layer in FILENAME_LAYER_MAP.items():
            if re.match(pattern, basename):
                expected_dir = f"tests/{target_layer}"
                if current_dir != expected_dir and not current_dir.startswith(expected_dir + "/"):
                    findings.append(Finding(
                        file=sf,
                        level="ERROR",
                        rule="staged-wrong-layer",
                        message=f"新文件应放在 {expected_dir}/",
                        suggestion=f"mv {sf} {expected_dir}/{basename}"
                    ))


def scan_unmarked_tests(findings: list) -> None:
    """检查测试文件是否缺少分层标记"""
    for f in TEST_DIR.iterdir():
        if not f.is_file() or not f.name.startswith("test_") or not f.name.endswith(".py"):
            continue
        if f.name in UNIT_EXCEPTIONS:
            continue

        try:
            content = f.read_text(encoding="utf-8")
        except Exception:
            continue

        # 检查是否有任何 pytest.mark 分层标记
        has_marker = bool(re.search(r"@pytest\.mark\.(fast|medium|slow|godot|visual|replay|llm)", content))
        if not has_marker:
            findings.append(Finding(
                file=str(f.relative_to(REPO_ROOT)),
                level="WARNING",
                rule="missing-layer-marker",
                message="测试文件缺少分层标记（@pytest.mark.fast/medium/slow 等）",
                suggestion="添加合适的标记，如 @pytest.mark.medium"
            ))


def main() -> int:
    staged_only = "--staged" in sys.argv

    findings: list = []

    if staged_only:
        scan_staged_placement(findings)
    else:
        scan_layer_placement(findings)
        scan_fast_tests_for_heavy_io(findings)
        scan_unmarked_tests(findings)

    errors = [f for f in findings if f.level == "ERROR"]
    warnings = [f for f in findings if f.level == "WARNING"]

    if not findings:
        print("✅ 测试分层检查通过 — 无违规")
        return 0

    print("=== 测试分层守卫 ===")
    print()

    if errors:
        print(f"❌ {len(errors)} ERROR(s):")
        for f in errors:
            print(f"  {f.file}")
            print(f"    {f.message}")
            if f.suggestion:
                print(f"    → {f.suggestion}")
            print()

    if warnings:
        print(f"⚠️  {len(warnings)} WARNING(s):")
        for f in warnings:
            print(f"  {f.file}")
            print(f"    {f.message}")
            if f.suggestion:
                print(f"    → {f.suggestion}")
            print()

    if errors:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
