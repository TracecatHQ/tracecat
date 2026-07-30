"""Tests for UnsafePidExecutor fallback mode."""

import asyncio
import contextlib
import logging
import os
import signal
from pathlib import Path

import pytest

from tracecat.sandbox import unsafe_pid_executor
from tracecat.sandbox.exceptions import SandboxTimeoutError
from tracecat.sandbox.unsafe_pid_executor import UnsafePidExecutor


def _process_is_running(pid: int) -> bool:
    """Return whether a process is alive, treating zombies as terminated."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False

    stat_path = Path(f"/proc/{pid}/stat")
    if not stat_path.exists():
        return True
    try:
        stat_fields = stat_path.read_text().split()
    except (FileNotFoundError, ProcessLookupError):
        # The process can exit between the existence check and reading procfs.
        return False
    if len(stat_fields) < 3 or stat_fields[2] == "Z":
        return False
    return True


async def _wait_for_file(path: Path) -> None:
    for _ in range(200):
        if path.exists():
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"Timed out waiting for {path}")


async def _wait_for_process_exit(pid: int) -> None:
    for _ in range(200):
        if not _process_is_running(pid):
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"Process {pid} did not exit")


def _background_process_script() -> str:
    return """
import subprocess
import sys
import time
from pathlib import Path

def main(pid_file, wait):
    child = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    Path(pid_file).write_text(str(child.pid))
    if wait:
        time.sleep(30)
    return child.pid
"""


class TestUnsafePidExecutor:
    @pytest.fixture
    def executor(self, tmp_path) -> UnsafePidExecutor:
        return UnsafePidExecutor(cache_dir=str(tmp_path))

    def test_process_probe_handles_procfs_exit_race(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def process_disappeared(
            path: Path,
            encoding: str | None = None,
            errors: str | None = None,
        ) -> str:
            del path, encoding, errors
            raise ProcessLookupError

        monkeypatch.setattr(os, "kill", lambda *_: None)
        monkeypatch.setattr(Path, "exists", lambda _: True)
        monkeypatch.setattr(Path, "read_text", process_disappeared)

        assert not _process_is_running(123)

    @pytest.mark.anyio
    async def test_build_execution_cmd_with_pid_namespace(
        self, executor: UnsafePidExecutor, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def pid_namespace_available() -> bool:
            return True

        monkeypatch.setattr(
            "tracecat.sandbox.unsafe_pid_executor.pid_namespace_available",
            pid_namespace_available,
        )
        cmd = await executor._build_execution_cmd(
            "python3", executor.cache_dir / "wrapper.py"
        )
        assert cmd[:4] == ["unshare", "--pid", "--fork", "--kill-child"]

    @pytest.mark.anyio
    async def test_pid_isolation_warning_logged_once(
        self,
        executor: UnsafePidExecutor,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        async def pid_namespace_unavailable() -> bool:
            return False

        loguru_warnings = []

        class FakeLogger:
            def warning(self, message: str, **kwargs: object) -> None:
                loguru_warnings.append((message, kwargs))

        monkeypatch.setattr(
            "tracecat.sandbox.unsafe_pid_executor.pid_namespace_available",
            pid_namespace_unavailable,
        )
        monkeypatch.setattr(
            "tracecat.sandbox.unsafe_pid_executor.pid_namespace_probe_error",
            lambda: "fargate restriction",
        )
        monkeypatch.setattr("tracecat.sandbox.unsafe_pid_executor.logger", FakeLogger())
        caplog.set_level(logging.WARNING, logger="tracecat.sandbox.unsafe_pid_executor")

        await executor._build_execution_cmd(
            "python3", executor.cache_dir / "wrapper.py"
        )
        await executor._build_execution_cmd(
            "python3", executor.cache_dir / "wrapper.py"
        )

        warnings = [
            record
            for record in caplog.records
            if "PID namespace isolation unavailable" in record.message
        ]
        assert len(warnings) == 1
        assert loguru_warnings == [
            (
                "PID namespace isolation unavailable; running script without PID isolation",
                {"reason": "fargate restriction"},
            )
        ]

    @pytest.mark.anyio
    async def test_pid_probe_timeout_handles_process_lookup_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        class FakeProbe:
            returncode = None

            async def wait(self) -> int:
                return 0

            def kill(self) -> None:
                raise ProcessLookupError

        async def fake_create_subprocess_exec(*args, **kwargs):
            return FakeProbe()

        async def fake_wait_for(awaitable, *args, **kwargs):
            del args, kwargs
            awaitable.close()
            raise TimeoutError

        monkeypatch.setattr("tracecat.sandbox.utils._PID_NAMESPACE_AVAILABLE", None)
        monkeypatch.setattr("tracecat.sandbox.utils._PID_NAMESPACE_PROBE_ERROR", None)
        monkeypatch.setattr("tracecat.sandbox.utils.shutil.which", lambda *_: "unshare")
        monkeypatch.setattr(
            asyncio, "create_subprocess_exec", fake_create_subprocess_exec
        )
        monkeypatch.setattr(asyncio, "wait_for", fake_wait_for)

        available = await unsafe_pid_executor.pid_namespace_available()
        assert not available
        assert (
            unsafe_pid_executor.pid_namespace_probe_error() == "unshare probe timed out"
        )

    @pytest.mark.anyio
    async def test_execute_basic_script(self, executor: UnsafePidExecutor) -> None:
        script = """
def main():
    return 42
"""
        result = await executor.execute(script=script)
        assert result.success
        assert result.output == 42

    @pytest.mark.anyio
    async def test_execute_kills_background_descendants_after_success(
        self, executor: UnsafePidExecutor, tmp_path: Path
    ) -> None:
        pid_file = tmp_path / "success-child.pid"
        result = await executor.execute(
            script=_background_process_script(),
            inputs={"pid_file": str(pid_file), "wait": False},
        )

        assert result.success
        child_pid = int(pid_file.read_text())
        try:
            await _wait_for_process_exit(child_pid)
        finally:
            with contextlib.suppress(ProcessLookupError):
                os.kill(child_pid, signal.SIGKILL)

    @pytest.mark.anyio
    async def test_execute_kills_descendants_on_timeout(
        self, executor: UnsafePidExecutor, tmp_path: Path
    ) -> None:
        pid_file = tmp_path / "timeout-child.pid"

        with pytest.raises(SandboxTimeoutError):
            await executor.execute(
                script=_background_process_script(),
                inputs={"pid_file": str(pid_file), "wait": True},
                timeout_seconds=1,
            )

        child_pid = int(pid_file.read_text())
        try:
            await _wait_for_process_exit(child_pid)
        finally:
            with contextlib.suppress(ProcessLookupError):
                os.kill(child_pid, signal.SIGKILL)

    @pytest.mark.anyio
    async def test_execute_kills_descendants_on_cancellation(
        self, executor: UnsafePidExecutor, tmp_path: Path
    ) -> None:
        pid_file = tmp_path / "cancelled-child.pid"
        execution = asyncio.create_task(
            executor.execute(
                script=_background_process_script(),
                inputs={"pid_file": str(pid_file), "wait": True},
                timeout_seconds=30,
            )
        )
        await _wait_for_file(pid_file)
        child_pid = int(pid_file.read_text())

        execution.cancel()
        try:
            with pytest.raises(asyncio.CancelledError):
                await execution
            await _wait_for_process_exit(child_pid)
        finally:
            with contextlib.suppress(ProcessLookupError):
                os.kill(child_pid, signal.SIGKILL)

    @pytest.mark.anyio
    async def test_execute_normalizes_non_json_leaf_values(
        self, executor: UnsafePidExecutor
    ) -> None:
        script = """
from datetime import datetime

def main():
    return {
        "all_ips": {"2.2.2.2", "1.1.1.1"},
        "seen_at": datetime(2026, 3, 30, 13, 9, 52),
        "nested": [{"ports": {443, 80}}],
    }
"""
        result = await executor.execute(script=script)
        assert result.success
        assert result.error is None
        assert result.output == {
            "all_ips": ["1.1.1.1", "2.2.2.2"],
            "seen_at": "2026-03-30T13:09:52",
            "nested": [{"ports": [80, 443]}],
        }

    @pytest.mark.anyio
    async def test_execute_normalizes_mixed_sets_deterministically(
        self, executor: UnsafePidExecutor
    ) -> None:
        script = """
def main():
    return {
        "mixed": {1, "a"},
        "nested": [{"values": {2, None}}],
    }
"""
        result = await executor.execute(script=script)
        assert result.success
        assert result.error is None
        assert result.output == {
            "mixed": ["a", 1],
            "nested": [{"values": [2, None]}],
        }

    @pytest.mark.anyio
    async def test_execute_normalizes_dataclass_instances_and_classes(
        self, executor: UnsafePidExecutor
    ) -> None:
        script = """
from dataclasses import dataclass

@dataclass
class Finding:
    name: str

def main():
    return {
        "instance": Finding("alert"),
        "class": Finding,
    }
"""
        result = await executor.execute(script=script)
        assert result.success
        assert result.error is None
        assert result.output == {
            "instance": {"name": "alert"},
            "class": "<class '__main__.Finding'>",
        }

    @pytest.mark.anyio
    async def test_execute_reports_recursive_dataclass_as_serialization_error(
        self, executor: UnsafePidExecutor
    ) -> None:
        script = """
from dataclasses import dataclass

@dataclass
class Node:
    name: str
    child: object = None

def main():
    root = Node("root")
    root.child = root
    return root
"""
        result = await executor.execute(script=script)
        assert not result.success
        assert result.output == "Node(name='root', child=...)"
        assert result.error is not None
        assert "Output not JSON-serializable" in result.error
        assert "Recursive dataclass values are not JSON-serializable" in result.error

    @pytest.mark.anyio
    @pytest.mark.integration
    async def test_execute_does_not_inherit_process_env(
        self, executor: UnsafePidExecutor, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("TRACECAT_TEST_SECRET", "super-secret")
        script = """
import os

def main():
    return os.environ.get("TRACECAT_TEST_SECRET")
"""
        result = await executor.execute(script=script)
        assert result.success
        assert result.output is None

    @pytest.mark.anyio
    async def test_execute_includes_explicit_env_vars(
        self, executor: UnsafePidExecutor
    ) -> None:
        script = """
import os

def main():
    return os.environ.get("INJECTED")
"""
        result = await executor.execute(script=script, env_vars={"INJECTED": "value"})
        assert result.success
        assert result.output == "value"

    @pytest.mark.anyio
    async def test_network_isolation_warning_logs_once_when_disallowed(
        self, executor: UnsafePidExecutor, caplog: pytest.LogCaptureFixture
    ) -> None:
        script = """
def main():
    return "ok"
"""
        caplog.set_level(logging.WARNING, logger="tracecat.sandbox.unsafe_pid_executor")
        caplog.clear()
        await executor.execute(script=script, allow_network=False)
        await executor.execute(script=script, allow_network=False)
        warnings = [
            record
            for record in caplog.records
            if "Network isolation is not enforced without nsjail" in record.message
        ]
        assert len(warnings) == 1

    @pytest.mark.anyio
    async def test_no_network_warning_when_network_is_explicitly_allowed(
        self, executor: UnsafePidExecutor, caplog: pytest.LogCaptureFixture
    ) -> None:
        script = """
def main():
    return "ok"
"""
        caplog.set_level(logging.WARNING, logger="tracecat.sandbox.unsafe_pid_executor")
        caplog.clear()
        await executor.execute(script=script, allow_network=True)
        assert not any(
            "Network isolation is not enforced without nsjail" in record.message
            for record in caplog.records
        )
