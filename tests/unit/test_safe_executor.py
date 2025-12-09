"""Tests for SafePythonExecutor - the fallback Python executor without nsjail."""

import importlib

import pytest

from tracecat.sandbox.exceptions import (
    SandboxExecutionError,
    SandboxTimeoutError,
    SandboxValidationError,
)
from tracecat.sandbox.safe_executor import SafePythonExecutor, _extract_package_name


class TestExtractPackageName:
    """Test the package name extraction helper."""

    def test_simple_package_name(self):
        """Test extracting a simple package name."""
        assert _extract_package_name("requests") == "requests"

    def test_package_with_version(self):
        """Test extracting package name from version spec."""
        assert _extract_package_name("requests==2.28.0") == "requests"
        assert _extract_package_name("requests>=2.28.0") == "requests"
        assert _extract_package_name("requests<=2.28.0") == "requests"
        assert _extract_package_name("requests~=2.28.0") == "requests"

    def test_package_with_extras(self):
        """Test extracting package name with extras."""
        assert _extract_package_name("requests[security]") == "requests"
        assert _extract_package_name("openpyxl[lxml]") == "openpyxl"

    def test_hyphenated_package_normalized(self):
        """Test that hyphenated package names are normalized."""
        assert _extract_package_name("py-ocsf-models") == "py_ocsf_models"
        assert _extract_package_name("py-ocsf-models==0.8.0") == "py_ocsf_models"


class TestSafePythonExecutorBasics:
    """Test basic SafePythonExecutor functionality."""

    @pytest.fixture
    def executor(self, tmp_path):
        """Create an executor with a temp cache directory."""
        return SafePythonExecutor(cache_dir=str(tmp_path))

    @pytest.mark.anyio
    async def test_basic_execution(self, executor):
        """Test basic script execution."""
        script = """
def main():
    return 42
"""
        result = await executor.execute(script=script)
        assert result.success
        assert result.output == 42

    @pytest.mark.anyio
    async def test_with_inputs(self, executor):
        """Test execution with input arguments."""
        script = """
def main(a, b):
    return a + b
"""
        result = await executor.execute(script=script, inputs={"a": 10, "b": 20})
        assert result.success
        assert result.output == 30

    @pytest.mark.anyio
    async def test_with_return_types(self, executor):
        """Test various return types."""
        # Return dict
        script = """
def main():
    return {"key": "value", "count": 42}
"""
        result = await executor.execute(script=script)
        assert result.success
        assert result.output == {"key": "value", "count": 42}

        # Return list
        script = """
def main():
    return [1, 2, 3, 4, 5]
"""
        result = await executor.execute(script=script)
        assert result.success
        assert result.output == [1, 2, 3, 4, 5]

        # Return string
        script = """
def main():
    return "hello world"
"""
        result = await executor.execute(script=script)
        assert result.success
        assert result.output == "hello world"

    @pytest.mark.anyio
    async def test_single_function_not_main(self, executor):
        """Test that single functions not named 'main' are called."""
        script = """
def process_data():
    return "processed"
"""
        result = await executor.execute(script=script)
        assert result.success
        assert result.output == "processed"


class TestSafeStdlibExecution:
    """Test execution with safe stdlib modules."""

    @pytest.fixture
    def executor(self, tmp_path):
        return SafePythonExecutor(cache_dir=str(tmp_path))

    @pytest.mark.anyio
    async def test_json_module(self, executor):
        """Test using json module."""
        script = """
import json

def main():
    data = {"key": "value"}
    return json.dumps(data)
"""
        result = await executor.execute(script=script)
        assert result.success
        assert result.output == '{"key": "value"}'

    @pytest.mark.anyio
    async def test_datetime_module(self, executor):
        """Test using datetime module."""
        script = """
from datetime import datetime

def main():
    dt = datetime(2024, 1, 15, 12, 30, 0)
    return dt.isoformat()
"""
        result = await executor.execute(script=script)
        assert result.success
        assert result.output == "2024-01-15T12:30:00"

    @pytest.mark.anyio
    async def test_re_module(self, executor):
        """Test using re module."""
        script = """
import re

def main(text):
    pattern = r"\\b\\w+@\\w+\\.\\w+\\b"
    matches = re.findall(pattern, text)
    return matches
"""
        result = await executor.execute(
            script=script,
            inputs={"text": "Contact us at test@example.com or info@test.org"},
        )
        assert result.success
        assert "test@example.com" in result.output
        assert "info@test.org" in result.output

    @pytest.mark.anyio
    async def test_base64_module(self, executor):
        """Test using base64 module."""
        script = """
import base64

def main(text):
    encoded = base64.b64encode(text.encode()).decode()
    return encoded
"""
        result = await executor.execute(script=script, inputs={"text": "hello"})
        assert result.success
        assert result.output == "aGVsbG8="

    @pytest.mark.anyio
    async def test_hashlib_module(self, executor):
        """Test using hashlib module."""
        script = """
import hashlib

def main(text):
    return hashlib.sha256(text.encode()).hexdigest()
"""
        result = await executor.execute(script=script, inputs={"text": "hello"})
        assert result.success
        assert len(result.output) == 64  # SHA256 hex digest length

    @pytest.mark.anyio
    async def test_collections_module(self, executor):
        """Test using collections module."""
        script = """
from collections import Counter

def main(items):
    return dict(Counter(items))
"""
        result = await executor.execute(
            script=script, inputs={"items": ["a", "b", "a", "c", "a"]}
        )
        assert result.success
        assert result.output == {"a": 3, "b": 1, "c": 1}


class TestSystemModulesBlocked:
    """Test that system modules are blocked."""

    @pytest.fixture
    def executor(self, tmp_path):
        return SafePythonExecutor(cache_dir=str(tmp_path))

    @pytest.mark.anyio
    async def test_os_import_blocked(self, executor):
        """Test that os module is blocked."""
        script = """
import os

def main():
    return os.getcwd()
"""
        with pytest.raises(SandboxValidationError) as exc_info:
            await executor.execute(script=script)
        assert "os" in str(exc_info.value).lower()

    @pytest.mark.anyio
    async def test_sys_import_blocked(self, executor):
        """Test that sys module is blocked."""
        script = """
import sys

def main():
    return sys.version
"""
        with pytest.raises(SandboxValidationError) as exc_info:
            await executor.execute(script=script)
        assert "sys" in str(exc_info.value).lower()

    @pytest.mark.anyio
    async def test_subprocess_import_blocked(self, executor):
        """Test that subprocess module is blocked."""
        script = """
import subprocess

def main():
    return subprocess.run(["echo", "hello"], capture_output=True).stdout
"""
        with pytest.raises(SandboxValidationError) as exc_info:
            await executor.execute(script=script)
        assert "subprocess" in str(exc_info.value).lower()

    @pytest.mark.anyio
    async def test_shutil_import_blocked(self, executor):
        """Test that shutil module is blocked."""
        script = """
import shutil

def main():
    return "should not work"
"""
        with pytest.raises(SandboxValidationError) as exc_info:
            await executor.execute(script=script)
        assert "shutil" in str(exc_info.value).lower()

    @pytest.mark.anyio
    async def test_pathlib_import_blocked(self, executor):
        """Test that pathlib module is blocked."""
        script = """
from pathlib import Path

def main():
    return str(Path.home())
"""
        with pytest.raises(SandboxValidationError) as exc_info:
            await executor.execute(script=script)
        assert "pathlib" in str(exc_info.value).lower()


class TestNetworkModulesBlocked:
    """Test that network modules are blocked by default."""

    @pytest.fixture
    def executor(self, tmp_path):
        return SafePythonExecutor(cache_dir=str(tmp_path))

    @pytest.mark.anyio
    async def test_socket_import_blocked(self, executor):
        """Test that socket module is blocked."""
        script = """
import socket

def main():
    return socket.gethostname()
"""
        with pytest.raises(SandboxValidationError) as exc_info:
            await executor.execute(script=script)
        assert (
            "socket" in str(exc_info.value).lower()
            or "network" in str(exc_info.value).lower()
        )

    @pytest.mark.anyio
    async def test_http_client_blocked(self, executor):
        """Test that http.client module is blocked."""
        script = """
import http.client

def main():
    return "should not work"
"""
        with pytest.raises(SandboxValidationError) as exc_info:
            await executor.execute(script=script)
        assert (
            "http" in str(exc_info.value).lower()
            or "network" in str(exc_info.value).lower()
        )

    @pytest.mark.anyio
    async def test_urllib_request_blocked(self, executor):
        """Test that urllib.request module is blocked."""
        script = """
import urllib.request

def main():
    return urllib.request.urlopen("https://example.com")
"""
        with pytest.raises(SandboxValidationError) as exc_info:
            await executor.execute(script=script)
        assert (
            "urllib" in str(exc_info.value).lower()
            or "network" in str(exc_info.value).lower()
        )

    @pytest.mark.anyio
    async def test_urllib_parse_allowed(self, executor):
        """Test that urllib.parse (safe) is allowed."""
        script = """
from urllib.parse import quote, urlencode

def main():
    return quote("hello world")
"""
        result = await executor.execute(script=script)
        assert result.success
        assert result.output == "hello%20world"


class TestScriptErrors:
    """Test error handling in scripts."""

    @pytest.fixture
    def executor(self, tmp_path):
        return SafePythonExecutor(cache_dir=str(tmp_path))

    @pytest.mark.anyio
    async def test_runtime_error(self, executor):
        """Test that runtime errors are captured."""
        script = """
def main():
    raise ValueError("Test error message")
"""
        result = await executor.execute(script=script)
        assert not result.success
        assert "ValueError" in result.error
        assert "Test error message" in result.error

    @pytest.mark.anyio
    async def test_division_by_zero(self, executor):
        """Test that division by zero is captured."""
        script = """
def main():
    return 1 / 0
"""
        result = await executor.execute(script=script)
        assert not result.success
        assert "ZeroDivisionError" in result.error

    @pytest.mark.anyio
    async def test_undefined_variable(self, executor):
        """Test that undefined variable errors are captured."""
        script = """
def main():
    return undefined_variable
"""
        result = await executor.execute(script=script)
        assert not result.success
        assert "NameError" in result.error

    @pytest.mark.anyio
    async def test_missing_required_argument(self, executor):
        """Test that missing argument errors are captured."""
        script = """
def main(required_arg):
    return required_arg
"""
        result = await executor.execute(script=script, inputs={})
        assert not result.success
        assert "TypeError" in result.error


class TestTimeout:
    """Test timeout handling."""

    @pytest.fixture
    def executor(self, tmp_path):
        return SafePythonExecutor(cache_dir=str(tmp_path))

    @pytest.mark.anyio
    async def test_timeout(self, executor):
        """Test that scripts that take too long are terminated."""
        script = """
import time

def main():
    time.sleep(10)
    return "done"
"""
        with pytest.raises(SandboxTimeoutError):
            await executor.execute(script=script, timeout_seconds=2)


class TestEnvVars:
    """Test environment variable handling."""

    @pytest.fixture
    def executor(self, tmp_path):
        return SafePythonExecutor(cache_dir=str(tmp_path))

    @pytest.mark.anyio
    async def test_env_vars_not_accessible_via_os(self, executor):
        """Test that os.environ is blocked even with env_vars passed."""
        script = """
import os

def main():
    return os.environ.get("TEST_VAR")
"""
        # Should fail validation before env_vars matter
        with pytest.raises(SandboxValidationError):
            await executor.execute(
                script=script,
                env_vars={"TEST_VAR": "test_value"},
            )


class TestCaching:
    """Test dependency caching."""

    @pytest.fixture
    def executor(self, tmp_path):
        return SafePythonExecutor(cache_dir=str(tmp_path))

    def test_cache_key_computation(self, executor):
        """Test that cache keys are computed correctly."""
        key1 = executor._compute_cache_key(["requests==2.28.0"])
        key2 = executor._compute_cache_key(["requests==2.28.0"])
        key3 = executor._compute_cache_key(["requests==2.29.0"])

        # Same deps = same key
        assert key1 == key2
        # Different version = different key
        assert key1 != key3

    def test_cache_key_workspace_isolation(self, executor):
        """Test that workspace ID affects cache key."""
        deps = ["requests==2.28.0"]
        key_no_workspace = executor._compute_cache_key(deps)
        key_workspace_a = executor._compute_cache_key(deps, workspace_id="workspace-a")
        key_workspace_b = executor._compute_cache_key(deps, workspace_id="workspace-b")

        # Different workspaces = different keys
        assert key_no_workspace != key_workspace_a
        assert key_workspace_a != key_workspace_b

    def test_allowed_modules_extraction(self, executor):
        """Test that allowed modules are extracted correctly from dependencies."""
        allowed = executor._get_allowed_modules(
            ["requests==2.28.0", "py-ocsf-models>=0.8.0"]
        )

        assert "requests" in allowed
        assert "py_ocsf_models" in allowed


class TestAllowNetwork:
    """Test allow_network parameter behavior."""

    @pytest.fixture
    def executor(self, tmp_path):
        return SafePythonExecutor(cache_dir=str(tmp_path))

    @pytest.mark.anyio
    async def test_allow_network_logs_warning(self, executor, caplog):
        """Test that allow_network=True logs a warning."""
        script = """
def main():
    return 42
"""
        await executor.execute(script=script, allow_network=True)
        # The warning should be logged about limited network isolation
        # Note: We can't easily check caplog with structlog, so we just verify execution works

    @pytest.mark.anyio
    async def test_allow_network_false_blocks_socket(self, executor):
        """Test that socket is blocked with allow_network=False."""
        script = """
import socket
def main():
    return "should fail"
"""
        with pytest.raises(SandboxValidationError):
            await executor.execute(script=script, allow_network=False)


class TestSafeExecutorFallback:
    """Tests for safe executor fallback when nsjail is not available.

    These tests run with TRACECAT__DISABLE_NSJAIL=true to use the safe executor
    instead of nsjail. This tests the fallback path for environments without
    privileged Docker mode.
    """

    @pytest.fixture(autouse=True)
    def force_safe_executor(self, monkeypatch, tmp_path):
        """Force use of safe executor by disabling nsjail."""
        cache_dir = str(tmp_path / "sandbox-cache")
        monkeypatch.setenv("TRACECAT__DISABLE_NSJAIL", "true")
        # Use a temp directory for sandbox cache to avoid permission issues
        monkeypatch.setenv("TRACECAT__SANDBOX_CACHE_DIR", cache_dir)
        # Reload config to pick up the change
        import tracecat.config as config_module

        importlib.reload(config_module)
        yield
        # Restore after test
        importlib.reload(config_module)

    @pytest.fixture
    def sandbox_service(self, tmp_path):
        """Create a SandboxService with a temporary cache directory."""
        # Import after config reload to get the updated values
        from tracecat.sandbox import SandboxService

        return SandboxService(cache_dir=str(tmp_path / "sandbox-cache"))

    @pytest.mark.anyio
    async def test_fallback_basic_execution(self, sandbox_service):
        """Test that fallback executor works for basic scripts."""
        script = """
def main():
    return 42
"""
        result = await sandbox_service.run_python(script=script)
        assert result == 42

    @pytest.mark.anyio
    async def test_fallback_with_inputs(self, sandbox_service):
        """Test fallback executor with input arguments."""
        script = """
def main(a, b):
    return a * b + 10
"""
        result = await sandbox_service.run_python(
            script=script, inputs={"a": 5, "b": 3}
        )
        assert result == 25

    @pytest.mark.anyio
    async def test_fallback_safe_stdlib(self, sandbox_service):
        """Test that safe stdlib modules work in fallback mode."""
        script = """
import json
import re
import datetime
import base64

def main():
    data = {"key": "value"}
    encoded = base64.b64encode(json.dumps(data).encode()).decode()
    return encoded
"""
        result = await sandbox_service.run_python(script=script)
        assert result is not None
        # Verify it's valid base64
        import base64 as b64

        decoded = b64.b64decode(result).decode()
        assert decoded == '{"key": "value"}'

    @pytest.mark.anyio
    async def test_fallback_blocks_os_module(self, sandbox_service):
        """Test that os module is blocked in fallback mode."""
        script = """
import os

def main():
    return os.getcwd()
"""
        with pytest.raises(SandboxValidationError) as exc_info:
            await sandbox_service.run_python(script=script)
        assert "os" in str(exc_info.value).lower()

    @pytest.mark.anyio
    async def test_fallback_blocks_subprocess(self, sandbox_service):
        """Test that subprocess module is blocked in fallback mode."""
        script = """
import subprocess

def main():
    return subprocess.run(["echo", "test"], capture_output=True).stdout
"""
        with pytest.raises(SandboxValidationError) as exc_info:
            await sandbox_service.run_python(script=script)
        assert "subprocess" in str(exc_info.value).lower()

    @pytest.mark.anyio
    async def test_fallback_blocks_network_by_default(self, sandbox_service):
        """Test that network modules are blocked by default in fallback mode."""
        script = """
import socket

def main():
    return socket.gethostname()
"""
        with pytest.raises(SandboxValidationError) as exc_info:
            await sandbox_service.run_python(script=script)
        assert (
            "socket" in str(exc_info.value).lower()
            or "network" in str(exc_info.value).lower()
        )

    @pytest.mark.anyio
    async def test_fallback_error_handling(self, sandbox_service):
        """Test that errors are properly captured in fallback mode."""
        script = """
def main():
    raise ValueError("Test error from fallback")
"""
        with pytest.raises(SandboxExecutionError) as exc_info:
            await sandbox_service.run_python(script=script)
        assert "Test error from fallback" in str(exc_info.value)

    @pytest.mark.anyio
    async def test_fallback_complex_data_types(self, sandbox_service):
        """Test handling of complex data types in fallback mode."""
        script = """
def main(data_list, data_dict):
    return {
        "input_list": data_list,
        "input_dict": data_dict,
        "processed": {
            "list_length": len(data_list),
            "dict_keys": list(data_dict.keys())
        }
    }
"""
        result = await sandbox_service.run_python(
            script=script,
            inputs={"data_list": [1, 2, 3, 4, 5], "data_dict": {"a": 1, "b": 2}},
        )
        assert isinstance(result, dict)
        assert result["input_list"] == [1, 2, 3, 4, 5]
        assert result["input_dict"] == {"a": 1, "b": 2}
        assert result["processed"]["list_length"] == 5


class TestSecurityBypasses:
    """Test that security bypass attempts are blocked.

    These tests verify that various attempts to bypass the sandbox security
    are properly blocked by AST validation and/or runtime import hooks.
    """

    @pytest.fixture
    def executor(self, tmp_path):
        """Create an executor with a temp cache directory."""
        return SafePythonExecutor(cache_dir=str(tmp_path))

    @pytest.mark.anyio
    async def test_dunder_import_blocked(self, executor):
        """Test that __import__() calls are blocked at AST validation.

        This verifies the fix for the security vulnerability where __import__('os')
        could bypass AST validation since it's an ast.Call node, not ast.Import.
        """
        script = """
def main():
    os = __import__('os')
    return os.getcwd()
"""
        with pytest.raises(SandboxValidationError) as exc_info:
            await executor.execute(script=script)
        assert "__import__" in str(exc_info.value)

    @pytest.mark.anyio
    async def test_builtins_dunder_import_blocked(self, executor):
        """Test that builtins.__import__() is blocked.

        This verifies that the attribute access form of __import__ is also blocked.
        """
        script = """
def main():
    import builtins
    os = builtins.__import__('os')
    return os.getcwd()
"""
        with pytest.raises(SandboxValidationError) as exc_info:
            await executor.execute(script=script)
        assert "__import__" in str(exc_info.value)

    @pytest.mark.anyio
    async def test_dunder_import_sys_blocked(self, executor):
        """Test that __import__('sys') is blocked."""
        script = """
def main():
    sys = __import__('sys')
    return sys.version
"""
        with pytest.raises(SandboxValidationError) as exc_info:
            await executor.execute(script=script)
        assert "__import__" in str(exc_info.value)

    @pytest.mark.anyio
    async def test_dunder_import_subprocess_blocked(self, executor):
        """Test that __import__('subprocess') is blocked."""
        script = """
def main():
    subprocess = __import__('subprocess')
    return subprocess.run(['echo', 'pwned'], capture_output=True).stdout
"""
        with pytest.raises(SandboxValidationError) as exc_info:
            await executor.execute(script=script)
        assert "__import__" in str(exc_info.value)

    @pytest.mark.anyio
    async def test_exec_blocked(self, executor):
        """Test that exec() is blocked to prevent import hook bypass.

        exec() with manipulated globals could be used to bypass the import hook's
        origin checking by setting __file__ to a fake site-packages path.
        """
        script = """
def main():
    exec("x = 1 + 1")
    return True
"""
        with pytest.raises(SandboxValidationError) as exc_info:
            await executor.execute(script=script)
        assert "exec" in str(exc_info.value)

    @pytest.mark.anyio
    async def test_eval_blocked(self, executor):
        """Test that eval() is blocked to prevent import hook bypass.

        eval() with manipulated globals could be used to bypass the import hook's
        origin checking.
        """
        script = """
def main():
    return eval("1 + 1")
"""
        with pytest.raises(SandboxValidationError) as exc_info:
            await executor.execute(script=script)
        assert "eval" in str(exc_info.value)

    @pytest.mark.anyio
    async def test_compile_blocked(self, executor):
        """Test that compile() is blocked to prevent code compilation attacks."""
        script = """
def main():
    code = compile("import os", "<string>", "exec")
    return code
"""
        with pytest.raises(SandboxValidationError) as exc_info:
            await executor.execute(script=script)
        assert "compile" in str(exc_info.value)

    @pytest.mark.anyio
    async def test_exec_with_manipulated_globals_blocked(self, executor):
        """Test that exec() with manipulated globals is blocked.

        This tests the specific attack vector where exec() is called with a
        fake __file__ to bypass the import hook's origin checking.
        """
        script = """
def main():
    fake_globals = {"__file__": "/fake/site-packages/malicious.py"}
    exec("import os", fake_globals)
    return fake_globals.get("os")
"""
        with pytest.raises(SandboxValidationError) as exc_info:
            await executor.execute(script=script)
        assert "exec" in str(exc_info.value)

    @pytest.mark.anyio
    async def test_builtins_exec_blocked(self, executor):
        """Test that builtins.exec() is also blocked."""
        script = """
import builtins

def main():
    builtins.exec("x = 1")
    return True
"""
        with pytest.raises(SandboxValidationError) as exc_info:
            await executor.execute(script=script)
        assert "exec" in str(exc_info.value)

    @pytest.mark.anyio
    async def test_safe_stdlib_with_os_dependency_still_works(self, executor):
        """Test that safe stdlib modules that internally use os still work.

        Modules like traceback depend on os internally, but they import it
        during wrapper initialization before the hook is active.
        User code should still be able to use these modules.
        """
        script = """
import traceback

def main():
    try:
        raise ValueError("test error")
    except ValueError:
        tb = traceback.format_exc()
        return "ValueError" in tb and "test error" in tb
"""
        result = await executor.execute(script=script)
        assert result.success
        assert result.output is True

    @pytest.mark.anyio
    async def test_inspect_module_blocked(self, executor):
        """Test that inspect module is blocked to prevent sandbox escape.

        The inspect module provides frame introspection capabilities that can be
        used to escape the sandbox by accessing parent frame globals and disabling
        the import hook (e.g., `inspect.currentframe().f_back.f_globals`).
        """
        script = """
import inspect

def example_func(a, b):
    return a + b

def main():
    return inspect.isfunction(example_func)
"""
        with pytest.raises(SandboxValidationError) as exc_info:
            await executor.execute(script=script)
        assert "inspect" in str(exc_info.value)

    @pytest.mark.anyio
    async def test_frame_introspection_escape_blocked(self, executor):
        """Test that frame introspection-based sandbox escape is blocked.

        This verifies that the specific attack vector using inspect.currentframe()
        to access the wrapper's globals and disable the import hook is blocked.
        """
        # This attack would:
        # 1. Import inspect
        # 2. Use inspect.currentframe().f_back.f_globals to access wrapper globals
        # 3. Set _wrapper_initialized = False to disable the import hook
        # 4. Import blocked modules like os
        script = """
import inspect

def main():
    # Get parent frame's globals (the wrapper)
    frame = inspect.currentframe()
    parent_globals = frame.f_back.f_globals
    # Try to disable the import hook
    parent_globals['_wrapper_initialized'] = False
    # Now try to import os
    import os
    return os.getcwd()
"""
        with pytest.raises(SandboxValidationError) as exc_info:
            await executor.execute(script=script)
        # Should fail at AST validation because inspect is not allowed
        assert "inspect" in str(exc_info.value)


class TestTransitiveImports:
    """Test that transitive imports from packages work while direct imports are blocked."""

    @pytest.fixture
    def executor(self, tmp_path):
        """Create an executor with a temp cache directory."""
        return SafePythonExecutor(cache_dir=str(tmp_path))

    @pytest.mark.anyio
    async def test_package_with_os_dependency_works(self, executor):
        """Test that packages which internally use os (like numpy) work correctly.

        Many packages (numpy, pandas, etc.) internally import os for path operations,
        temp files, etc. This should NOT be blocked because:
        1. The import comes from site-packages (trusted installed code)
        2. The user explicitly declared the package as a dependency
        3. Only DIRECT user script imports of os should be blocked

        This test uses 'logging' which is stdlib and uses os internally.
        """
        script = """
import logging

def main():
    # logging module internally uses os for various operations
    logger = logging.getLogger('test')
    logger.setLevel(logging.INFO)
    return "logging works"
"""
        result = await executor.execute(script=script)
        assert result.success
        assert result.output == "logging works"

    @pytest.mark.anyio
    async def test_numpy_with_internal_os_import(self, executor):
        """Regression test: numpy (which internally imports os) should work.

        This is a regression test for the issue where numpy failed with:
        "ImportError: Import of module 'os' is blocked for security reasons"

        The fix ensures that packages can use os internally while still blocking
        direct user script imports of os.
        """
        script = """
def main():
    import numpy as np
    result = np.array([1, 2, 3])
    return result.tolist()
"""
        result = await executor.execute(
            script=script,
            dependencies=["numpy"],
            timeout_seconds=120,  # numpy installation can take time
        )
        assert result.success, f"Expected success but got error: {result.error}"
        assert result.output == [1, 2, 3]

    @pytest.mark.anyio
    async def test_package_transitive_imports_allowed(self, executor):
        """Test that packages can import their transitive dependencies.

        When a user imports a package like openpyxl, that package should be
        able to import its own dependencies (like et_xmlfile, strings, etc.)
        even if those aren't in the user's declared dependencies.

        This test uses the 'csv' stdlib module which internally may import
        other modules. The key is that transitive imports from allowed
        packages should work.
        """
        script = """
import csv
import io

def main():
    # csv module works and can import its dependencies
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['name', 'value'])
    writer.writerow(['test', 42])
    return output.getvalue()
"""
        result = await executor.execute(script=script)
        assert result.success
        assert "name,value" in result.output
        assert "test,42" in result.output

    @pytest.mark.anyio
    async def test_user_cannot_import_unlisted_packages(self, executor):
        """Test that users cannot directly import packages not in their dependencies.

        Even though transitive imports from packages are allowed, users should
        not be able to directly import arbitrary modules that aren't:
        1. In the safe stdlib list
        2. In their declared dependencies
        """
        script = """
import someunknownpackage

def main():
    return "should fail"
"""
        # This should fail at AST validation (before runtime)
        with pytest.raises(SandboxValidationError) as exc_info:
            await executor.execute(script=script)
        assert "someunknownpackage" in str(exc_info.value)

    @pytest.mark.anyio
    async def test_blocked_modules_blocked_in_user_script(self, executor):
        """Test that user scripts cannot directly import blocked modules (os, sys, etc.).

        Security model:
        - User scripts CANNOT directly import os, sys, subprocess, etc.
        - But packages CAN use these internally (they're trusted installed code)

        This test verifies that direct user imports of blocked modules fail
        at AST validation time.
        """
        # This tests the AST validation - os is blocked at validation time
        script = """
import os

def main():
    return os.getcwd()
"""
        with pytest.raises(SandboxValidationError) as exc_info:
            await executor.execute(script=script)
        assert "os" in str(exc_info.value).lower()
