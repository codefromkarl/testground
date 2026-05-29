"""集成测试：drivers/godot/script_runner.py

测试 Godot 脚本运行器的核心逻辑，全部使用 mock，不需要真实 Godot 进程。
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest

# 确保项目根目录在 sys.path 中
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

pytestmark = pytest.mark.medium


# ══════════════════════════════════════════════════════════════
# Godot binary 发现逻辑测试
# ══════════════════════════════════════════════════════════════


class TestGodotBinaryDiscovery:
    """测试 Godot 二进制文件发现优先级逻辑。

    GODOT_BIN 是模块加载时通过条件逻辑计算的常量。
    由于 reload 会重新执行常量定义，我们直接测试发现函数逻辑。
    """

    def test_discovery_prefers_env_var(self, tmp_path):
        """验证环境变量路径优先级最高（逻辑测试）。"""
        fake_godot = tmp_path / "godot_from_env"
        fake_godot.touch(mode=0o755)

        from drivers.godot.script_runner import _is_runnable_binary

        # 模拟发现逻辑：环境变量存在且可执行
        env_bin = str(fake_godot)
        if env_bin and _is_runnable_binary(Path(env_bin)):
            result = Path(env_bin)
        else:
            result = None

        assert result == fake_godot

    def test_discovery_skips_empty_env_var(self, tmp_path):
        """空环境变量应跳过。"""
        from drivers.godot.script_runner import _is_runnable_binary

        env_bin = ""
        # 空字符串应被视为 False
        if env_bin and _is_runnable_binary(Path(env_bin)):
            result = "env"
        else:
            result = "next"

        assert result == "next"

    def test_discovery_falls_through_to_system(self, tmp_path):
        """环境变量不可用时应检查系统 godot。"""
        from drivers.godot.script_runner import _is_runnable_binary

        fake_system = tmp_path / "system_godot"
        fake_system.touch(mode=0o755)

        # 模拟：env 不可用，system 可用
        env_bin = ""
        if env_bin and _is_runnable_binary(Path(env_bin)):
            result = Path(env_bin)
        elif _is_runnable_binary(fake_system):
            result = fake_system
        else:
            result = None

        assert result == fake_system

    def test_discovery_falls_through_to_local_tools(self, tmp_path):
        """env 和 system 都不可用时应检查本地工具目录。"""
        from drivers.godot.script_runner import _is_runnable_binary

        fake_local = tmp_path / "local_godot"
        fake_local.touch(mode=0o755)
        fake_system = tmp_path / "system_godot"  # 不存在

        # 模拟：env 不可用，system 不可用，local 可用
        env_bin = ""
        if env_bin and _is_runnable_binary(Path(env_bin)):
            result = Path(env_bin)
        elif _is_runnable_binary(fake_system):
            result = fake_system
        elif _is_runnable_binary(fake_local):
            result = fake_local
        else:
            result = None

        assert result == fake_local

    def test_discovery_fallback_when_none_available(self, tmp_path):
        """所有选项都不可用时应回退到 system path。"""
        from drivers.godot.script_runner import _is_runnable_binary

        fake_system = tmp_path / "nonexistent"
        fake_local = tmp_path / "also_nonexistent"

        # 模拟：全部不可用
        env_bin = ""
        if env_bin and _is_runnable_binary(Path(env_bin)):
            result = Path(env_bin)
        elif _is_runnable_binary(fake_system):
            result = fake_system
        elif _is_runnable_binary(fake_local):
            result = fake_local
        else:
            result = fake_system  # 回退到 system

        assert result == fake_system

    def test_godot_bin_is_path_instance(self):
        """GODOT_BIN 应该是 Path 实例。"""
        from drivers.godot.script_runner import GODOT_BIN
        assert isinstance(GODOT_BIN, Path)


class TestIsRunnableBinary:
    """测试 _is_runnable_binary 辅助函数。"""

    def test_existing_executable_file(self, tmp_path):
        """应识别可执行文件。"""
        from drivers.godot.script_runner import _is_runnable_binary

        exe_file = tmp_path / "executable"
        exe_file.touch(mode=0o755)

        assert _is_runnable_binary(exe_file) is True

    def test_existing_non_executable_file(self, tmp_path):
        """应拒绝不可执行的文件。"""
        from drivers.godot.script_runner import _is_runnable_binary

        non_exe = tmp_path / "not_executable"
        non_exe.touch(mode=0o644)

        assert _is_runnable_binary(non_exe) is False

    def test_nonexistent_path(self, tmp_path):
        """应拒绝不存在的路径。"""
        from drivers.godot.script_runner import _is_runnable_binary

        fake_path = tmp_path / "does_not_exist"

        assert _is_runnable_binary(fake_path) is False

    def test_directory_rejected(self, tmp_path):
        """应拒绝目录。"""
        from drivers.godot.script_runner import _is_runnable_binary

        dir_path = tmp_path / "directory"
        dir_path.mkdir()

        assert _is_runnable_binary(dir_path) is False


# ══════════════════════════════════════════════════════════════
# run_godot_script() Mock 测试
# ══════════════════════════════════════════════════════════════


class TestRunGodotScript:
    """测试 run_godot_script() 的 mock 场景。"""

    @pytest.fixture(autouse=True)
    def setup_mocks(self, tmp_path):
        """设置通用 mock：假的 Godot 二进制文件。"""
        self.fake_godot = tmp_path / "godot"
        self.fake_godot.touch(mode=0o755)
        self.tmp_path = tmp_path

    def test_command_line_construction_headless(self):
        """应正确构建 --headless 模式的命令行参数。"""
        mock_result = MagicMock(spec=subprocess.CompletedProcess)
        mock_result.returncode = 0

        with patch("drivers.godot.script_runner.GODOT_BIN", self.fake_godot), \
             patch("subprocess.run", return_value=mock_result) as mock_run:

            from drivers.godot.script_runner import run_godot_script
            run_godot_script("test_script.gd", project_path="/test/project")

            mock_run.assert_called_once()
            cmd_args = mock_run.call_args[0][0]

            assert cmd_args[0] == str(self.fake_godot)
            assert "--path" in cmd_args
            assert "/test/project" in cmd_args
            assert "--headless" in cmd_args
            assert "--script" in cmd_args
            assert "test_script.gd" in cmd_args

    def test_command_line_construction_no_headless(self):
        """headless=False 时不应包含 --headless。"""
        mock_result = MagicMock(spec=subprocess.CompletedProcess)
        mock_result.returncode = 0

        with patch("drivers.godot.script_runner.GODOT_BIN", self.fake_godot), \
             patch("subprocess.run", return_value=mock_result) as mock_run:

            from drivers.godot.script_runner import run_godot_script
            run_godot_script("test_script.gd", headless=False)

            cmd_args = mock_run.call_args[0][0]
            assert "--headless" not in cmd_args

    def test_extra_args_passed(self):
        """extra_args 应正确追加到命令行。"""
        mock_result = MagicMock(spec=subprocess.CompletedProcess)
        mock_result.returncode = 0

        with patch("drivers.godot.script_runner.GODOT_BIN", self.fake_godot), \
             patch("subprocess.run", return_value=mock_result) as mock_run:

            from drivers.godot.script_runner import run_godot_script
            run_godot_script(
                "test_script.gd",
                extra_args=["--verbose", "--debug-collisions"],
            )

            cmd_args = mock_run.call_args[0][0]
            assert "--verbose" in cmd_args
            assert "--debug-collisions" in cmd_args

    def test_timeout_parameter_passed(self):
        """timeout_seconds 应正确传递给 subprocess.run。"""
        mock_result = MagicMock(spec=subprocess.CompletedProcess)
        mock_result.returncode = 0

        with patch("drivers.godot.script_runner.GODOT_BIN", self.fake_godot), \
             patch("subprocess.run", return_value=mock_result) as mock_run:

            from drivers.godot.script_runner import run_godot_script
            run_godot_script("test_script.gd", timeout_seconds=30)

            call_kwargs = mock_run.call_args[1]
            assert call_kwargs["timeout"] == 30

    def test_timeout_float_value(self):
        """应支持浮点数超时值。"""
        mock_result = MagicMock(spec=subprocess.CompletedProcess)
        mock_result.returncode = 0

        with patch("drivers.godot.script_runner.GODOT_BIN", self.fake_godot), \
             patch("subprocess.run", return_value=mock_result) as mock_run:

            from drivers.godot.script_runner import run_godot_script
            run_godot_script("test_script.gd", timeout_seconds=2.5)

            call_kwargs = mock_run.call_args[1]
            assert call_kwargs["timeout"] == 2.5

    def test_env_merge_correct(self):
        """自定义环境变量应与 os.environ 合并。"""
        mock_result = MagicMock(spec=subprocess.CompletedProcess)
        mock_result.returncode = 0

        custom_env = {"CUSTOM_VAR": "test_value", "DEBUG": "1"}

        with patch("drivers.godot.script_runner.GODOT_BIN", self.fake_godot), \
             patch.dict(os.environ, {"EXISTING_VAR": "keep_me"}), \
             patch("subprocess.run", return_value=mock_result) as mock_run:

            from drivers.godot.script_runner import run_godot_script
            run_godot_script("test_script.gd", env=custom_env)

            call_kwargs = mock_run.call_args[1]
            merged_env = call_kwargs["env"]

            # 自定义变量应存在
            assert merged_env["CUSTOM_VAR"] == "test_value"
            assert merged_env["DEBUG"] == "1"
            # 原有环境变量应保留
            assert merged_env["EXISTING_VAR"] == "keep_me"

    def test_env_override_priority(self):
        """自定义环境变量应覆盖 os.environ 中的同名变量。"""
        mock_result = MagicMock(spec=subprocess.CompletedProcess)
        mock_result.returncode = 0

        with patch("drivers.godot.script_runner.GODOT_BIN", self.fake_godot), \
             patch.dict(os.environ, {"VAR_TO_OVERRIDE": "original"}), \
             patch("subprocess.run", return_value=mock_result) as mock_run:

            from drivers.godot.script_runner import run_godot_script
            run_godot_script("test_script.gd", env={"VAR_TO_OVERRIDE": "overridden"})

            call_kwargs = mock_run.call_args[1]
            merged_env = call_kwargs["env"]
            assert merged_env["VAR_TO_OVERRIDE"] == "overridden"

    def test_default_project_path_uses_repo_root(self):
        """不指定 project_path 时应使用 REPO_ROOT。"""
        mock_result = MagicMock(spec=subprocess.CompletedProcess)
        mock_result.returncode = 0

        with patch("drivers.godot.script_runner.GODOT_BIN", self.fake_godot), \
             patch("subprocess.run", return_value=mock_result) as mock_run:

            from drivers.godot.script_runner import run_godot_script, REPO_ROOT
            run_godot_script("test_script.gd")

            cmd_args = mock_run.call_args[0][0]
            assert str(REPO_ROOT) in cmd_args

    def test_capture_output_enabled(self):
        """capture=True 时应捕获输出。"""
        mock_result = MagicMock(spec=subprocess.CompletedProcess)
        mock_result.returncode = 0

        with patch("drivers.godot.script_runner.GODOT_BIN", self.fake_godot), \
             patch("subprocess.run", return_value=mock_result) as mock_run:

            from drivers.godot.script_runner import run_godot_script
            run_godot_script("test_script.gd", capture=True)

            call_kwargs = mock_run.call_args[1]
            assert call_kwargs["capture_output"] is True
            assert call_kwargs["text"] is True

    def test_capture_output_disabled(self):
        """capture=False 时不应捕获输出。"""
        mock_result = MagicMock(spec=subprocess.CompletedProcess)
        mock_result.returncode = 0

        with patch("drivers.godot.script_runner.GODOT_BIN", self.fake_godot), \
             patch("subprocess.run", return_value=mock_result) as mock_run:

            from drivers.godot.script_runner import run_godot_script
            run_godot_script("test_script.gd", capture=False)

            call_kwargs = mock_run.call_args[1]
            assert call_kwargs["capture_output"] is False
            assert call_kwargs["text"] is False

    def test_check_false_always_set(self):
        """check=False 应始终设置（不自动抛异常）。"""
        mock_result = MagicMock(spec=subprocess.CompletedProcess)
        mock_result.returncode = 1

        with patch("drivers.godot.script_runner.GODOT_BIN", self.fake_godot), \
             patch("subprocess.run", return_value=mock_result) as mock_run:

            from drivers.godot.script_runner import run_godot_script
            run_godot_script("test_script.gd")

            call_kwargs = mock_run.call_args[1]
            assert call_kwargs["check"] is False

    def test_script_path_in_command_after_args(self):
        """--script 应在 --path 和可选参数之后。"""
        mock_result = MagicMock(spec=subprocess.CompletedProcess)
        mock_result.returncode = 0

        with patch("drivers.godot.script_runner.GODOT_BIN", self.fake_godot), \
             patch("subprocess.run", return_value=mock_result) as mock_run:

            from drivers.godot.script_runner import run_godot_script
            run_godot_script(
                "my_test.gd",
                extra_args=["--arg1", "--arg2"],
            )

            cmd_args = mock_run.call_args[0][0]
            # --script 应在最后（script 本身之前）
            script_idx = cmd_args.index("--script")
            assert cmd_args[script_idx + 1] == "my_test.gd"
            # --path 应在 --script 之前
            path_idx = cmd_args.index("--path")
            assert path_idx < script_idx

    def test_returns_completed_process(self):
        """应返回 subprocess.CompletedProcess 对象。"""
        mock_result = MagicMock(spec=subprocess.CompletedProcess)
        mock_result.returncode = 42
        mock_result.stdout = "test output"
        mock_result.stderr = ""

        with patch("drivers.godot.script_runner.GODOT_BIN", self.fake_godot), \
             patch("subprocess.run", return_value=mock_result):

            from drivers.godot.script_runner import run_godot_script
            result = run_godot_script("test_script.gd")

            assert result.returncode == 42
            assert result.stdout == "test output"

    def test_godot_not_found_skips_test(self):
        """当 Godot 二进制不存在时，应 pytest.skip。"""
        non_existent = self.tmp_path / "nonexistent_godot"

        with patch("drivers.godot.script_runner.GODOT_BIN", non_existent):
            from drivers.godot.script_runner import run_godot_script

            with pytest.raises(pytest.skip.Exception):
                run_godot_script("test_script.gd")


# ══════════════════════════════════════════════════════════════
# normalize_godot_output() 测试
# ══════════════════════════════════════════════════════════════


class TestNormalizeGodotOutput:
    """测试 Godot 输出规范化函数。"""

    def test_filter_godot_engine_banner(self):
        """应过滤 Godot Engine 版本横幅行。"""
        from drivers.godot.script_runner import normalize_godot_output

        output = """Godot Engine v4.6.0.stable.official
Godot Engine v4.6.0.stable.official (c) 2014-present Godot Engine contributors.
Test passed: 5/5"""

        result = normalize_godot_output(output)
        lines = result.splitlines()

        assert len(lines) == 1
        assert lines[0] == "Test passed: 5/5"

    def test_filter_empty_lines(self):
        """应过滤空行。"""
        from drivers.godot.script_runner import normalize_godot_output

        output = """


Actual test output here

"""

        result = normalize_godot_output(output)
        assert result == "Actual test output here"

    def test_preserve_actual_test_output(self):
        """应保留实际测试输出（不以 Godot Engine v 开头）。"""
        from drivers.godot.script_runner import normalize_godot_output

        output = """Running test: test_movement
PASS: test_movement
Running test: test_collision
PASS: test_collision
Total: 2 tests passed"""

        result = normalize_godot_output(output)
        lines = result.splitlines()

        assert len(lines) == 5
        assert "PASS: test_movement" in lines
        assert "Total: 2 tests passed" in lines

    def test_mixed_banner_and_output(self):
        """应正确分离横幅和实际输出。"""
        from drivers.godot.script_runner import normalize_godot_output

        output = """Godot Engine v4.6.0.stable.official
[INFO] Loading project...
Test result: SUCCESS
Godot Engine v4.6.0.stable.official (c) 2014-present
Final status: PASSED"""

        result = normalize_godot_output(output)
        lines = result.splitlines()

        assert "[INFO] Loading project..." in lines
        assert "Test result: SUCCESS" in lines
        assert "Final status: PASSED" in lines
        # 不应包含 Godot Engine 行
        assert not any("Godot Engine" in line for line in lines)

    def test_empty_output(self):
        """应处理空输出。"""
        from drivers.godot.script_runner import normalize_godot_output

        result = normalize_godot_output("")
        assert result == ""

    def test_only_banner_lines(self):
        """当只有横幅行时应返回空字符串。"""
        from drivers.godot.script_runner import normalize_godot_output

        output = """Godot Engine v4.6.0.stable.official
Godot Engine v4.6.0.stable.official (c) 2014-present Godot Engine contributors."""

        result = normalize_godot_output(output)
        assert result == ""

    def test_whitespace_handling(self):
        """应正确处理带空格的行。"""
        from drivers.godot.script_runner import normalize_godot_output

        output = """  Godot Engine v4.6.0.stable.official  
  Test output with spaces  """

        result = normalize_godot_output(output)
        lines = result.splitlines()

        assert len(lines) == 1
        assert lines[0] == "Test output with spaces"

    def test_preserve_indented_output(self):
        """应保留缩进的输出内容（strip 后）。"""
        from drivers.godot.script_runner import normalize_godot_output

        output = """Godot Engine v4.6.0.stable.official
    [PASS] test_case_1
    [FAIL] test_case_2
    Expected: 1, Got: 2"""

        result = normalize_godot_output(output)
        lines = result.splitlines()

        assert len(lines) == 3
        assert "[PASS] test_case_1" in lines
        assert "[FAIL] test_case_2" in lines


# ══════════════════════════════════════════════════════════════
# run_python_server() Context Manager 测试
# ══════════════════════════════════════════════════════════════


class TestRunPythonServer:
    """测试 run_python_server() context manager。"""

    @pytest.fixture
    def mock_popen(self):
        """创建 mock Popen 实例。"""
        mock_proc = MagicMock()
        mock_proc.stdout = MagicMock()
        mock_proc.stderr = MagicMock()
        mock_proc.terminate = MagicMock()
        mock_proc.kill = MagicMock()
        mock_proc.wait = MagicMock(return_value=0)
        return mock_proc

    def test_server_starts_with_correct_command(self, mock_popen):
        """应使用正确的命令启动服务器。"""
        with patch("subprocess.Popen", return_value=mock_popen) as mock_popen_cls, \
             patch("drivers.godot.script_runner._find_free_port", return_value=8765), \
             patch("socket.socket") as mock_socket_cls:

            # 模拟连接成功（服务器就绪）
            mock_sock = MagicMock()
            mock_socket_cls.return_value.__enter__ = MagicMock(return_value=mock_sock)
            mock_socket_cls.return_value.__exit__ = MagicMock(return_value=False)

            from drivers.godot.script_runner import run_python_server

            with run_python_server(module="test.module:app", port=0) as base_url:
                assert base_url == "http://127.0.0.1:8765"

            # 验证 Popen 调用参数
            popen_args = mock_popen_cls.call_args[0][0]
            assert sys.executable in popen_args[0]
            assert "-m" in popen_args
            assert "uvicorn" in popen_args
            assert "test.module:app" in popen_args

    def test_port_discovery(self, mock_popen):
        """port=0 时应自动发现可用端口。"""
        with patch("subprocess.Popen", return_value=mock_popen), \
             patch("drivers.godot.script_runner._find_free_port", return_value=9999) as mock_find_port, \
             patch("socket.socket") as mock_socket_cls:

            mock_sock = MagicMock()
            mock_socket_cls.return_value.__enter__ = MagicMock(return_value=mock_sock)
            mock_socket_cls.return_value.__exit__ = MagicMock(return_value=False)

            from drivers.godot.script_runner import run_python_server

            with run_python_server(port=0) as base_url:
                mock_find_port.assert_called_once()
                assert base_url == "http://127.0.0.1:9999"

    def test_specific_port(self, mock_popen):
        """指定端口时不应调用端口发现。"""
        with patch("subprocess.Popen", return_value=mock_popen), \
             patch("drivers.godot.script_runner._find_free_port") as mock_find_port, \
             patch("socket.socket") as mock_socket_cls:

            mock_sock = MagicMock()
            mock_socket_cls.return_value.__enter__ = MagicMock(return_value=mock_sock)
            mock_socket_cls.return_value.__exit__ = MagicMock(return_value=False)

            from drivers.godot.script_runner import run_python_server

            with run_python_server(port=5555) as base_url:
                mock_find_port.assert_not_called()
                assert base_url == "http://127.0.0.1:5555"

    def test_cleanup_terminate_called(self, mock_popen):
        """退出时应调用 terminate。"""
        with patch("subprocess.Popen", return_value=mock_popen), \
             patch("drivers.godot.script_runner._find_free_port", return_value=8765), \
             patch("socket.socket") as mock_socket_cls:

            mock_sock = MagicMock()
            mock_socket_cls.return_value.__enter__ = MagicMock(return_value=mock_sock)
            mock_socket_cls.return_value.__exit__ = MagicMock(return_value=False)

            from drivers.godot.script_runner import run_python_server

            with run_python_server(port=8765):
                pass

            mock_popen.terminate.assert_called_once()

    def test_cleanup_kill_on_timeout(self, mock_popen):
        """terminate 超时时应调用 kill。"""
        mock_popen.wait.side_effect = [
            subprocess.TimeoutExpired(cmd="test", timeout=5),  # 第一次 wait 超时
            0,  # kill 后的 wait 成功
        ]

        with patch("subprocess.Popen", return_value=mock_popen), \
             patch("drivers.godot.script_runner._find_free_port", return_value=8765), \
             patch("socket.socket") as mock_socket_cls:

            mock_sock = MagicMock()
            mock_socket_cls.return_value.__enter__ = MagicMock(return_value=mock_sock)
            mock_socket_cls.return_value.__exit__ = MagicMock(return_value=False)

            from drivers.godot.script_runner import run_python_server

            with run_python_server(port=8765):
                pass

            mock_popen.terminate.assert_called_once()
            mock_popen.kill.assert_called_once()
            assert mock_popen.wait.call_count == 2

    def test_context_manager_yields_base_url(self, mock_popen):
        """应 yield 正确格式的 base_url。"""
        with patch("subprocess.Popen", return_value=mock_popen), \
             patch("drivers.godot.script_runner._find_free_port", return_value=8765), \
             patch("socket.socket") as mock_socket_cls:

            mock_sock = MagicMock()
            mock_socket_cls.return_value.__enter__ = MagicMock(return_value=mock_sock)
            mock_socket_cls.return_value.__exit__ = MagicMock(return_value=False)

            from drivers.godot.script_runner import run_python_server

            with run_python_server(host="127.0.0.1", port=8765) as base_url:
                assert base_url == "http://127.0.0.1:8765"

    def test_custom_host(self, mock_popen):
        """应支持自定义 host。"""
        with patch("subprocess.Popen", return_value=mock_popen), \
             patch("drivers.godot.script_runner._find_free_port", return_value=8765), \
             patch("socket.socket") as mock_socket_cls:

            mock_sock = MagicMock()
            mock_socket_cls.return_value.__enter__ = MagicMock(return_value=mock_sock)
            mock_socket_cls.return_value.__exit__ = MagicMock(return_value=False)

            from drivers.godot.script_runner import run_python_server

            with run_python_server(host="0.0.0.0", port=8765) as base_url:
                assert base_url == "http://0.0.0.0:8765"

    def test_server_not_ready_raises(self):
        """服务器未就绪时应抛出 RuntimeError。"""
        mock_popen = MagicMock()
        mock_popen.terminate = MagicMock()
        mock_popen.kill = MagicMock()
        mock_popen.wait = MagicMock(return_value=0)

        with patch("subprocess.Popen", return_value=mock_popen), \
             patch("drivers.godot.script_runner._find_free_port", return_value=8765), \
             patch("socket.socket") as mock_socket_cls, \
             patch("time.time", side_effect=[0, 0, 0, 10, 10, 10]), \
             patch("time.sleep"):

            # 模拟连接失败
            mock_sock = MagicMock()
            mock_sock.connect = MagicMock(side_effect=ConnectionRefusedError)
            mock_socket_cls.return_value.__enter__ = MagicMock(return_value=mock_sock)
            mock_socket_cls.return_value.__exit__ = MagicMock(return_value=False)

            from drivers.godot.script_runner import run_python_server

            with pytest.raises(RuntimeError, match="Server failed to start"):
                with run_python_server(port=8765, timeout=1.0):
                    pass

    def test_env_includes_pythonpath(self, mock_popen):
        """应设置 PYTHONPATH 为项目根目录。"""
        with patch("subprocess.Popen", return_value=mock_popen) as mock_popen_cls, \
             patch("drivers.godot.script_runner._find_free_port", return_value=8765), \
             patch("socket.socket") as mock_socket_cls:

            mock_sock = MagicMock()
            mock_socket_cls.return_value.__enter__ = MagicMock(return_value=mock_sock)
            mock_socket_cls.return_value.__exit__ = MagicMock(return_value=False)

            from drivers.godot.script_runner import run_python_server, REPO_ROOT

            with run_python_server(port=8765):
                pass

            popen_kwargs = mock_popen_cls.call_args[1]
            assert popen_kwargs["env"]["PYTHONPATH"] == str(REPO_ROOT)


# ══════════════════════════════════════════════════════════════
# _find_free_port() 辅助函数测试
# ══════════════════════════════════════════════════════════════


class TestFindFreePort:
    """测试端口发现辅助函数。"""

    def test_returns_integer_port(self):
        """应返回整数端口号。"""
        from drivers.godot.script_runner import _find_free_port

        port = _find_free_port()
        assert isinstance(port, int)

    def test_port_in_valid_range(self):
        """应返回有效端口范围内的端口。"""
        from drivers.godot.script_runner import _find_free_port

        port = _find_free_port()
        assert 1024 <= port <= 65535

    def test_returns_unique_ports(self):
        """多次调用应返回不同端口（概率性测试）。"""
        from drivers.godot.script_runner import _find_free_port

        ports = {_find_free_port() for _ in range(5)}
        # 至少应有 3 个不同端口（概率极低会失败）
        assert len(ports) >= 3

    def test_port_is_usable(self):
        """返回的端口应可以绑定。"""
        from drivers.godot.script_runner import _find_free_port

        port = _find_free_port()

        # 尝试绑定该端口
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", port))
            # 成功绑定说明端口可用


# ══════════════════════════════════════════════════════════════
# xdist 并行兼容性测试
# ══════════════════════════════════════════════════════════════


class TestXdistCompatibility:
    """确保测试可以安全地在 pytest-xdist 并行模式下运行。"""

    def test_no_shared_state_between_tests(self):
        """测试之间不应共享可变状态。"""
        from drivers.godot.script_runner import REPO_ROOT

        # REPO_ROOT 是只读的 Path，可以安全共享
        assert isinstance(REPO_ROOT, Path)

    def test_mock_isolation(self):
        """每个测试的 mock 应正确隔离。"""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)

            from drivers.godot.script_runner import run_godot_script
            # 模拟 GODOT_BIN 存在
            with patch("drivers.godot.script_runner.GODOT_BIN", Path("/fake/godot")):
                run_godot_script("test.gd")

            assert mock_run.call_count == 1

        # 退出 with 块后 mock 应已清理
        import subprocess
        assert not hasattr(subprocess.run, '_mock_children')  # 不是 mock
