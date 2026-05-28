"""
Image recognition engine — adapted from stardrifter.

Supports template matching (OpenCV), color region detection, and
effect detection (glow, shield, particles).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

try:
    from PIL import Image
    HAS_PIL = True
except ImportError:
    HAS_PIL = False

try:
    import numpy as np
    HAS_NUMPY = True
except ImportError:
    HAS_NUMPY = False

try:
    import cv2
    HAS_CV2 = True
except ImportError:
    HAS_CV2 = False


@dataclass
class DetectionResult:
    """Result of an image detection operation."""
    found: bool
    confidence: float
    location: Optional[Tuple[int, int]] = None
    size: Optional[Tuple[int, int]] = None
    details: Optional[Dict[str, any]] = None


@dataclass
class ColorRange:
    """Color range for detection."""
    name: str
    lower: Tuple[int, int, int]
    upper: Tuple[int, int, int]


class ImageRecognizer:
    """Detect colors, effects, and regions in screenshots."""

    def __init__(self):
        if not HAS_PIL:
            raise ImportError("PIL is required. Install with: pip install Pillow")

    def detect_color_region(
        self,
        image_path: str,
        color_range: ColorRange,
        min_area: int = 100,
    ) -> DetectionResult:
        """Detect a color region in an image."""
        img = Image.open(image_path)

        if not HAS_NUMPY:
            return DetectionResult(found=False, confidence=0.0)

        arr = np.array(img)
        r, g, b = arr[:, :, 0], arr[:, :, 1], arr[:, :, 2]
        mask = (
            (r >= color_range.lower[0]) & (r <= color_range.upper[0])
            & (g >= color_range.lower[1]) & (g <= color_range.upper[1])
            & (b >= color_range.lower[2]) & (b <= color_range.upper[2])
        )
        pixel_count = int(np.sum(mask))

        if pixel_count >= min_area:
            y_coords, x_coords = np.where(mask)
            center_x = int(np.mean(x_coords))
            center_y = int(np.mean(y_coords))
            return DetectionResult(
                found=True,
                confidence=min(pixel_count / (img.width * img.height), 1.0),
                location=(center_x, center_y),
                details={"pixel_count": pixel_count, "color_range": color_range.name},
            )

        return DetectionResult(found=False, confidence=0.0)

    def detect_glow_effect(
        self,
        image_path: str,
        glow_color: Tuple[int, int, int] = (255, 200, 100),
        tolerance: int = 50,
    ) -> DetectionResult:
        """Detect glow/bloom effect."""
        color_range = ColorRange(
            name="glow",
            lower=(
                max(0, glow_color[0] - tolerance),
                max(0, glow_color[1] - tolerance),
                max(0, glow_color[2] - tolerance),
            ),
            upper=(
                min(255, glow_color[0] + tolerance),
                min(255, glow_color[1] + tolerance),
                min(255, glow_color[2] + tolerance),
            ),
        )
        return self.detect_color_region(image_path, color_range, min_area=50)

    def detect_shield_effect(
        self,
        image_path: str,
        shield_color: Tuple[int, int, int] = (100, 150, 255),
        tolerance: int = 50,
    ) -> DetectionResult:
        """Detect shield/blue bubble effect."""
        color_range = ColorRange(
            name="shield",
            lower=(
                max(0, shield_color[0] - tolerance),
                max(0, shield_color[1] - tolerance),
                max(0, shield_color[2] - tolerance),
            ),
            upper=(
                min(255, shield_color[0] + tolerance),
                min(255, shield_color[1] + tolerance),
                min(255, shield_color[2] + tolerance),
            ),
        )
        return self.detect_color_region(image_path, color_range, min_area=100)

    def detect_explosion_effect(self, image_path: str) -> DetectionResult:
        """Detect explosion/orange-red-yellow effect."""
        explosion_colors = [
            ColorRange("orange", (200, 100, 0), (255, 200, 100)),
            ColorRange("red", (200, 0, 0), (255, 100, 100)),
            ColorRange("yellow", (200, 200, 0), (255, 255, 100)),
        ]
        best = DetectionResult(found=False, confidence=0.0)
        for color_range in explosion_colors:
            result = self.detect_color_region(image_path, color_range, min_area=50)
            if result.found and result.confidence > best.confidence:
                best = result
        return best

    def detect_particle_effect(
        self,
        image_path: str,
        particle_color: Tuple[int, int, int] = (255, 255, 255),
        tolerance: int = 30,
    ) -> DetectionResult:
        """Detect bright particle/sparkle effect."""
        if not HAS_NUMPY:
            return DetectionResult(found=False, confidence=0.0)

        img = Image.open(image_path)
        arr = np.array(img)
        r, g, b = arr[:, :, 0], arr[:, :, 1], arr[:, :, 2]
        brightness = (r.astype(int) + g.astype(int) + b.astype(int)) / 3
        bright_mask = brightness > 200
        bright_pixels = int(np.sum(bright_mask))

        if bright_pixels > 10:
            y_coords, x_coords = np.where(bright_mask)
            center_x = int(np.mean(x_coords))
            center_y = int(np.mean(y_coords))
            return DetectionResult(
                found=True,
                confidence=min(bright_pixels / 1000, 1.0),
                location=(center_x, center_y),
                details={"bright_pixels": bright_pixels},
            )

        return DetectionResult(found=False, confidence=0.0)


class TemplateMatcher:
    """OpenCV-based template matcher for finding UI elements in screenshots."""

    def __init__(self):
        if not HAS_CV2:
            raise ImportError("OpenCV is required. Install with: pip install opencv-python")

    def match_template(
        self,
        image_path: str,
        template_path: str,
        threshold: float = 0.8,
        method: int = None,
    ) -> DetectionResult:
        """Match a template within an image."""
        if method is None:
            method = cv2.TM_CCOEFF_NORMED

        img = cv2.imread(image_path)
        template = cv2.imread(template_path)

        if img is None or template is None:
            return DetectionResult(
                found=False, confidence=0.0, details={"error": "Failed to load images"}
            )

        result = cv2.matchTemplate(img, template, method)
        min_val, max_val, min_loc, max_loc = cv2.minMaxLoc(result)

        if method in (cv2.TM_SQDIFF, cv2.TM_SQDIFF_NORMED):
            match_val = min_val
            match_loc = min_loc
            is_match = match_val <= (1 - threshold)
        else:
            match_val = max_val
            match_loc = max_loc
            is_match = match_val >= threshold

        if is_match:
            h, w = template.shape[:2]
            center_x = match_loc[0] + w // 2
            center_y = match_loc[1] + h // 2
            return DetectionResult(
                found=True,
                confidence=float(match_val),
                location=(center_x, center_y),
                size=(w, h),
                details={"match_value": float(match_val), "match_location": match_loc},
            )

        return DetectionResult(
            found=False, confidence=float(match_val), details={"match_value": float(match_val)}
        )

    def match_multiple_templates(
        self,
        image_path: str,
        template_path: str,
        threshold: float = 0.8,
        max_matches: int = 10,
    ) -> List[DetectionResult]:
        """Find multiple instances of a template."""
        img = cv2.imread(image_path)
        template = cv2.imread(template_path)

        if img is None or template is None:
            return []

        h, w = template.shape[:2]
        result = cv2.matchTemplate(img, template, cv2.TM_CCOEFF_NORMED)
        locations = np.where(result >= threshold)

        matches = []
        for pt in zip(*locations[::-1]):
            if len(matches) >= max_matches:
                break

            # Deduplicate overlapping matches
            is_overlap = False
            for existing in matches:
                dist = np.sqrt((pt[0] - existing.location[0]) ** 2 + (pt[1] - existing.location[1]) ** 2)
                if dist < min(w, h) / 2:
                    is_overlap = True
                    break

            if not is_overlap:
                center_x = pt[0] + w // 2
                center_y = pt[1] + h // 2
                confidence = float(result[pt[1], pt[0]])
                matches.append(
                    DetectionResult(
                        found=True,
                        confidence=confidence,
                        location=(center_x, center_y),
                        size=(w, h),
                        details={"match_value": confidence},
                    )
                )

        return matches
