"""CI 脚本验证测试

验证 Godot CI 集成的正确性:
- Workflow YAML 语法
- Makefile 目标
- GameBench 无 Godot 环境运行
"""

from __future__ import annotations

from pathlib import Path

import yaml

import pytest

pytestmark = pytest.mark.fast

# 项目根目录
ROOT = Path(__file__).parent.parent


class TestWorkflowSyntax:
    """验证 CI workflow YAML 语法正确"""

    def _load_workflow(self, name: str) -> dict:
        path = ROOT / ".github" / "workflows" / name
        assert path.exists(), f"Workflow file not found: {path}"
        with open(path) as f:
            return yaml.safe_load(f)

    def _get_triggers(self, wf: dict) -> dict:
        """获取 workflow 触发条件 (YAML 解析 on -> True)"""
        # YAML 解析 "on" 为 True (布尔值)
        return wf.get(True) or wf.get("on") or {}

    def test_godot_ci_yaml_syntax(self):
        """godot-ci.yml 语法正确"""
        wf = self._load_workflow("godot-ci.yml")
        assert "name" in wf
        assert True in wf or "on" in wf  # YAML 解析 on -> True
        assert "jobs" in wf

    def test_godot_ci_triggers(self):
        """godot-ci.yml 包含正确的触发条件"""
        wf = self._load_workflow("godot-ci.yml")
        triggers = self._get_triggers(wf)
        assert "push" in triggers
        assert "pull_request" in triggers
        assert "workflow_dispatch" in triggers

    def test_godot_ci_dispatch_inputs(self):
        """godot-ci.yml 有正确的手动触发参数"""
        wf = self._load_workflow("godot-ci.yml")
        inputs = self._get_triggers(wf)["workflow_dispatch"]["inputs"]
        assert "godot_version" in inputs
        assert "project_path" in inputs
        assert "project_name" in inputs

    def test_godot_ci_jobs(self):
        """godot-ci.yml 包含 game-bench job"""
        wf = self._load_workflow("godot-ci.yml")
        assert "game-bench" in wf["jobs"]

    def test_godot_ci_steps(self):
        """godot-ci.yml game-bench job 有正确的步骤"""
        wf = self._load_workflow("godot-ci.yml")
        steps = wf["jobs"]["game-bench"]["steps"]
        step_names = [s.get("name", "") for s in steps]
        assert any("Install Python" in n for n in step_names)
        assert any("Install Godot" in n for n in step_names)
        assert any("Run GameBench" in n for n in step_names)
        assert any("Upload" in n for n in step_names)

    def test_godot_e2e_yaml_syntax(self):
        """godot-e2e.yml 语法正确"""
        wf = self._load_workflow("godot-e2e.yml")
        assert "name" in wf
        assert True in wf or "on" in wf
        assert "jobs" in wf

    def test_godot_e2e_triggers(self):
        """godot-e2e.yml 仅有手动触发"""
        wf = self._load_workflow("godot-e2e.yml")
        triggers = self._get_triggers(wf)
        assert "workflow_dispatch" in triggers
        # E2E 不自动触发
        assert "push" not in triggers
        assert "pull_request" not in triggers

    def test_godot_e2e_dispatch_inputs(self):
        """godot-e2e.yml 有正确的手动触发参数"""
        wf = self._load_workflow("godot-e2e.yml")
        inputs = self._get_triggers(wf)["workflow_dispatch"]["inputs"]
        assert "project_name" in inputs
        assert "godot_host" in inputs
        assert "godot_port" in inputs

    def test_godot_e2e_steps(self):
        """godot-e2e.yml 包含 E2E 测试步骤"""
        wf = self._load_workflow("godot-e2e.yml")
        steps = wf["jobs"]["e2e-test"]["steps"]
        step_names = [s.get("name", "") for s in steps]
        assert any("Install dependencies" in n for n in step_names)
        assert any("Start Gateway" in n for n in step_names)
        assert any("EventBridge E2E" in n for n in step_names)
        assert any("quality gate" in n.lower() for n in step_names)


class TestMakefileTargets:
    """验证 Makefile 目标存在"""

    def _get_makefile_targets(self) -> list[str]:
        makefile = ROOT / "Makefile"
        assert makefile.exists(), "Makefile not found"
        targets = []
        with open(makefile) as f:
            for line in f:
                # 匹配 "target:  ## comment" 或 "target:" 格式
                if line and not line.startswith(("\t", "#", " ", ".")) and ":" in line:
                    target = line.split(":")[0].strip()
                    if target and not target.startswith("$"):
                        targets.append(target)
        return targets

    def test_godot_bench_target(self):
        """Makefile 包含 godot-bench 目标"""
        targets = self._get_makefile_targets()
        assert "godot-bench" in targets, f"godot-bench not in Makefile targets: {targets}"

    def test_godot_e2e_target(self):
        """Makefile 包含 godot-e2e 目标"""
        targets = self._get_makefile_targets()
        assert "godot-e2e" in targets, f"godot-e2e not in Makefile targets: {targets}"

    def test_existing_targets_preserved(self):
        """原有目标未被破坏"""
        targets = self._get_makefile_targets()
        required = ["help", "install", "gateway", "test", "lint", "clean", "bench-pogong", "bench-loop"]
        for t in required:
            assert t in targets, f"Missing existing target: {t}"

    def test_makefile_syntax(self):
        """Makefile 语法检查 (make -n dry run)"""
        # 只测试 phony 声明和基本结构，不实际执行
        makefile = ROOT / "Makefile"
        content = makefile.read_text()
        # godot-bench 和 godot-e2e 应在 .PHONY 中
        assert "godot-bench" in content
        assert "godot-e2e" in content


class TestGameBenchHeadless:
    """验证 GameBench 可在无 Godot 环境下运行"""

    def test_gamebench_import(self):
        """GameBench 可正常导入"""
        from drivers.godot.bench import GameBench

        assert GameBench is not None

    def test_gamebench_evaluate_no_godot(self, tmp_path):
        """GameBench 在无 Godot 环境下可运行 (headless=False)"""
        from drivers.godot.bench import GameBench

        # 创建最小 project.godot
        project_dir = tmp_path / "test_project"
        project_dir.mkdir()
        (project_dir / "project.godot").write_text(
            "; Generated by Godot engine editor\n"
            '[gd_resource type="ProjectSettings"]\n\n'
            "config_version=5\n\n"
            "[application]\n\n"
            'config/name="Test Game"\n'
            'run/main_scene="res://main.tscn"\n\n'
            "[autoload]\n\n"
            'GameManager="*res://scripts/game_manager.gd"\n'
        )
        # 创建引用的文件
        (project_dir / "main.tscn").write_text("[gd_scene]\n")
        scripts = project_dir / "scripts"
        scripts.mkdir()
        (scripts / "game_manager.gd").write_text("extends Node\n")

        bench = GameBench(
            project_path=str(project_dir),
            project_name="test_game",
            godot_path="godot",  # 不存在也没关系
        )

        # headless=False 跳过 Godot 二进制检查
        result = bench.evaluate(run_headless=False)

        assert result.project_name == "test_game"
        assert result.total_score > 0
        assert result.build_health is not None
        assert result.intent_alignment is not None

    def test_gamebench_build_health_checks(self, tmp_path):
        """GameBench Build Health 检查项正确"""
        from drivers.godot.bench import GameBench

        # 空项目 —— 应该得分很低
        project_dir = tmp_path / "empty_project"
        project_dir.mkdir()
        (project_dir / "project.godot").write_text("config_version=5\n")

        bench = GameBench(project_path=str(project_dir))
        result = bench.evaluate(run_headless=False)

        bh = result.build_health
        assert bh is not None
        # project.godot 存在应通过
        assert any(c["name"] == "project.godot_exists" and c["passed"] for c in bh.checks)

    def test_gamebench_result_serialization(self, tmp_path):
        """BenchResult 可序列化为 JSON"""
        import json

        from drivers.godot.bench import GameBench

        project_dir = tmp_path / "test_project"
        project_dir.mkdir()
        (project_dir / "project.godot").write_text("config_version=5\n")

        bench = GameBench(project_path=str(project_dir))
        result = bench.evaluate(run_headless=False)

        # 应可序列化
        d = result.to_dict()
        json_str = json.dumps(d, ensure_ascii=False)
        assert len(json_str) > 0

        # 反序列化后结构完整
        loaded = json.loads(json_str)
        assert "project_name" in loaded
        assert "total_score" in loaded
        assert "dimensions" in loaded


class TestEventBridgeImport:
    """验证 EventBridge 可导入和基本结构正确"""

    def test_event_bridge_import(self):
        """EventBridge 可正常导入"""
        from drivers.godot.event_bridge import EventBridge

        assert EventBridge is not None

    def test_event_bridge_init(self):
        """EventBridge 可正常初始化"""
        from drivers.godot.event_bridge import EventBridge

        class MockDriver:
            pass

        bridge = EventBridge(
            driver=MockDriver(),
            gateway_url="http://localhost:8900",
            project="test",
        )
        assert bridge.project == "test"
        assert bridge.is_active is False
        assert bridge.session_id is None
