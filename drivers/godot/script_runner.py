"""Godot script runner — adapted from stardrifter.

Provides subprocess-based execution of Godot test scripts via `--headless`
mode, complementing the async TCP-based GodotDriver. Useful for CI pipelines
and one-shot test validation.

Usage:
    from drivers.godot.script_runner import run_godot_script, GODOT_BIN

    result = run_godot_script("my_test_runner.gd", timeout_seconds=60)
    assert result.returncode == 0, result.stderr
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Sequence

import pytest

# Resolve repository root relative to this file
REPO_ROOT = Path(__file__).resolve().parents[2]

# ─── Godot binary discovery ─────────────────────────────────

_ENV_GODOT_BIN = os.environ.get("GODOT_BIN", "").strip()
_DEFAULT_GODOT_LOCAL = REPO_ROOT / "tools" / "godot" / "Godot_v4.6-stable_linux.x86_64"
_SYSTEM_GODOT = Path("/usr/local/bin/godot")


def _is_runnable_binary(path: Path) -> bool:
    return path.is_file() and os.access(path, os.X_OK)


if _ENV_GODOT_BIN and _is_runnable_binary(Path(_ENV_GODOT_BIN)):
    GODOT_BIN = Path(_ENV_GODOT_BIN)
elif _is_runnable_binary(_SYSTEM_GODOT):
    GODOT_BIN = _SYSTEM_GODOT
elif _is_runnable_binary(_DEFAULT_GODOT_LOCAL):
    GODOT_BIN = _DEFAULT_GODOT_LOCAL
else:
    GODOT_BIN = _SYSTEM_GODOT  # fallback; will fail gracefully


# ─── Core runner ────────────────────────────────────────────


def run_godot_script(
    script_path: str | Path,
    *,
    project_path: str | Path | None = None,
    extra_args: Sequence[str] | None = None,
    timeout_seconds: int | float = 120,
    env: dict[str, str] | None = None,
    headless: bool = True,
    capture: bool = True,
) -> subprocess.CompletedProcess[str]:
    """Run a Godot script via subprocess.

    Args:
        script_path: Path to the .gd script to execute.
        project_path: Godot project root (defaults to REPO_ROOT).
        extra_args: Additional Godot CLI arguments.
        timeout_seconds: Max seconds to wait.
        env: Extra environment variables merged into os.environ.
        headless: Add --headless flag (disable for visual tests needing renderer).
        capture: Capture stdout/stderr.

    Returns:
        CompletedProcess with returncode, stdout, stderr.

    Raises:
        pytest.skip: If Godot binary is not available (when called from tests).
    """
    if not GODOT_BIN.exists():
        pytest.skip(f"Godot runtime not available: {GODOT_BIN}")

    project = Path(project_path) if project_path else REPO_ROOT
    command = [str(GODOT_BIN), "--path", str(project)]

    if headless:
        command.append("--headless")
    if extra_args:
        command.extend(extra_args)

    command.extend(["--script", str(script_path)])

    return subprocess.run(
        command,
        cwd=REPO_ROOT,
        capture_output=capture,
        text=capture,
        check=False,
        timeout=timeout_seconds,
        env={**os.environ, **(env or {})},
    )


# ─── Utility helpers ────────────────────────────────────────


def _find_free_port() -> int:
    """Find an unused TCP port on localhost."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@contextmanager
def run_python_server(
    module: str = "gateway.main:app",
    host: str = "127.0.0.1",
    port: int = 0,
    timeout: float = 5.0,
) -> Iterator[str]:
    """Context manager that starts a Python server and yields its base URL.

    Useful for spawning the Gateway during Godot E2E tests.
    """
    if port == 0:
        port = _find_free_port()

    env = {
        **os.environ,
        "PYTHONPATH": str(REPO_ROOT),
    }

    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", module, "--host", host, "--port", str(port)],
        cwd=REPO_ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )

    # Wait for server to be ready
    start = time.time()
    ready = False
    while time.time() - start < timeout:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.settimeout(1.0)
                sock.connect((host, port))
                ready = True
                break
        except OSError:
            time.sleep(0.2)

    base_url = f"http://{host}:{port}"
    try:
        if not ready:
            raise RuntimeError(f"Server failed to start on {base_url}")
        yield base_url
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)


def normalize_godot_output(output: str) -> str:
    """Strip Godot engine banner lines from output."""
    lines = []
    for raw in output.splitlines():
        line = raw.strip()
        if not line or line.startswith("Godot Engine v"):
            continue
        lines.append(line)
    return "\n".join(lines)
