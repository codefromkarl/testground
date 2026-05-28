"""
Visual assertion library — adapted from stardrifter.

Provides pytest-friendly visual test assertions without requiring
Godot UI tree validation (that remains in the game projects).

Usage:
    from tests.visual.framework import VisualAssertions

    va = VisualAssertions()
    va.assert_screenshots_equal("test_name", "a.png", "b.png", tolerance=0.05)
    va.assert_no_visual_regression("test_name", "current.png", tolerance=0.05)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .screenshot import ComparisonMode, ComparisonResult, ScreenshotComparer, VisualRegressionDetector
from .image_recognition import ColorRange, DetectionResult, ImageRecognizer, TemplateMatcher


class TestStatus(Enum):
    """Visual test status."""
    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"
    ERROR = "error"


@dataclass
class VisualTestResult:
    """Result of a single visual test assertion."""
    test_name: str
    status: TestStatus
    message: str
    details: Optional[Dict[str, any]] = None
    screenshot_path: Optional[str] = None
    diff_image_path: Optional[str] = None


class VisualAssertions:
    """High-level visual assertion API for pytest."""

    def __init__(
        self,
        output_dir: str = "build/visual_tests",
        baseline_dir: str = "tests/visual/baselines",
    ):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

        self.screenshot_comparer = ScreenshotComparer(str(self.output_dir))
        self.regression_detector = VisualRegressionDetector(baseline_dir)
        self.image_recognizer = ImageRecognizer()

        self.results: List[VisualTestResult] = []

    # ─── Screenshot equality ──────────────────────────────────

    def assert_screenshots_equal(
        self,
        test_name: str,
        screenshot1: str,
        screenshot2: str,
        tolerance: float = 0.05,
        mode: ComparisonMode = ComparisonMode.STRUCTURAL,
        message: str = "",
    ) -> None:
        """Assert two screenshots are equal within tolerance."""
        result = self.screenshot_comparer.compare_screenshots(
            screenshot1, screenshot2, mode=mode
        )

        if result.similarity_score >= (1.0 - tolerance):
            self._record_pass(test_name, message or f"Screenshots match ({result.similarity_score:.2%})", result)
        else:
            self._record_fail(
                test_name,
                message or f"Screenshots differ ({result.similarity_score:.2%})",
                result,
            )
            raise AssertionError(
                f"{test_name}: Screenshots differ ({result.similarity_score:.2%}). "
                f"Diff: {result.diff_image_path}"
            )

    # ─── Visual regression (golden image) ─────────────────────

    def assert_no_visual_regression(
        self,
        test_name: str,
        current_screenshot: str,
        tolerance: float = 0.05,
        message: str = "",
    ) -> None:
        """Assert no visual regression against baseline.

        If baseline does not exist, it is auto-created (first-run friendly).
        """
        result = self.regression_detector.check_regression(
            test_name, current_screenshot, tolerance
        )

        if result.similarity_score >= (1.0 - tolerance):
            self._record_pass(
                test_name,
                message or f"No regression ({result.similarity_score:.2%})",
                result,
            )
        else:
            self._record_fail(
                test_name,
                message or f"Regression detected ({result.similarity_score:.2%})",
                result,
            )
            raise AssertionError(
                f"{test_name}: Visual regression ({result.similarity_score:.2%}). "
                f"Diff: {result.diff_image_path}"
            )

    # ─── Color region existence ───────────────────────────────

    def assert_color_region_exists(
        self,
        test_name: str,
        screenshot: str,
        color_name: str,
        color_lower: Tuple[int, int, int],
        color_upper: Tuple[int, int, int],
        min_area: int = 100,
        message: str = "",
    ) -> None:
        """Assert a color region exists in the screenshot."""
        color_range = ColorRange(color_name, color_lower, color_upper)
        result = self.image_recognizer.detect_color_region(
            screenshot, color_range, min_area
        )

        if result.found:
            self.results.append(
                VisualTestResult(
                    test_name=test_name,
                    status=TestStatus.PASSED,
                    message=message or f"Color '{color_name}' found",
                    details={"location": result.location, "confidence": result.confidence},
                )
            )
        else:
            self.results.append(
                VisualTestResult(
                    test_name=test_name,
                    status=TestStatus.FAILED,
                    message=message or f"Color '{color_name}' not found",
                )
            )
            raise AssertionError(f"{test_name}: Color region '{color_name}' not found")

    # ─── Template matching ────────────────────────────────────

    def assert_template_exists(
        self,
        test_name: str,
        screenshot: str,
        template: str,
        threshold: float = 0.8,
        message: str = "",
    ) -> Tuple[int, int]:
        """Assert a template image exists within the screenshot.

        Returns the center location (x, y) of the match.
        """
        matcher = TemplateMatcher()
        result = matcher.match_template(screenshot, template, threshold)

        if result.found:
            self.results.append(
                VisualTestResult(
                    test_name=test_name,
                    status=TestStatus.PASSED,
                    message=message or f"Template found at {result.location}",
                    details={"location": result.location, "confidence": result.confidence},
                )
            )
            return result.location

        self.results.append(
            VisualTestResult(
                test_name=test_name,
                status=TestStatus.FAILED,
                message=message or f"Template not found (confidence {result.confidence:.2f})",
            )
        )
        raise AssertionError(
            f"{test_name}: Template not found (threshold={threshold}, best={result.confidence:.2f})"
        )

    # ─── Helpers ──────────────────────────────────────────────

    def _record_pass(self, test_name: str, message: str, result: ComparisonResult) -> None:
        self.results.append(
            VisualTestResult(
                test_name=test_name,
                status=TestStatus.PASSED,
                message=message,
                details={"similarity": result.similarity_score},
            )
        )

    def _record_fail(self, test_name: str, message: str, result: ComparisonResult) -> None:
        self.results.append(
            VisualTestResult(
                test_name=test_name,
                status=TestStatus.FAILED,
                message=message,
                details={"similarity": result.similarity_score, "diff_pixels": result.pixel_diff_count},
                diff_image_path=result.diff_image_path,
            )
        )

    def get_summary(self) -> Dict[str, int]:
        """Return count of passed/failed/skipped results."""
        return {
            "passed": sum(1 for r in self.results if r.status == TestStatus.PASSED),
            "failed": sum(1 for r in self.results if r.status == TestStatus.FAILED),
            "skipped": sum(1 for r in self.results if r.status == TestStatus.SKIPPED),
            "total": len(self.results),
        }
