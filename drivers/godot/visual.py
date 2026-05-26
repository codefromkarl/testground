"""视觉断言层 — 借鉴 Airtest 图像识别用于 Godot 游戏测试

Airtest 核心思路:
  1. 模板匹配 (cv2.matchTemplate + TM_CCOEFF_NORMED) + RGB 三通道校验
  2. 关键点匹配 (KAZE/AKAZE/BRISK/ORB) 用于缩放/旋转场景
  3. loop_find 超时循环等待机制

本模块将 Airtest 的 CV 能力适配到 godot_e2e 截图之上:
  - screenshot → OpenCV 模板匹配 → 视觉断言
  - loop_find → 等待 UI 元素出现
  - find_all → 批量检测同类元素 (如手牌区域)

依赖: opencv-python, numpy, Pillow (可选)
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, List, Optional, Tuple

try:
    import cv2
    import numpy as np

    HAS_CV2 = True
except ImportError:
    HAS_CV2 = False

try:
    from PIL import Image

    HAS_PIL = True
except ImportError:
    HAS_PIL = False


# ─── 数据结构 ──────────────────────────────────────────────


@dataclass
class TemplateMatch:
    """模板匹配参数"""

    # 模板图片路径或 numpy 数组
    template_path: Optional[str] = None
    template_array: Optional[Any] = None  # np.ndarray
    # 匹配阈值 (0-1), Airtest 默认 0.8
    threshold: float = 0.8
    # 是否使用 RGB 三通道校验 (Airtest 的 rgb 模式)
    rgb: bool = True
    # 目标点击位置偏移 (0-1 比例), 如 (0.5, 0.5) = 中心
    target_pos: Tuple[float, float] = (0.5, 0.5)
    # 分辨率缩放区域 (用于多尺度匹配)
    scale_range: Tuple[float, float] = (0.8, 1.2)
    scale_steps: int = 5


@dataclass
class VisualMatchResult:
    """视觉匹配结果"""

    matched: bool
    position: Optional[Tuple[int, int]] = None  # 匹配中心坐标
    confidence: float = 0.0
    rectangle: Optional[Tuple[Tuple[int, int], ...]] = None  # 四角坐标
    scale: float = 1.0  # 匹配时的缩放比
    template_shape: Optional[Tuple[int, int]] = None


# ─── 视觉断言器 ────────────────────────────────────────────


class VisualAsserter:
    """基于 OpenCV 的视觉断言器

    用法:
        asserter = VisualAsserter()
        result = asserter.assert_template(
            screenshot_path="test.png",
            template_path="button.png",
            threshold=0.85,
        )
        assert result.matched

    与 GodotDriver 集成:
        async with GodotDriver() as driver:
            screenshot = await driver.screenshot("state.png")
            asserter = VisualAsserter()
            result = asserter.assert_exists(screenshot, TemplateMatch(
                template_path="expected_battle_ui.png",
                threshold=0.8,
            ))
    """

    def __init__(self):
        if not HAS_CV2:
            raise ImportError("视觉断言需要 opencv-python 和 numpy。安装: pip install opencv-python numpy")

    # ─── 模板匹配 (Airtest TemplateMatching 核心) ──────────

    def match_template(
        self,
        source: Any,  # np.ndarray | str | Path
        template: TemplateMatch,
    ) -> VisualMatchResult:
        """单目标模板匹配

        实现了 Airtest 的 TemplateMatching.find_best_result 逻辑:
        1. 灰度 cv2.matchTemplate + TM_CCOEFF_NORMED
        2. 可选 RGB 三通道置信度校验
        3. 多尺度搜索
        """
        img_source = self._load_image(source)
        img_template = self._load_template(template)

        if img_source is None or img_template is None:
            return VisualMatchResult(matched=False)

        sh, sw = img_source.shape[:2]
        th, tw = img_template.shape[:2]

        if th > sh or tw > sw:
            return VisualMatchResult(matched=False)

        best_result = VisualMatchResult(matched=False, confidence=0.0)

        # 多尺度搜索
        scales = np.linspace(template.scale_range[0], template.scale_range[1], template.scale_steps)

        for scale in scales:
            scaled_h, scaled_w = int(th * scale), int(tw * scale)
            if scaled_h > sh or scaled_w > sw or scaled_h < 1 or scaled_w < 1:
                continue

            scaled_template = cv2.resize(img_template, (scaled_w, scaled_h))

            # 灰度匹配 (Airtest 的 _get_template_result_matrix)
            gray_source = cv2.cvtColor(img_source, cv2.COLOR_BGR2GRAY)
            gray_template = cv2.cvtColor(scaled_template, cv2.COLOR_BGR2GRAY)
            result_matrix = cv2.matchTemplate(gray_source, gray_template, cv2.TM_CCOEFF_NORMED)
            _, max_val, _, max_loc = cv2.minMaxLoc(result_matrix)

            # RGB 三通道校验 (Airtest 的 cal_rgb_confidence)
            if template.rgb:
                x, y = max_loc
                crop = img_source[y : y + scaled_h, x : x + scaled_w]
                if crop.shape[:2] == scaled_template.shape[:2]:
                    confidence = self._cal_rgb_confidence(crop, scaled_template)
                else:
                    confidence = max_val
            else:
                confidence = max_val

            if confidence > best_result.confidence:
                # 计算匹配中心 (Airtest 的 _get_target_rectangle)
                cx = int(max_loc[0] + scaled_w * template.target_pos[0])
                cy = int(max_loc[1] + scaled_h * template.target_pos[1])

                rect = (
                    (max_loc[0], max_loc[1]),
                    (max_loc[0], max_loc[1] + scaled_h),
                    (max_loc[0] + scaled_w, max_loc[1] + scaled_h),
                    (max_loc[0] + scaled_w, max_loc[1]),
                )

                best_result = VisualMatchResult(
                    matched=confidence >= template.threshold,
                    position=(cx, cy),
                    confidence=confidence,
                    rectangle=rect,
                    scale=scale,
                    template_shape=(th, tw),
                )

        return best_result

    def match_all_templates(
        self,
        source: Any,
        template: TemplateMatch,
        max_count: int = 10,
    ) -> List[VisualMatchResult]:
        """多目标模板匹配

        实现了 Airtest 的 TemplateMatching.find_all_results:
        循环取出最优结果，用矩形遮挡已匹配区域，继续搜索。
        """
        img_source = self._load_image(source)
        img_template = self._load_template(template)

        if img_source is None or img_template is None:
            return []

        th, tw = img_template.shape[:2]
        gray_source = cv2.cvtColor(img_source, cv2.COLOR_BGR2GRAY)
        gray_template = cv2.cvtColor(img_template, cv2.COLOR_BGR2GRAY)

        result_matrix = cv2.matchTemplate(gray_source, gray_template, cv2.TM_CCOEFF_NORMED)

        results: List[VisualMatchResult] = []

        for _ in range(max_count):
            _, max_val, _, max_loc = cv2.minMaxLoc(result_matrix)

            if template.rgb:
                x, y = max_loc
                crop = img_source[y : y + th, x : x + tw]
                if crop.shape[:2] == img_template.shape[:2]:
                    confidence = self._cal_rgb_confidence(crop, img_template)
                else:
                    confidence = max_val
            else:
                confidence = max_val

            if confidence < template.threshold:
                break

            cx = int(max_loc[0] + tw * template.target_pos[0])
            cy = int(max_loc[1] + th * template.target_pos[1])

            rect = (
                (max_loc[0], max_loc[1]),
                (max_loc[0], max_loc[1] + th),
                (max_loc[0] + tw, max_loc[1] + th),
                (max_loc[0] + tw, max_loc[1]),
            )

            results.append(
                VisualMatchResult(
                    matched=True,
                    position=(cx, cy),
                    confidence=confidence,
                    rectangle=rect,
                    template_shape=(th, tw),
                )
            )

            # 遮挡已匹配区域 (Airtest 用 cv2.rectangle 黑色填充)
            cv2.rectangle(
                result_matrix,
                (max(0, max_loc[0] - tw // 2), max(0, max_loc[1] - th // 2)),
                (min(result_matrix.shape[1], max_loc[0] + tw // 2), min(result_matrix.shape[0], max_loc[1] + th // 2)),
                0,
                -1,
            )

        return results

    # ─── 断言方法 (Airtest API 风格) ─────────────────────

    def assert_exists(self, source: Any, template: TemplateMatch) -> VisualMatchResult:
        """断言模板存在 (对应 Airtest assert_exists)"""
        result = self.match_template(source, template)
        if not result.matched:
            raise AssertionError(f"模板未找到: confidence={result.confidence:.4f} < threshold={template.threshold}")
        return result

    def assert_not_exists(self, source: Any, template: TemplateMatch) -> None:
        """断言模板不存在 (对应 Airtest assert_not_exists)"""
        result = self.match_template(source, template)
        if result.matched:
            raise AssertionError(f"模板不应存在但找到了: confidence={result.confidence:.4f} at {result.position}")

    def exists(self, source: Any, template: TemplateMatch) -> VisualMatchResult:
        """检查模板是否存在 (不抛异常, 对应 Airtest exists)"""
        return self.match_template(source, template)

    def find_all(self, source: Any, template: TemplateMatch, max_count: int = 10) -> List[VisualMatchResult]:
        """查找所有匹配 (对应 Airtest find_all)"""
        return self.match_all_templates(source, template, max_count)

    def loop_find(
        self,
        screenshot_func,
        template: TemplateMatch,
        timeout: float = 20.0,
        interval: float = 0.5,
    ) -> VisualMatchResult:
        """循环等待模板出现 (对应 Airtest loop_find)

        screenshot_func: 异步或同步截图函数, 返回图片路径或数组
        """
        deadline = time.time() + timeout
        while time.time() < deadline:
            screenshot = screenshot_func()
            result = self.match_template(screenshot, template)
            if result.matched:
                return result
            time.sleep(interval)

        return VisualMatchResult(matched=False, confidence=0.0)

    # ─── 辅助方法 ────────────────────────────────────────

    @staticmethod
    def _load_image(source: Any) -> Any:
        """加载图片为 numpy 数组"""
        if isinstance(source, np.ndarray):
            return source
        if isinstance(source, (str, Path)):
            path = Path(source)
            if not path.exists():
                return None
            return cv2.imread(str(path))
        if isinstance(source, bytes):
            return cv2.imdecode(np.frombuffer(source, np.uint8), cv2.IMREAD_COLOR)
        if HAS_PIL and isinstance(source, Image.Image):
            return cv2.cvtColor(np.array(source), cv2.COLOR_RGB2BGR)
        return None

    def _load_template(self, template: TemplateMatch) -> Any:
        """加载模板图片"""
        if template.template_array is not None:
            return template.template_array
        if template.template_path:
            return self._load_image(template.template_path)
        return None

    @staticmethod
    def _cal_rgb_confidence(img_crop: Any, img_template: Any) -> float:
        """RGB 三通道置信度计算 (借鉴 Airtest cal_rgb_confidence)

        对 BGR 三通道分别计算归一化相关系数，取平均。
        """
        if img_crop.shape != img_template.shape:
            return 0.0

        channels = []
        for i in range(3):
            src = img_crop[:, :, i].astype(np.float32)
            tpl = img_template[:, :, i].astype(np.float32)
            src_norm = src - src.mean()
            tpl_norm = tpl - tpl.mean()
            denom = np.sqrt(np.sum(src_norm**2) * np.sum(tpl_norm**2))
            if denom == 0:
                channels.append(0.0)
            else:
                channels.append(float(np.sum(src_norm * tpl_norm) / denom))

        return sum(channels) / 3.0

    # ─── 快捷方法 ────────────────────────────────────────

    @staticmethod
    def save_template_from_screenshot(
        screenshot_path: str,
        region: Tuple[int, int, int, int],
        output_path: str,
    ) -> Path:
        """从截图中裁切区域保存为模板

        region: (x, y, width, height)
        """
        img = cv2.imread(screenshot_path)
        x, y, w, h = region
        crop = img[y : y + h, x : x + w]
        output = Path(output_path)
        output.parent.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(output), crop)
        return output
