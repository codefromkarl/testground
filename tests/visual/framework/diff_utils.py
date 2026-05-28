"""Pixel-diff utilities for comparing screenshots.

Adapted from stardrifter. Computes a diff ratio between two PNG images:
0.0 = identical, 1.0 = completely different.
"""

from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass
from pathlib import Path

try:
    from PIL import Image
    _HAS_PIL = True
except ImportError:
    _HAS_PIL = False


@dataclass
class DiffResult:
    """Result of comparing two images."""
    ratio: float  # 0.0 = identical, 1.0 = completely different
    total_pixels: int
    changed_pixels: int
    threshold: float
    passed: bool

    def __str__(self) -> str:
        status = "PASS" if self.passed else "FAIL"
        return (
            f"[{status}] diff_ratio={self.ratio:.4f} "
            f"(changed={self.changed_pixels}/{self.total_pixels}, "
            f"threshold={self.threshold})"
        )


def _load_png_pixels_pil(path: Path) -> list[tuple[int, int, int, int]]:
    """Load PNG pixels using PIL."""
    img = Image.open(path).convert("RGBA")
    if hasattr(img, "get_flattened_data"):
        return list(img.get_flattened_data())
    return list(img.getdata())


def _load_png_pixels_stdlib(path: Path) -> list[tuple[int, int, int, int]]:
    """Load PNG pixels using stdlib (fallback, simplified)."""
    data = path.read_bytes()
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError(f"Not a PNG file: {path}")

    chunks = []
    pos = 8
    width = height = 0
    bit_depth = color_type = 0
    while pos < len(data):
        length = struct.unpack(">I", data[pos : pos + 4])[0]
        chunk_type = data[pos + 4 : pos + 8]
        chunk_data = data[pos + 8 : pos + 8 + length]
        pos += 12 + length

        if chunk_type == b"IHDR":
            width = struct.unpack(">I", chunk_data[0:4])[0]
            height = struct.unpack(">I", chunk_data[4:8])[0]
            bit_depth = chunk_data[8]
            color_type = chunk_data[9]
        elif chunk_type == b"IDAT":
            chunks.append(chunk_data)
        elif chunk_type == b"IEND":
            break

    if not chunks:
        raise ValueError(f"No IDAT chunks in {path}")

    raw = zlib.decompress(b"".join(chunks))
    # Simplified: assume RGBA, 8-bit, filter=0 (None) per row
    if color_type == 6 and bit_depth == 8:
        stride = width * 4 + 1
        pixels = []
        for y in range(height):
            row_start = y * stride
            row_data = raw[row_start + 1 : row_start + 1 + width * 4]
            for x in range(width):
                offset = x * 4
                pixels.append((row_data[offset], row_data[offset + 1], row_data[offset + 2], row_data[offset + 3]))
        return pixels
    raise ValueError(f"Unsupported PNG format: color_type={color_type}, bit_depth={bit_depth}")


def load_pixels(path: Path) -> list[tuple[int, int, int, int]]:
    """Load PNG pixels, preferring PIL if available."""
    if _HAS_PIL:
        return _load_png_pixels_pil(path)
    return _load_png_pixels_stdlib(path)


def diff_ratio(
    path_a: Path,
    path_b: Path,
    *,
    pixel_threshold: int = 30,
) -> float:
    """Compute ratio of changed pixels between two images.

    Returns float in [0.0, 1.0] where 0.0 = identical.
    """
    pixels_a = load_pixels(path_a)
    pixels_b = load_pixels(path_b)

    if len(pixels_a) != len(pixels_b):
        return 1.0

    total = len(pixels_a)
    if total == 0:
        return 0.0

    changed = 0
    for pa, pb in zip(pixels_a, pixels_b):
        if (
            abs(pa[0] - pb[0]) > pixel_threshold
            or abs(pa[1] - pb[1]) > pixel_threshold
            or abs(pa[2] - pb[2]) > pixel_threshold
            or abs(pa[3] - pb[3]) > pixel_threshold
        ):
            changed += 1

    return changed / total


def build_diff_result(
    path_a: Path,
    path_b: Path,
    *,
    threshold: float = 0.01,
    pixel_threshold: int = 30,
) -> DiffResult:
    """Compare two images and return a structured DiffResult."""
    pixels_a = load_pixels(path_a)
    pixels_b = load_pixels(path_b)

    total = max(len(pixels_a), len(pixels_b))
    if total == 0 or len(pixels_a) != len(pixels_b):
        return DiffResult(
            ratio=1.0,
            total_pixels=total,
            changed_pixels=total,
            threshold=threshold,
            passed=True,
        )

    changed = 0
    for pa, pb in zip(pixels_a, pixels_b):
        if (
            abs(pa[0] - pb[0]) > pixel_threshold
            or abs(pa[1] - pb[1]) > pixel_threshold
            or abs(pa[2] - pb[2]) > pixel_threshold
            or abs(pa[3] - pb[3]) > pixel_threshold
        ):
            changed += 1

    ratio = changed / total
    return DiffResult(
        ratio=ratio,
        total_pixels=total,
        changed_pixels=changed,
        threshold=threshold,
        passed=ratio >= threshold,
    )
