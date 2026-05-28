"""Pytest fixtures for visual regression tests.

Provides xvfb detection, Godot binary resolution, output directories,
and baseline management. Adapted from stardrifter's visual test harness.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

# ─── Godot binary resolution ────────────────────────────────

_ENV_GODOT_BIN = os.environ.get("GODOT_BIN", "").strip()
_DEFAULT_GODOT_LOCAL = REPO_ROOT / "tools" / "godot" / "Godot_v4.6-stable_linux.x86_64"
_SYSTEM_GODOT = Path("/usr/local/bin/godot")


def _is_runnable(path: Path) -> bool:
    return path.is_file() and os.access(path, os.X_OK)


if _ENV_GODOT_BIN and _is_runnable(Path(_ENV_GODOT_BIN)):
    GODOT_BIN = Path(_ENV_GODOT_BIN)
elif _is_runnable(_SYSTEM_GODOT):
    GODOT_BIN = _SYSTEM_GODOT
elif _is_runnable(_DEFAULT_GODOT_LOCAL):
    GODOT_BIN = _DEFAULT_GODOT_LOCAL
else:
    GODOT_BIN = _SYSTEM_GODOT  # fallback, tests will skip


# ─── Fixtures ───────────────────────────────────────────────


@pytest.fixture(scope="session")
def godot_binary() -> Path:
    """Resolved Godot binary path."""
    return GODOT_BIN


@pytest.fixture(scope="session")
def godot_available() -> bool:
    """True if Godot binary is available."""
    return GODOT_BIN.exists()


@pytest.fixture(scope="session")
def xvfb_available() -> bool:
    """True if xvfb-run is available for headless rendering."""
    try:
        result = subprocess.run(
            ["which", "xvfb-run"], capture_output=True, text=True, timeout=5
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False


@pytest.fixture(scope="session")
def visual_output_dir(tmp_path_factory) -> Path:
    """Shared output directory for visual test artifacts."""
    return tmp_path_factory.mktemp("visual_tests")


@pytest.fixture(scope="session")
def baseline_dir() -> Path:
    """Directory for golden-image baselines."""
    path = REPO_ROOT / "tests" / "visual" / "baselines"
    path.mkdir(parents=True, exist_ok=True)
    return path
