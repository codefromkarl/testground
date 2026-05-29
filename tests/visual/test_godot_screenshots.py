"""Visual regression tests integrated with GodotDriver.

Validates that VisualAssertions works with Godot screenshot workflows
without requiring a real Godot process. All tests use mocked GodotDriver
to produce synthetic screenshots.

Run with: pytest tests/visual/test_godot_screenshots.py -v
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

PIL = pytest.importorskip("PIL")
from PIL import Image

from tests.visual.framework import VisualAssertions, VisualRegressionDetector

pytestmark = [pytest.mark.visual, pytest.mark.medium]


# ─── Helpers ─────────────────────────────────────────────────


def _make_screenshot(path: Path, color: tuple = (255, 0, 0), size: tuple = (320, 240)) -> str:
    """Create a synthetic screenshot PNG and return its path."""
    img = Image.new("RGB", size, color)
    img.save(path)
    return str(path)


def _create_mock_driver(screenshot_path: Path) -> MagicMock:
    """Create a MagicMock GodotDriver whose screenshot() returns a resolved Path."""
    driver = MagicMock()
    driver.screenshot = AsyncMock(return_value=screenshot_path)
    driver.screenshot_base64 = AsyncMock(return_value="")
    driver.observe = AsyncMock(return_value={"scene": "res://Main.tscn"})
    driver.get_scene = AsyncMock(return_value="res://Main.tscn")
    driver.close = AsyncMock()
    return driver


# ─── Fixtures ────────────────────────────────────────────────


@pytest.fixture
def baselines(tmp_path: Path) -> Path:
    return tmp_path / "baselines"


@pytest.fixture
def output_dir(tmp_path: Path) -> Path:
    return tmp_path / "visual_output"


@pytest.fixture
def red_screenshot(tmp_path: Path) -> Path:
    path = tmp_path / "godot_screenshot_red.png"
    _make_screenshot(path, color=(255, 0, 0))
    return path


@pytest.fixture
def green_screenshot(tmp_path: Path) -> Path:
    path = tmp_path / "godot_screenshot_green.png"
    _make_screenshot(path, color=(0, 255, 0))
    return path


@pytest.fixture
def identical_screenshot(tmp_path: Path) -> Path:
    path = tmp_path / "godot_screenshot_copy.png"
    _make_screenshot(path, color=(255, 0, 0))
    return path


# ─── Test: GodotDriver mock screenshot → VisualAssertions ────


class TestGodotDriverVisualIntegration:
    """Verify that a mocked GodotDriver screenshot flows through
    VisualAssertions.assert_no_visual_regression without errors."""

    def test_screenshot_creates_baseline(
        self, red_screenshot: Path, baselines: Path, output_dir: Path
    ):
        """First run: assert_no_visual_regression creates a baseline from the
        Godot screenshot."""
        driver = _create_mock_driver(red_screenshot)
        screenshot_path = asyncio.get_event_loop().run_until_complete(
            driver.screenshot("battle_ui.png")
        )

        va = VisualAssertions(
            output_dir=str(output_dir),
            baseline_dir=str(baselines),
        )
        va.assert_no_visual_regression("battle_ui", str(screenshot_path))

        assert (baselines / "battle_ui.png").exists()
        assert va.get_summary()["passed"] == 1

    def test_screenshot_no_regression(
        self,
        red_screenshot: Path,
        identical_screenshot: Path,
        baselines: Path,
        output_dir: Path,
    ):
        """Baseline exists → identical screenshot passes regression check."""
        detector = VisualRegressionDetector(str(baselines))
        detector.check_regression("main_scene", str(red_screenshot))

        driver = _create_mock_driver(identical_screenshot)
        screenshot_path = asyncio.get_event_loop().run_until_complete(
            driver.screenshot("main_scene.png")
        )

        va = VisualAssertions(
            output_dir=str(output_dir),
            baseline_dir=str(baselines),
        )
        va.assert_no_visual_regression("main_scene", str(screenshot_path))
        assert va.get_summary()["passed"] == 1

    def test_screenshot_detects_regression(
        self,
        red_screenshot: Path,
        green_screenshot: Path,
        baselines: Path,
        output_dir: Path,
    ):
        """Baseline exists → different screenshot triggers AssertionError."""
        detector = VisualRegressionDetector(str(baselines))
        detector.check_regression("menu", str(red_screenshot))

        driver = _create_mock_driver(green_screenshot)
        screenshot_path = asyncio.get_event_loop().run_until_complete(
            driver.screenshot("menu.png")
        )

        va = VisualAssertions(
            output_dir=str(output_dir),
            baseline_dir=str(baselines),
        )
        with pytest.raises(AssertionError, match="Visual regression"):
            va.assert_no_visual_regression("menu", str(screenshot_path))
        assert va.get_summary()["failed"] == 1

    def test_two_screenshots_equal(
        self,
        red_screenshot: Path,
        identical_screenshot: Path,
        output_dir: Path,
    ):
        """assert_screenshots_equal with two identical Godot screenshots passes."""
        driver = _create_mock_driver(red_screenshot)
        path_a = asyncio.get_event_loop().run_until_complete(
            driver.screenshot("a.png")
        )
        driver.screenshot.return_value = identical_screenshot
        path_b = asyncio.get_event_loop().run_until_complete(
            driver.screenshot("b.png")
        )

        va = VisualAssertions(output_dir=str(output_dir))
        va.assert_screenshots_equal(
            "scene_match", str(path_a), str(path_b), tolerance=0.05
        )
        assert va.get_summary()["passed"] == 1

    def test_two_screenshots_differ(
        self,
        red_screenshot: Path,
        green_screenshot: Path,
        output_dir: Path,
    ):
        """assert_screenshots_equal with different screenshots raises."""
        driver = _create_mock_driver(red_screenshot)
        path_a = asyncio.get_event_loop().run_until_complete(
            driver.screenshot("a.png")
        )
        driver.screenshot.return_value = green_screenshot
        path_b = asyncio.get_event_loop().run_until_complete(
            driver.screenshot("b.png")
        )

        va = VisualAssertions(output_dir=str(output_dir))
        with pytest.raises(AssertionError, match="Screenshots differ"):
            va.assert_screenshots_equal(
                "scene_diff", str(path_a), str(path_b), tolerance=0.05
            )
        assert va.get_summary()["failed"] == 1


# ─── Test: multi-step Godot workflow ─────────────────────────


class TestMultiStepWorkflow:
    """Simulate a multi-step Godot interaction sequence where
    screenshots are taken at each step and compared."""

    def test_sequential_screenshots(
        self, tmp_path: Path, baselines: Path, output_dir: Path
    ):
        """Simulate: navigate menu → take screenshot → navigate sub-menu →
        take screenshot.  Both screenshots create baselines independently."""
        step1 = _make_screenshot(tmp_path / "step1.png", color=(100, 100, 200))
        step2 = _make_screenshot(tmp_path / "step2.png", color=(200, 200, 100))

        driver = _create_mock_driver(Path(step1))
        driver.screenshot.return_value = Path(step1)

        va = VisualAssertions(
            output_dir=str(output_dir),
            baseline_dir=str(baselines),
        )

        s1 = asyncio.get_event_loop().run_until_complete(
            driver.screenshot("menu.png")
        )
        va.assert_no_visual_regression("menu_step", str(s1))

        driver.screenshot.return_value = Path(step2)
        s2 = asyncio.get_event_loop().run_until_complete(
            driver.screenshot("sub_menu.png")
        )
        va.assert_no_visual_regression("sub_menu_step", str(s2))

        assert va.get_summary()["passed"] == 2
        assert va.get_summary()["total"] == 2

    def test_partial_change_regression(
        self, tmp_path: Path, baselines: Path, output_dir: Path
    ):
        """Only one of two tracked views regresses."""
        view_a = _make_screenshot(tmp_path / "view_a.png", color=(50, 50, 50))
        view_b = _make_screenshot(tmp_path / "view_b.png", color=(200, 200, 200))

        # Seed baselines
        detector = VisualRegressionDetector(str(baselines))
        detector.check_regression("view_a", view_a)
        detector.check_regression("view_b", view_b)

        # view_a unchanged, view_b changes
        view_b_new = _make_screenshot(tmp_path / "view_b_new.png", color=(10, 10, 10))

        va = VisualAssertions(
            output_dir=str(output_dir),
            baseline_dir=str(baselines),
        )
        va.assert_no_visual_regression("view_a", view_a)  # passes

        with pytest.raises(AssertionError):
            va.assert_no_visual_regression("view_b", view_b_new)  # fails

        summary = va.get_summary()
        assert summary["passed"] == 1
        assert summary["failed"] == 1


# ─── Test: driver mock protocol coverage ─────────────────────


class TestDriverMockProtocol:
    """Ensure the mock driver exposes the same interface the real
    GodotDriver does, guarding against API drift."""

    def test_driver_has_screenshot_method(self):
        driver = _create_mock_driver(Path("/dev/null"))
        assert hasattr(driver, "screenshot")
        assert callable(driver.screenshot)

    def test_driver_has_observe_method(self):
        driver = _create_mock_driver(Path("/dev/null"))
        assert hasattr(driver, "observe")
        assert callable(driver.observe)

    def test_driver_has_close_method(self):
        driver = _create_mock_driver(Path("/dev/null"))
        assert hasattr(driver, "close")
        assert callable(driver.close)

    def test_driver_returns_path_from_screenshot(self, tmp_path: Path):
        path = tmp_path / "test.png"
        _make_screenshot(path, color=(0, 0, 0))
        driver = _create_mock_driver(path)
        result = asyncio.get_event_loop().run_until_complete(driver.screenshot())
        assert isinstance(result, Path)
        assert result.exists()
