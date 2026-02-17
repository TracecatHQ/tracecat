"""Tests for UnsafePidExecutor fallback mode."""

import pytest

from tracecat.sandbox.unsafe_pid_executor import (
    UnsafePidExecutor,
    _extract_package_name,
)


class TestDependencyParsing:
    def test_extract_package_name(self) -> None:
        assert _extract_package_name("requests==2.31.0") == "requests"
        assert _extract_package_name("py-ocsf-models>=0.8.0") == "py_ocsf_models"


class TestUnsafePidExecutor:
    @pytest.fixture
    def executor(self, tmp_path) -> UnsafePidExecutor:
        return UnsafePidExecutor(cache_dir=str(tmp_path))

    @pytest.mark.anyio
    async def test_build_execution_cmd_with_pid_namespace(
        self, executor: UnsafePidExecutor, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def pid_namespace_available() -> bool:
            return True

        monkeypatch.setattr(
            executor,
            "_is_pid_namespace_available",
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

        monkeypatch.setattr(
            executor,
            "_is_pid_namespace_available",
            pid_namespace_unavailable,
        )
        executor._pid_namespace_probe_error = "fargate restriction"

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
    async def test_allow_network_warning_logs_only_when_enabled(
        self, executor: UnsafePidExecutor, caplog: pytest.LogCaptureFixture
    ) -> None:
        script = """
def main():
    return "ok"
"""
        caplog.clear()
        await executor.execute(script=script)
        assert not any(
            "allow_network is not enforced without nsjail" in record.message
            for record in caplog.records
        )

        caplog.clear()
        await executor.execute(script=script, allow_network=True)
        assert any(
            "allow_network is not enforced without nsjail" in record.message
            for record in caplog.records
        )
