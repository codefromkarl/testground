"""Visual framework smoke tests — validates the integrated screenshot
comparison, template matching, and diff utilities without requiring Godot.

These tests use synthetic PIL images, so they run under @pytest.mark.fast.
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest

# Skip entire module if PIL is unavailable
PIL = pytest.importorskip("PIL")
from PIL import Image

from tests.visual.framework import (
    ComparisonMode,
    ScreenshotComparer,
    VisualRegressionDetector,
    VisualAssertions,
    diff_ratio,
    build_diff_result,
    load_pixels,
)


@pytest.fixture
def output_dir(tmp_path: Path) -> Path:
    return tmp_path / "visual_output"


@pytest.fixture
def red_png(tmp_path: Path) -> str:
    """100x100 red PNG."""
    path = tmp_path / "red.png"
    Image.new("RGB", (100, 100), (255, 0, 0)).save(path)
    return str(path)


@pytest.fixture
def blue_png(tmp_path: Path) -> str:
    """100x100 blue PNG."""
    path = tmp_path / "blue.png"
    Image.new("RGB", (100, 100), (0, 0, 255)).save(path)
    return str(path)


@pytest.fixture
def red_rgba_png(tmp_path: Path) -> str:
    """100x100 red RGBA PNG."""
    path = tmp_path / "red_rgba.png"
    Image.new("RGBA", (100, 100), (255, 0, 0, 255)).save(path)
    return str(path)


# ─── Screenshot comparer ────────────────────────────────────


@pytest.mark.fast
class TestScreenshotComparer:
    def test_pixel_exact_identical(self, red_png: str, output_dir: Path):
        comp = ScreenshotComparer(str(output_dir))
        result = comp.compare_screenshots(red_png, red_png, mode=ComparisonMode.PIXEL_EXACT)
        assert result.is_identical
        assert result.similarity_score == 1.0
        assert result.pixel_diff_count == 0

    def test_pixel_exact_different(self, red_png: str, blue_png: str, output_dir: Path):
        comp = ScreenshotComparer(str(output_dir))
        result = comp.compare_screenshots(red_png, blue_png, mode=ComparisonMode.PIXEL_EXACT)
        assert not result.is_identical
        assert result.pixel_diff_count > 0

    def test_structural_similarity_identical(self, red_png: str, output_dir: Path):
        comp = ScreenshotComparer(str(output_dir))
        result = comp.compare_screenshots(red_png, red_png, mode=ComparisonMode.STRUCTURAL)
        assert result.is_identical
        assert result.similarity_score > 0.99

    def test_perceptual_hash_identical(self, red_png: str, output_dir: Path):
        comp = ScreenshotComparer(str(output_dir))
        result = comp.compare_screenshots(red_png, red_png, mode=ComparisonMode.PERCEPTUAL)
        assert result.is_identical
        assert result.similarity_score == 1.0

    def test_different_sizes_resized(self, tmp_path: Path, output_dir: Path):
        comp = ScreenshotComparer(str(output_dir))
        small = tmp_path / "small.png"
        large = tmp_path / "large.png"
        Image.new("RGB", (50, 50), (128, 128, 128)).save(small)
        Image.new("RGB", (100, 100), (128, 128, 128)).save(large)

        result = comp.compare_screenshots(str(small), str(large), mode=ComparisonMode.PIXEL_TOLERANT)
        assert result.is_identical  # same color, resized


# ─── Visual regression detector ─────────────────────────────


@pytest.mark.fast
class TestVisualRegressionDetector:
    def test_creates_baseline_on_first_run(self, red_png: str, tmp_path: Path):
        baseline_dir = tmp_path / "baselines"
        detector = VisualRegressionDetector(str(baseline_dir))
        result = detector.check_regression("test_red", red_png, tolerance=0.05)
        assert result.details is not None
        assert result.details.get("message") == "Baseline created"
        assert (baseline_dir / "test_red.png").exists()

    def test_detects_regression(self, red_png: str, blue_png: str, tmp_path: Path):
        baseline_dir = tmp_path / "baselines"
        detector = VisualRegressionDetector(str(baseline_dir))
        # First run: create baseline from red
        detector.check_regression("test_color", red_png)
        # Second run: compare blue against red baseline
        result = detector.check_regression("test_color", blue_png, tolerance=0.05)
        assert not result.is_identical
        assert result.similarity_score < 0.95


# ─── Diff utils ─────────────────────────────────────────────


@pytest.mark.fast
class TestDiffUtils:
    def test_load_pixels(self, red_rgba_png: str):
        pixels = load_pixels(Path(red_rgba_png))
        assert len(pixels) == 100 * 100
        assert pixels[0] == (255, 0, 0, 255)

    def test_diff_ratio_identical(self, red_rgba_png: str):
        ratio = diff_ratio(Path(red_rgba_png), Path(red_rgba_png))
        assert ratio == 0.0

    def test_diff_ratio_different(self, red_rgba_png: str, tmp_path: Path):
        blue_path = tmp_path / "blue_rgba.png"
        Image.new("RGBA", (100, 100), (0, 0, 255, 255)).save(blue_path)
        ratio = diff_ratio(Path(red_rgba_png), blue_path)
        assert ratio == 1.0

    def test_build_diff_result(self, red_rgba_png: str, tmp_path: Path):
        blue_path = tmp_path / "blue_rgba.png"
        Image.new("RGBA", (100, 100), (0, 0, 255, 255)).save(blue_path)
        result = build_diff_result(Path(red_rgba_png), blue_path, threshold=0.01)
        assert result.ratio == 1.0
        assert result.passed  # completely different passes the "visible change" check


# ─── Visual assertions API ──────────────────────────────────


@pytest.mark.fast
class TestVisualAssertions:
    def test_assert_screenshots_equal_passes(self, red_png: str, tmp_path: Path):
        va = VisualAssertions(output_dir=str(tmp_path / "out"))
        va.assert_screenshots_equal("same", red_png, red_png, tolerance=0.05)
        assert va.get_summary()["passed"] == 1

    def test_assert_screenshots_equal_fails(self, red_png: str, blue_png: str, tmp_path: Path):
        va = VisualAssertions(output_dir=str(tmp_path / "out"))
        with pytest.raises(AssertionError):
            va.assert_screenshots_equal("diff", red_png, blue_png, tolerance=0.05)
        assert va.get_summary()["failed"] == 1

    def test_assert_no_visual_regression_creates_baseline(self, red_png: str, tmp_path: Path):
        baseline = tmp_path / "baselines"
        va = VisualAssertions(output_dir=str(tmp_path / "out"), baseline_dir=str(baseline))
        va.assert_no_visual_regression("baseline_test", red_png)
        assert (baseline / "baseline_test.png").exists()
        assert va.get_summary()["passed"] == 1
