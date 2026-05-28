"""Visual testing framework core modules."""

from .screenshot import ComparisonMode, ComparisonResult, RegionOfInterest, ScreenshotComparer, VisualRegressionDetector
from .image_recognition import ColorRange, DetectionResult, ImageRecognizer, TemplateMatcher
from .visual_assertions import TestStatus, VisualTestResult, VisualAssertions
from .diff_utils import DiffResult, build_diff_result, diff_ratio, load_pixels

__all__ = [
    "ComparisonMode",
    "ComparisonResult",
    "RegionOfInterest",
    "ScreenshotComparer",
    "VisualRegressionDetector",
    "ColorRange",
    "DetectionResult",
    "ImageRecognizer",
    "TemplateMatcher",
    "TestStatus",
    "VisualTestResult",
    "VisualAssertions",
    "DiffResult",
    "build_diff_result",
    "diff_ratio",
    "load_pixels",
]
