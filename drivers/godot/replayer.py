"""游戏操作回放器 — 回放录制的 Godot 操作序列

支持:
- 加载 JSON 录制文件
- 按原速或倍速回放操作
- 可选截图对比验证（使用 VisualAsserter）
- 关键节点验证（不检查每一帧）

用法:
    replayer = GameReplayer()
    replayer.load_recording("recordings/rec-xxx.json")

    async with GodotDriver() as driver:
        result = await replayer.replay(driver, speed=1.0, verify=True)
        print(f"回放结果: {result.passed}")
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from drivers.godot.recorder import RecordedAction


@dataclass
class ReplayStep:
    """单步回放结果"""

    index: int
    action_type: str
    params: Dict[str, Any]
    passed: bool
    duration_ms: int
    screenshot_actual: Optional[str] = None  # 回放时的截图
    screenshot_expected: Optional[str] = None  # 录制时的截图
    error: Optional[str] = None
    # 视觉验证
    visual_match: bool = False
    visual_confidence: float = 0.0


@dataclass
class ReplayResult:
    """回放结果"""

    session_id: str
    total_steps: int
    passed_steps: int
    failed_steps: int
    skipped_steps: int
    duration_ms: int
    steps: List[ReplayStep] = field(default_factory=list)
    error: Optional[str] = None

    @property
    def passed(self) -> bool:
        return self.failed_steps == 0 and self.error is None

    @property
    def pass_rate(self) -> float:
        if self.total_steps == 0:
            return 0.0
        return self.passed_steps / self.total_steps

    @property
    def duration_s(self) -> float:
        return self.duration_ms / 1000.0


@dataclass
class VerifyResult:
    """验证结果"""

    passed: bool
    checks_total: int
    checks_passed: int
    checks_failed: int
    details: List[Dict[str, Any]] = field(default_factory=list)


class GameReplayer:
    """游戏操作回放器

    回放录制的操作序列，可选截图对比验证。

    参数:
        recording_dir: 录制文件目录（用于加载相对路径的截图）
        verify_screenshots: 是否进行截图对比验证
        verify_threshold: 截图匹配阈值（0-1）
        on_step: 每步回调函数 (step: ReplayStep) -> None
    """

    def __init__(
        self,
        recording_dir: Optional[str] = None,
        verify_screenshots: bool = False,
        verify_threshold: float = 0.8,
        on_step: Optional[Callable] = None,
    ):
        self._recording_dir = Path(recording_dir) if recording_dir else None
        self._verify_screenshots = verify_screenshots
        self._verify_threshold = verify_threshold
        self._on_step = on_step

        # 加载的录制数据
        self._session_id: Optional[str] = None
        self._metadata: Dict[str, Any] = {}
        self._actions: List[RecordedAction] = []
        self._json_path: Optional[Path] = None

    @property
    def is_loaded(self) -> bool:
        return len(self._actions) > 0

    @property
    def action_count(self) -> int:
        return len(self._actions)

    @property
    def session_id(self) -> Optional[str]:
        return self._session_id

    def load_recording(self, json_path: str) -> None:
        """加载录制文件

        Args:
            json_path: JSON 录制文件路径

        Raises:
            FileNotFoundError: 文件不存在
            ValueError: JSON 格式错误
        """
        path = Path(json_path)
        if not path.exists():
            raise FileNotFoundError(f"录制文件不存在: {path}")

        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            raise ValueError(f"JSON 解析错误: {e}") from e

        if not isinstance(data, dict) or "actions" not in data:
            raise ValueError("无效的录制格式：缺少 'actions' 字段")

        self._json_path = path
        self._session_id = data.get("session_id", path.stem)
        self._metadata = data.get("metadata", {})
        self._recording_dir = self._recording_dir or path.parent

        # 解析操作列表
        self._actions = []
        for i, action_data in enumerate(data["actions"]):
            action = RecordedAction(
                timestamp=action_data.get("timestamp", 0),
                type=action_data.get("type", "unknown"),
                params=action_data.get("params", {}),
                screenshot=action_data.get("screenshot"),
                index=action_data.get("index", i),
            )
            self._actions.append(action)

        print(f"[GameReplayer] 已加载录制: {self._session_id} ({len(self._actions)} 个操作)")

    async def replay(
        self,
        driver: Any,  # GodotDriver
        speed: float = 1.0,
        verify: bool = True,
        verify_indices: Optional[List[int]] = None,
    ) -> ReplayResult:
        """回放录制的操作序列

        Args:
            driver: GodotDriver 实例
            speed: 回放速度（1.0 = 原速，2.0 = 2 倍速，0 = 无延迟）
            verify: 是否进行截图验证
            verify_indices: 需要验证的步骤索引（None = 验证所有）

        Returns:
            ReplayResult 包含每步结果和总体统计
        """
        if not self.is_loaded:
            raise RuntimeError("未加载录制文件，请先 load_recording()")

        if speed < 0:
            raise ValueError("speed 不能为负数")

        started_at = int(time.time() * 1000)
        steps: List[ReplayStep] = []
        passed_count = 0
        failed_count = 0
        skipped_count = 0

        # 初始化视觉断言器（如果需要验证）
        asserter = None
        if verify and self._verify_screenshots:
            try:
                from drivers.godot.visual import VisualAsserter

                asserter = VisualAsserter()
            except ImportError:
                print("[GameReplayer] 警告: 无法导入 VisualAsserter，跳过截图验证")

        print(f"[GameReplayer] 开始回放: {self._session_id} (speed={speed}x, verify={verify})")

        for i, action in enumerate(self._actions):
            # 计算延迟
            if i > 0 and speed > 0:
                delay_ms = action.timestamp - self._actions[i - 1].timestamp
                if delay_ms > 0:
                    await asyncio.sleep(delay_ms / 1000.0 / speed)

            # 执行操作
            step_started = int(time.time() * 1000)
            step_passed = True
            step_error = None
            actual_screenshot = None
            visual_match = False
            visual_confidence = 0.0

            try:
                await self._execute_action(driver, action)
            except Exception as e:
                step_passed = False
                step_error = str(e)
                print(f"[GameReplayer] ❌ 步骤 #{i} 失败: {e}")

            # 截图验证（仅在需要时）
            should_verify = verify and (
                verify_indices is None or i in verify_indices
            )

            if should_verify and asserter and action.screenshot:
                try:
                    # 截取当前屏幕
                    prefix = f"replay_{self._session_id}_{i:04d}"
                    actual_path = await driver.screenshot(f"{prefix}.png")
                    actual_screenshot = str(actual_path)

                    # 对比截图
                    expected_path = self._recording_dir / action.screenshot
                    if expected_path.exists():
                        from drivers.godot.visual import TemplateMatch

                        template = TemplateMatch(
                            template_path=str(expected_path),
                            threshold=self._verify_threshold,
                            rgb=True,
                        )
                        result = asserter.match_template(actual_path, template)
                        visual_match = result.matched
                        visual_confidence = result.confidence

                        if not visual_match:
                            step_passed = False
                            step_error = step_error or f"截图不匹配: confidence={result.confidence:.3f}"
                except Exception as e:
                    print(f"[GameReplayer] ⚠️ 截图验证失败 (步骤 #{i}): {e}")

            step_duration = int(time.time() * 1000) - step_started

            step = ReplayStep(
                index=i,
                action_type=action.type,
                params=action.params,
                passed=step_passed,
                duration_ms=step_duration,
                screenshot_actual=actual_screenshot,
                screenshot_expected=action.screenshot,
                error=step_error,
                visual_match=visual_match,
                visual_confidence=visual_confidence,
            )
            steps.append(step)

            if step_passed:
                passed_count += 1
                print(f"[GameReplayer] ✅ 步骤 #{i}: {action.type}")
            else:
                failed_count += 1
                print(f"[GameReplayer] ❌ 步骤 #{i}: {action.type} — {step_error}")

            # 回调
            if self._on_step:
                self._on_step(step)

        total_duration = int(time.time() * 1000) - started_at

        result = ReplayResult(
            session_id=self._session_id or "",
            total_steps=len(self._actions),
            passed_steps=passed_count,
            failed_steps=failed_count,
            skipped_steps=skipped_count,
            duration_ms=total_duration,
            steps=steps,
        )

        print(f"[GameReplayer] 回放完成: {passed_count}/{len(self._actions)} 通过, 耗时 {total_duration / 1000:.1f}s")

        return result

    async def verify_recording(
        self,
        driver: Any,  # GodotDriver
        recording: Optional[Dict[str, Any]] = None,
        check_scene: bool = True,
        check_screenshots: bool = True,
        screenshot_threshold: float = 0.8,
    ) -> VerifyResult:
        """验证当前游戏状态与录制一致

        不执行操作，仅检查当前状态是否匹配录制的预期状态。
        用于验证回放后的游戏状态。

        Args:
            driver: GodotDriver 实例
            recording: 录制数据（默认使用已加载的）
            check_scene: 是否检查场景路径
            check_screenshots: 是否进行截图对比
            screenshot_threshold: 截图匹配阈值

        Returns:
            VerifyResult
        """
        if not self.is_loaded:
            raise RuntimeError("未加载录制文件")

        checks: List[Dict[str, Any]] = []

        # 检查场景
        if check_scene and "start_scene" in self._metadata:
            expected_scene = self._metadata["start_scene"]
            try:
                actual_scene = await driver.get_scene()
                passed = actual_scene == expected_scene
                checks.append({
                    "type": "scene",
                    "expected": expected_scene,
                    "actual": actual_scene,
                    "passed": passed,
                })
            except Exception as e:
                checks.append({
                    "type": "scene",
                    "error": str(e),
                    "passed": False,
                })

        # 检查最终截图（如果有）
        if check_screenshots and self._actions:
            last_action = self._actions[-1]
            if last_action.screenshot:
                try:
                    from drivers.godot.visual import TemplateMatch, VisualAsserter

                    asserter = VisualAsserter()
                    expected_path = self._recording_dir / last_action.screenshot
                    if expected_path.exists():
                        actual_path = await driver.screenshot("verify_check.png")
                        template = TemplateMatch(
                            template_path=str(expected_path),
                            threshold=screenshot_threshold,
                        )
                        result = asserter.match_template(actual_path, template)
                        checks.append({
                            "type": "final_screenshot",
                            "expected": str(expected_path),
                            "confidence": result.confidence,
                            "threshold": screenshot_threshold,
                            "passed": result.matched,
                        })
                except Exception as e:
                    checks.append({
                        "type": "final_screenshot",
                        "error": str(e),
                        "passed": False,
                    })

        passed_count = sum(1 for c in checks if c.get("passed", False))

        return VerifyResult(
            passed=all(c.get("passed", False) for c in checks),
            checks_total=len(checks),
            checks_passed=passed_count,
            checks_failed=len(checks) - passed_count,
            details=checks,
        )

    async def _execute_action(self, driver: Any, action: RecordedAction) -> None:
        """执行单个操作

        Args:
            driver: GodotDriver 实例
            action: 要执行的操作

        Raises:
            ValueError: 未知的操作类型
            Exception: 执行失败
        """
        action_type = action.type
        params = action.params

        # 映射操作类型到 driver 方法
        if action_type == "click_node":
            await driver.click_node(params["path"])
        elif action_type == "input_key":
            await driver.input_key(
                params.get("keycode", 0),
                params.get("pressed", True),
            )
        elif action_type == "input_action":
            await driver.input_action(
                params.get("action", ""),
                params.get("strength", 1.0),
            )
        elif action_type == "input_mouse_button":
            pos = params.get("position", [0, 0])
            await driver.input_mouse_button(
                position=tuple(pos),
                button_index=params.get("button_index", 1),
                pressed=params.get("pressed", True),
            )
        elif action_type == "input_mouse_motion":
            pos = params.get("position", [0, 0])
            rel = params.get("relative", [0, 0])
            await driver.input_mouse_motion(
                position=tuple(pos),
                relative=tuple(rel),
            )
        elif action_type == "change_scene":
            await driver.change_scene(params["scene_path"])
        elif action_type == "reload_scene":
            await driver.reload_scene()
        elif action_type == "wait_seconds":
            await driver.wait_seconds(params.get("seconds", 1.0))
        elif action_type == "wait_process_frames":
            await driver.wait_process_frames(params.get("count", 1))
        elif action_type == "wait_for_node":
            await driver.wait_for_node(
                params["path"],
                params.get("timeout", 10.0),
            )
        elif action_type == "wait_for_scene":
            await driver.wait_for_scene(
                params["scene_path"],
                params.get("timeout", 10.0),
            )
        elif action_type == "set_property":
            await driver.set_property(
                params["path"],
                params["property"],
                params["value"],
            )
        elif action_type == "call_method":
            await driver.call_method(
                params["path"],
                params["method"],
                params.get("args"),
            )
        elif action_type == "screenshot":
            # 截图操作：只是截取屏幕，不做其他操作
            await driver.screenshot(params.get("filename"))
        else:
            # 未知操作类型：尝试通用 _send
            try:
                await driver._send(action_type, params)
            except Exception as e:
                raise ValueError(f"未知操作类型: {action_type}") from e
