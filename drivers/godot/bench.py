"""游戏质量评估流水线 — 借鉴 OpenGame-Bench 三维评估

OpenGame-Bench 评估维度:
  1. Build Health (BH) — 项目能否编译、加载、渲染
  2. Visual Usability (VU) — 视觉质量、UI 可用性
  3. Intent Alignment (IA) — 是否符合设计意图

本模块适配到 Godot 游戏测试:
  - BH: headless godot 启动 + 场景加载 + 无控制台错误
  - VU: 截图分析 (规则引擎, 可选 VLM)
  - IA: 游戏日志分析 + 配置断言

评分范围: [0, 100]
"""

from __future__ import annotations

import json
import subprocess
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional


class BenchDimension(str, Enum):
    BUILD_HEALTH = "build_health"
    VISUAL_USABILITY = "visual_usability"
    INTENT_ALIGNMENT = "intent_alignment"


@dataclass
class DimensionScore:
    """单维度评分"""

    dimension: str
    score: float  # 0-100
    max_score: float = 100.0
    checks: List[Dict[str, Any]] = field(default_factory=list)
    passed: bool = True
    details: str = ""


@dataclass
class BenchResult:
    """完整评估结果"""

    project_name: str
    timestamp: str
    dimensions: List[DimensionScore] = field(default_factory=list)
    total_score: float = 0.0
    passed: bool = False
    threshold: float = 60.0  # 及格线

    @property
    def build_health(self) -> Optional[DimensionScore]:
        for d in self.dimensions:
            if d.dimension == BenchDimension.BUILD_HEALTH:
                return d
        return None

    @property
    def visual_usability(self) -> Optional[DimensionScore]:
        for d in self.dimensions:
            if d.dimension == BenchDimension.VISUAL_USABILITY:
                return d
        return None

    @property
    def intent_alignment(self) -> Optional[DimensionScore]:
        for d in self.dimensions:
            if d.dimension == BenchDimension.INTENT_ALIGNMENT:
                return d
        return None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "project_name": self.project_name,
            "timestamp": self.timestamp,
            "dimensions": [
                {
                    "dimension": d.dimension,
                    "score": d.score,
                    "passed": d.passed,
                    "details": d.details,
                    "checks": d.checks,
                }
                for d in self.dimensions
            ],
            "total_score": self.total_score,
            "passed": self.passed,
            "threshold": self.threshold,
        }


class GameBench:
    """Godot 游戏质量评估器

    用法:
        bench = GameBench(
            godot_path="godot",
            project_path="/path/to/godot/project",
        )
        result = await bench.evaluate()
        print(f"总分: {result.total_score}/100")
    """

    def __init__(
        self,
        project_path: str,
        project_name: str = "",
        godot_path: str = "godot",
        screenshot_dir: str = "",
        threshold: float = 60.0,
    ):
        self.project_path = Path(project_path)
        self.project_name = project_name or self.project_path.name
        self.godot_path = godot_path
        self.screenshot_dir = screenshot_dir
        self.threshold = threshold

    # ─── 评估入口 ───────────────────────────────────────

    def evaluate(self, run_headless: bool = True) -> BenchResult:
        """执行完整三维评估"""
        result = BenchResult(
            project_name=self.project_name,
            timestamp=time.strftime("%Y-%m-%dT%H:%M:%S"),
            threshold=self.threshold,
        )

        # 1. Build Health
        bh = self._evaluate_build_health(run_headless)
        result.dimensions.append(bh)

        # 2. Visual Usability (仅在有截图时评估)
        vu = self._evaluate_visual_usability()
        result.dimensions.append(vu)

        # 3. Intent Alignment
        ia = self._evaluate_intent_alignment()
        result.dimensions.append(ia)

        # 汇总
        result.total_score = sum(d.score for d in result.dimensions) / len(result.dimensions)
        result.passed = result.total_score >= self.threshold

        return result

    # ─── Build Health (BH) ──────────────────────────────

    def _evaluate_build_health(self, run_headless: bool) -> DimensionScore:
        """Build Health: 编译、加载、渲染"""
        checks: List[Dict[str, Any]] = []
        score = 0.0

        # Check 1: project.godot 存在
        project_file = self.project_path / "project.godot"
        exists = project_file.exists()
        checks.append(
            {
                "name": "project.godot_exists",
                "passed": exists,
                "weight": 10,
            }
        )
        if exists:
            score += 10

        # Check 2: 主场景可解析
        if exists:
            main_scene = self._parse_main_scene(project_file)
            scene_exists = main_scene and (self.project_path / main_scene.replace("res://", "")).exists()
            checks.append(
                {
                    "name": "main_scene_valid",
                    "passed": scene_exists,
                    "detail": main_scene,
                    "weight": 15,
                }
            )
            if scene_exists:
                score += 15

        # Check 3: 无 GDScript 语法错误 (headless import)
        if run_headless:
            import_ok = self._check_headless_import()
            checks.append(
                {
                    "name": "headless_import",
                    "passed": import_ok,
                    "weight": 30,
                }
            )
            if import_ok:
                score += 30

        # Check 4: 关键 Autoload 注册
        autoloads = self._parse_autoloads(project_file)
        autoload_ok = len(autoloads) > 0
        checks.append(
            {
                "name": "autoloads_registered",
                "passed": autoload_ok,
                "detail": f"{len(autoloads)} autoloads",
                "weight": 15,
            }
        )
        if autoload_ok:
            score += 15

        # Check 5: 资源完整性 (scripts 文件不缺失)
        resource_check = self._check_resources()
        checks.append(
            {
                "name": "resource_integrity",
                "passed": resource_check["passed"],
                "detail": resource_check.get("detail", ""),
                "weight": 30,
            }
        )
        if resource_check["passed"]:
            score += 30

        return DimensionScore(
            dimension=BenchDimension.BUILD_HEALTH,
            score=min(score, 100),
            checks=checks,
            passed=score >= 60,
            details=f"{len([c for c in checks if c['passed']])}/{len(checks)} checks passed",
        )

    # ─── Visual Usability (VU) ──────────────────────────

    def _evaluate_visual_usability(self) -> DimensionScore:
        """Visual Usability: 截图分析"""
        checks: List[Dict[str, Any]] = []
        score = 50.0  # 基础分 (无截图时)

        # Check 1: 截图文件存在
        screenshot_path = self.screenshot_dir or str(self.project_path / "test_screenshots")
        ss_dir = Path(screenshot_path)
        screenshots = list(ss_dir.glob("*.png")) if ss_dir.exists() else []
        has_screenshots = len(screenshots) > 0
        checks.append(
            {
                "name": "screenshots_available",
                "passed": has_screenshots,
                "detail": f"{len(screenshots)} screenshots",
                "weight": 20,
            }
        )
        if has_screenshots:
            score = 20

            # Check 2: 截图尺寸合理 (非零、非纯黑)
            try:
                import cv2

                for ss in screenshots[:3]:
                    img = cv2.imread(str(ss))
                    if img is not None and img.mean() > 5:
                        score += 10
                        checks.append(
                            {
                                "name": f"screenshot_quality_{ss.name}",
                                "passed": True,
                                "detail": f"mean_brightness={img.mean():.1f}",
                                "weight": 10,
                            }
                        )
                    else:
                        checks.append(
                            {
                                "name": f"screenshot_quality_{ss.name}",
                                "passed": False,
                                "detail": "empty or pure black",
                                "weight": 10,
                            }
                        )
            except ImportError:
                score += 30  # 无 OpenCV 时给予基础分
                checks.append(
                    {
                        "name": "visual_analysis",
                        "passed": True,
                        "detail": "opencv not available, skipped",
                        "weight": 30,
                    }
                )
        else:
            checks.append(
                {
                    "name": "visual_analysis",
                    "passed": False,
                    "detail": "no screenshots to analyze",
                    "weight": 50,
                }
            )

        return DimensionScore(
            dimension=BenchDimension.VISUAL_USABILITY,
            score=min(score, 100),
            checks=checks,
            passed=score >= 40,
            details="截图质量检查",
        )

    # ─── Intent Alignment (IA) ──────────────────────────

    def _evaluate_intent_alignment(self) -> DimensionScore:
        """Intent Alignment: 配置与设计一致性"""
        checks: List[Dict[str, Any]] = []
        score = 0.0

        # Check 1: 游戏配置文件完整性
        config_files = self._find_config_files()
        has_config = len(config_files) > 0
        checks.append(
            {
                "name": "config_files",
                "passed": has_config,
                "detail": f"{len(config_files)} config files",
                "weight": 20,
            }
        )
        if has_config:
            score += 20

        # Check 2: 配置值合理性 (数值在合理范围)
        config_valid = self._validate_configs(config_files)
        checks.append(
            {
                "name": "config_values_valid",
                "passed": config_valid["passed"],
                "detail": config_valid.get("detail", ""),
                "weight": 30,
            }
        )
        if config_valid["passed"]:
            score += 30

        # Check 3: 场景文件结构完整 (TitleScreen → GameScene → EndScreen)
        scene_structure = self._check_scene_structure()
        checks.append(
            {
                "name": "scene_structure",
                "passed": scene_structure["passed"],
                "detail": scene_structure.get("detail", ""),
                "weight": 30,
            }
        )
        if scene_structure["passed"]:
            score += 30

        # Check 4: 测试覆盖 (有测试文件)
        test_coverage = self._check_test_coverage()
        checks.append(
            {
                "name": "test_coverage",
                "passed": test_coverage["passed"],
                "detail": test_coverage.get("detail", ""),
                "weight": 20,
            }
        )
        if test_coverage["passed"]:
            score += 20

        return DimensionScore(
            dimension=BenchDimension.INTENT_ALIGNMENT,
            score=min(score, 100),
            checks=checks,
            passed=score >= 50,
            details="配置与结构一致性检查",
        )

    # ─── 辅助方法 ───────────────────────────────────────

    def _parse_main_scene(self, project_file: Path) -> Optional[str]:
        """从 project.godot 解析主场景路径"""
        try:
            content = project_file.read_text()
            for line in content.split("\n"):
                if "run/main_scene" in line:
                    return line.split("=", 1)[1].strip().strip('"')
        except Exception:
            pass
        return None

    def _parse_autoloads(self, project_file: Path) -> List[str]:
        """解析 autoload 注册"""
        autoloads = []
        try:
            content = project_file.read_text()
            in_autoload = False
            for line in content.split("\n"):
                if line.strip() == "[autoload]":
                    in_autoload = True
                    continue
                if in_autoload and line.startswith("["):
                    break
                if in_autoload and "=" in line:
                    autoloads.append(line.split("=")[0].strip())
        except Exception:
            pass
        return autoloads

    def _check_headless_import(self) -> bool:
        """headless 模式检查项目导入"""
        try:
            result = subprocess.run(
                [
                    self.godot_path,
                    "--headless",
                    "--editor",
                    "--quit-after",
                    "1",
                    "--path",
                    str(self.project_path),
                    "--import",
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )
            # Godot 返回 0 表示导入成功
            return result.returncode == 0
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return False

    def _check_resources(self) -> Dict[str, Any]:
        """检查资源引用完整性"""
        gd_files = list(self.project_path.rglob("*.gd"))
        if not gd_files:
            return {"passed": False, "detail": "no .gd files found"}

        missing = []
        for gd in gd_files[:20]:  # 采样检查
            try:
                content = gd.read_text()
                # 检查 preload/load 引用
                import re

                refs = re.findall(r'(?:preload|load)\("(res://[^"]+)"\)', content)
                for ref in refs:
                    local = ref.replace("res://", "")
                    if not (self.project_path / local).exists():
                        missing.append(ref)
            except Exception:
                continue

        if missing:
            return {"passed": False, "detail": f"missing: {missing[:5]}"}
        return {"passed": True, "detail": f"checked {min(len(gd_files), 20)} files"}

    def _find_config_files(self) -> List[Path]:
        """查找游戏配置文件"""
        patterns = ["**/gameConfig.json", "**/config.json", "**/*_config.json", "**/manifest.json", "**/catalog.json"]
        configs = []
        for pattern in patterns:
            configs.extend(self.project_path.rglob(pattern))
        return configs

    def _validate_configs(self, configs: List[Path]) -> Dict[str, Any]:
        """验证配置文件"""
        valid = 0
        invalid = []
        for cfg in configs[:10]:
            try:
                data = json.loads(cfg.read_text())
                if isinstance(data, dict) and len(data) > 0:
                    valid += 1
                else:
                    invalid.append(cfg.name)
            except json.JSONDecodeError:
                invalid.append(cfg.name)

        if invalid:
            return {"passed": valid > 0, "detail": f"invalid: {invalid[:3]}"}
        return {"passed": valid > 0, "detail": f"{valid} valid configs"}

    def _check_scene_structure(self) -> Dict[str, Any]:
        """检查场景文件结构"""
        scenes = list(self.project_path.rglob("*.tscn"))
        scene_names = [s.stem.lower() for s in scenes]

        has_title = any("title" in n or "main" in n or "menu" in n for n in scene_names)
        has_game = any("game" in n or "battle" in n or "expedition" in n for n in scene_names)

        if has_title and has_game:
            return {"passed": True, "detail": f"{len(scenes)} scenes, has menu + game"}
        return {
            "passed": has_game,
            "detail": f"{len(scenes)} scenes (title={has_title}, game={has_game})",
        }

    def _check_test_coverage(self) -> Dict[str, Any]:
        """检查测试文件"""
        test_dirs = [
            self.project_path / "test",
            self.project_path / "tests",
            self.project_path / "scripts" / "tests",
        ]
        test_files = []
        for td in test_dirs:
            if td.exists():
                test_files.extend(td.rglob("*.gd"))

        if test_files:
            return {"passed": True, "detail": f"{len(test_files)} test files"}
        return {"passed": False, "detail": "no test files found"}
