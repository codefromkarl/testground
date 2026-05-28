"""
Screenshot comparison engine — adapted from stardrifter.

Supports pixel-exact, pixel-tolerant, structural (SSIM), and perceptual-hash
comparison modes. Also includes VisualRegressionDetector for baseline-based
golden-image testing (inspired by loopepedition's ScreenshotTestHelper).
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional, Tuple

try:
    from PIL import Image, ImageChops
    HAS_PIL = True
except ImportError:
    HAS_PIL = False

try:
    import numpy as np
    HAS_NUMPY = True
except ImportError:
    HAS_NUMPY = False


class ComparisonMode(Enum):
    """Comparison mode for screenshots."""
    PIXEL_EXACT = "pixel_exact"          # Pixels must be identical
    PIXEL_TOLERANT = "pixel_tolerant"    # Per-pixel tolerance (0-255)
    STRUCTURAL = "structural"            # Structural similarity (SSIM)
    PERCEPTUAL = "perceptual"            # Perceptual hash (pHash)


@dataclass
class ComparisonResult:
    """Result of comparing two screenshots."""
    is_identical: bool
    similarity_score: float  # 0.0 - 1.0
    pixel_diff_count: int
    diff_image_path: Optional[str] = None
    details: Optional[Dict[str, any]] = None


@dataclass
class RegionOfInterest:
    """Region of interest within a screenshot."""
    x: int
    y: int
    width: int
    height: int
    name: str = ""


class ScreenshotComparer:
    """Compare screenshots using multiple algorithms."""

    def __init__(self, output_dir: str = "build/visual_tests"):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

        if not HAS_PIL:
            raise ImportError("PIL is required. Install with: pip install Pillow")

    def compare_screenshots(
        self,
        image1_path: str,
        image2_path: str,
        mode: ComparisonMode = ComparisonMode.PIXEL_TOLERANT,
        tolerance: int = 10,
        region: Optional[RegionOfInterest] = None,
    ) -> ComparisonResult:
        """Compare two screenshots.

        Args:
            image1_path: Path to first image.
            image2_path: Path to second image.
            mode: Comparison algorithm.
            tolerance: Per-pixel tolerance (0-255), used for PIXEL_TOLERANT.
            region: Optional ROI to compare instead of full image.
        """
        img1 = Image.open(image1_path)
        img2 = Image.open(image2_path)

        if region:
            bbox = (region.x, region.y, region.x + region.width, region.y + region.height)
            img1 = img1.crop(bbox)
            img2 = img2.crop(bbox)

        if img1.size != img2.size:
            img2 = img2.resize(img1.size)

        if mode == ComparisonMode.PIXEL_EXACT:
            return self._compare_pixel_exact(img1, img2)
        elif mode == ComparisonMode.PIXEL_TOLERANT:
            return self._compare_pixel_tolerant(img1, img2, tolerance)
        elif mode == ComparisonMode.STRUCTURAL:
            return self._compare_structural(img1, img2)
        elif mode == ComparisonMode.PERCEPTUAL:
            return self._compare_perceptual(img1, img2)
        else:
            raise ValueError(f"Unknown comparison mode: {mode}")

    def _compare_pixel_exact(self, img1: Image.Image, img2: Image.Image) -> ComparisonResult:
        diff = ImageChops.difference(img1, img2)
        diff_pixels = diff.getbbox()

        if diff_pixels is None:
            return ComparisonResult(
                is_identical=True,
                similarity_score=1.0,
                pixel_diff_count=0,
            )

        diff_array = np.array(diff) if HAS_NUMPY else None
        diff_count = int(np.sum(diff_array > 0)) if diff_array is not None else 0

        diff_path = self.output_dir / "diff_exact.png"
        diff.save(diff_path)

        return ComparisonResult(
            is_identical=False,
            similarity_score=0.0,
            pixel_diff_count=diff_count,
            diff_image_path=str(diff_path),
        )

    def _compare_pixel_tolerant(self, img1: Image.Image, img2: Image.Image, tolerance: int) -> ComparisonResult:
        if not HAS_NUMPY:
            diff = ImageChops.difference(img1, img2)
            diff_pixels = diff.getbbox()
            is_identical = diff_pixels is None
            return ComparisonResult(
                is_identical=is_identical,
                similarity_score=1.0 if is_identical else 0.5,
                pixel_diff_count=0,
            )

        arr1 = np.array(img1)
        arr2 = np.array(img2)
        diff = np.abs(arr1.astype(int) - arr2.astype(int))
        diff_mask = diff > tolerance
        diff_count = int(np.sum(diff_mask))
        total_pixels = arr1.shape[0] * arr1.shape[1]
        similarity = 1.0 - (diff_count / total_pixels) if total_pixels else 1.0

        diff_image = Image.fromarray((diff_mask * 255).astype(np.uint8))
        diff_path = self.output_dir / "diff_tolerant.png"
        diff_image.save(diff_path)

        return ComparisonResult(
            is_identical=diff_count == 0,
            similarity_score=float(similarity),
            pixel_diff_count=diff_count,
            diff_image_path=str(diff_path),
        )

    def _compare_structural(self, img1: Image.Image, img2: Image.Image) -> ComparisonResult:
        if not HAS_NUMPY:
            return self._compare_pixel_tolerant(img1, img2, 10)

        gray1 = np.array(img1.convert("L"))
        gray2 = np.array(img2.convert("L"))

        mu1 = gray1.mean()
        mu2 = gray2.mean()
        sigma1_sq = gray1.var()
        sigma2_sq = gray2.var()
        sigma12 = np.mean((gray1 - mu1) * (gray2 - mu2))

        C1 = (0.01 * 255) ** 2
        C2 = (0.03 * 255) ** 2

        numerator = (2 * mu1 * mu2 + C1) * (2 * sigma12 + C2)
        denominator = (mu1 ** 2 + mu2 ** 2 + C1) * (sigma1_sq + sigma2_sq + C2)
        ssim = numerator / denominator if denominator else 1.0

        diff = np.abs(gray1.astype(int) - gray2.astype(int))
        diff_count = int(np.sum(diff > 10))

        return ComparisonResult(
            is_identical=ssim > 0.95,
            similarity_score=float(ssim),
            pixel_diff_count=diff_count,
        )

    def _compare_perceptual(self, img1: Image.Image, img2: Image.Image) -> ComparisonResult:
        hash1 = self._calculate_phash(img1)
        hash2 = self._calculate_phash(img2)
        hamming_distance = bin(hash1 ^ hash2).count("1")
        similarity = 1.0 - (hamming_distance / 64)

        return ComparisonResult(
            is_identical=hamming_distance <= 5,
            similarity_score=float(similarity),
            pixel_diff_count=hamming_distance,
        )

    def _calculate_phash(self, img: Image.Image) -> int:
        img_small = img.resize((8, 8), Image.LANCZOS).convert("L")
        if hasattr(img_small, "get_flattened_data"):
            pixels = list(img_small.get_flattened_data())
        else:
            pixels = list(img_small.getdata())
        avg = sum(pixels) / len(pixels)
        hash_value = 0
        for i, pixel in enumerate(pixels):
            if pixel > avg:
                hash_value |= (1 << i)
        return hash_value

    def compare_regions(
        self,
        image_path: str,
        regions: List[RegionOfInterest],
        expected_colors: Optional[Dict[str, Tuple[int, int, int]]] = None,
    ) -> Dict[str, ComparisonResult]:
        """Compare multiple regions of a screenshot."""
        img = Image.open(image_path)
        results = {}

        for region in regions:
            bbox = (region.x, region.y, region.x + region.width, region.y + region.height)
            region_img = img.crop(bbox)
            avg_color = self._calculate_average_color(region_img)

            if expected_colors and region.name in expected_colors:
                expected = expected_colors[region.name]
                color_diff = sum(abs(a - e) for a, e in zip(avg_color, expected))
                similarity = 1.0 - (color_diff / (255 * 3))
                results[region.name] = ComparisonResult(
                    is_identical=color_diff < 30,
                    similarity_score=similarity,
                    pixel_diff_count=color_diff,
                    details={"average_color": avg_color, "expected_color": expected},
                )
            else:
                results[region.name] = ComparisonResult(
                    is_identical=True,
                    similarity_score=1.0,
                    pixel_diff_count=0,
                    details={"average_color": avg_color},
                )

        return results

    def _calculate_average_color(self, img: Image.Image) -> Tuple[int, int, int]:
        if HAS_NUMPY:
            arr = np.array(img)
            return tuple(arr.mean(axis=(0, 1)).astype(int))
        pixels = list(img.getdata())
        r = sum(p[0] for p in pixels) / len(pixels)
        g = sum(p[1] for p in pixels) / len(pixels)
        b = sum(p[2] for p in pixels) / len(pixels)
        return (int(r), int(g), int(b))


class VisualRegressionDetector:
    """Baseline-based visual regression detector (golden-image pattern).

    Inspired by loopepedition's ScreenshotTestHelper golden-image workflow:
    - First run saves the screenshot as baseline
    - Subsequent runs compare against baseline
    - Tolerance-configurable per-test
    """

    def __init__(self, baseline_dir: str = "tests/visual/baselines"):
        self.baseline_dir = Path(baseline_dir)
        self.baseline_dir.mkdir(parents=True, exist_ok=True)
        self.comparer = ScreenshotComparer()

    def check_regression(
        self,
        test_name: str,
        current_screenshot: str,
        tolerance: float = 0.05,
    ) -> ComparisonResult:
        """Check for visual regression against baseline.

        If baseline does not exist, creates it from current screenshot.
        """
        baseline_path = self.baseline_dir / f"{test_name}.png"

        if not baseline_path.exists():
            self._create_baseline(test_name, current_screenshot)
            return ComparisonResult(
                is_identical=True,
                similarity_score=1.0,
                pixel_diff_count=0,
                details={"message": "Baseline created"},
            )

        result = self.comparer.compare_screenshots(
            str(baseline_path),
            current_screenshot,
            mode=ComparisonMode.STRUCTURAL,
        )

        if result.similarity_score < (1.0 - tolerance):
            diff_path = self.comparer.output_dir / f"{test_name}_diff.png"
            self._save_diff(str(baseline_path), current_screenshot, str(diff_path))
            result.diff_image_path = str(diff_path)

        return result

    def _create_baseline(self, test_name: str, screenshot_path: str) -> None:
        baseline_path = self.baseline_dir / f"{test_name}.png"
        shutil.copy2(screenshot_path, baseline_path)

    def _save_diff(self, baseline_path: str, current_path: str, diff_path: str) -> None:
        baseline = Image.open(baseline_path)
        current = Image.open(current_path)
        if baseline.size != current.size:
            current = current.resize(baseline.size)
        diff = ImageChops.difference(baseline, current)
        diff_enhanced = diff.point(lambda x: x * 3)
        diff_enhanced.save(diff_path)
