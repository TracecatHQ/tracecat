"""Tests for the ActionRunner.

These tests cover tarball caching, cache key computation, and execution logic.
"""

from __future__ import annotations

import asyncio
import tempfile
import uuid
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import orjson
import pytest

from tracecat.auth.types import Role
from tracecat.authz.scopes import SERVICE_PRINCIPAL_SCOPES
from tracecat.dsl.common import create_default_execution_context
from tracecat.dsl.schemas import ActionStatement, RunActionInput, RunContext
from tracecat.executor import action_runner
from tracecat.executor.action_runner import ActionRunner
from tracecat.executor.schemas import (
    ActionImplementation,
    ExecutorActionErrorInfo,
    ResolvedContext,
)
from tracecat.executor.secret_preprocessors import SecretEnvProjection
from tracecat.identifiers.workflow import WorkflowUUID
from tracecat.registry.lock.types import RegistryLock


def _empty_secret_projection() -> SecretEnvProjection:
    """Return an empty secret projection for direct runner unit tests."""
    return SecretEnvProjection(env={}, mask_values=set())


@pytest.fixture
def mock_role() -> Role:
    """Create a mock role for testing."""
    return Role(
        type="service",
        service_id="tracecat-executor",
        workspace_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        scopes=SERVICE_PRINCIPAL_SCOPES["tracecat-executor"],
    )


@pytest.fixture
def mock_run_action_input() -> RunActionInput:
    """Create a mock RunActionInput for testing."""
    wf_id = WorkflowUUID.new_uuid4()
    action_name = "core.http_request"
    return RunActionInput(
        task=ActionStatement(
            action=action_name,
            args={"url": "https://example.com"},
            ref="test_action",
        ),
        exec_context=create_default_execution_context(),
        run_context=RunContext(
            wf_id=wf_id,
            wf_exec_id=f"{wf_id.short()}/exec_test",
            wf_run_id=uuid.uuid4(),
            environment="test",
            logical_time=datetime.now(UTC),
        ),
        registry_lock=RegistryLock(
            origins={"tracecat_registry": "test-version"},
            actions={action_name: "tracecat_registry"},
        ),
    )


@pytest.fixture
def temp_cache_dir():
    """Create a temporary cache directory."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


class TestActionRunner:
    """Tests for ActionRunner class."""

    @pytest.mark.anyio
    async def test_ensure_registry_environment_no_tarball(self, temp_cache_dir):
        """Test that an empty list is returned when no tarball URI provided."""
        runner = ActionRunner(cache_dir=temp_cache_dir)

        result = await runner.ensure_registry_environment(None)
        assert result == []

        result = await runner.ensure_registry_environment("")
        assert result == []

    @pytest.mark.anyio
    async def test_execute_action_timeout(
        self, temp_cache_dir, mock_run_action_input, mock_role
    ):
        """Test that action execution respects timeout."""
        runner = ActionRunner(cache_dir=temp_cache_dir)

        # Create base cache dir
        base_dir = temp_cache_dir / "base"
        base_dir.mkdir()

        with (
            patch("tracecat.executor.action_runner.config") as mock_config,
            patch("asyncio.create_subprocess_exec") as mock_subprocess,
        ):
            mock_config.TRACECAT__EXECUTOR_CLIENT_TIMEOUT = 0.1
            mock_config.TRACECAT__EXECUTOR_SANDBOX_ENABLED = False
            mock_config.TRACECAT__EXECUTOR_REGISTRY_CACHE_DIR = str(temp_cache_dir)

            # Create a mock process that hangs
            mock_proc = AsyncMock()
            mock_proc.returncode = None

            async def slow_communicate(input=None):  # noqa: A002
                await asyncio.sleep(10)  # Hang forever
                return b"", b""

            mock_proc.communicate = slow_communicate
            mock_proc.kill = MagicMock()
            mock_proc.wait = AsyncMock()
            mock_subprocess.return_value = mock_proc

            result = await runner._execute_direct(
                input=mock_run_action_input,
                role=mock_role,
                registry_paths=[base_dir],
                secret_projection=_empty_secret_projection(),
                timeout=0.1,
            )

            assert isinstance(result, ExecutorActionErrorInfo)
            assert result.type == "TimeoutError"
            mock_proc.kill.assert_called_once()

    @pytest.mark.anyio
    async def test_execute_action_subprocess_crash(
        self, temp_cache_dir, mock_run_action_input, mock_role
    ):
        """Test handling of subprocess crash."""
        runner = ActionRunner(cache_dir=temp_cache_dir)
        base_dir = temp_cache_dir / "base"
        base_dir.mkdir()

        with patch("asyncio.create_subprocess_exec") as mock_subprocess:
            mock_proc = AsyncMock()
            mock_proc.returncode = 1
            mock_proc.communicate = AsyncMock(return_value=(b"", b"Segmentation fault"))
            mock_subprocess.return_value = mock_proc

            result = await runner._execute_direct(
                input=mock_run_action_input,
                role=mock_role,
                registry_paths=[base_dir],
                secret_projection=_empty_secret_projection(),
                timeout=10.0,
            )

            assert isinstance(result, ExecutorActionErrorInfo)
            assert result.type == "SubprocessError"
            assert "Segmentation fault" in result.message

    @pytest.mark.anyio
    async def test_execute_action_success(
        self, temp_cache_dir, mock_run_action_input, mock_role
    ):
        """Test successful action execution."""
        runner = ActionRunner(cache_dir=temp_cache_dir)
        base_dir = temp_cache_dir / "base"
        base_dir.mkdir()

        import orjson

        success_response = orjson.dumps({"success": True, "result": {"data": "test"}})

        with patch("asyncio.create_subprocess_exec") as mock_subprocess:
            mock_proc = AsyncMock()
            mock_proc.returncode = 0
            mock_proc.communicate = AsyncMock(return_value=(success_response, b""))
            mock_subprocess.return_value = mock_proc

            result = await runner._execute_direct(
                input=mock_run_action_input,
                role=mock_role,
                registry_paths=[base_dir],
                secret_projection=_empty_secret_projection(),
                timeout=10.0,
            )

            assert result == {"data": "test"}

    @pytest.mark.anyio
    async def test_execute_action_error_response(
        self, temp_cache_dir, mock_run_action_input, mock_role
    ):
        """Test handling of error response from subprocess."""
        runner = ActionRunner(cache_dir=temp_cache_dir)
        base_dir = temp_cache_dir / "base"
        base_dir.mkdir()

        import orjson

        error_response = orjson.dumps(
            {
                "success": False,
                "result": None,
                "error": {
                    "type": "ValueError",
                    "message": "Invalid input",
                    "action_name": "test_action",
                    "filename": "<subprocess>",
                    "function": "execute_action",
                },
            }
        )

        with patch("asyncio.create_subprocess_exec") as mock_subprocess:
            mock_proc = AsyncMock()
            mock_proc.returncode = 0
            mock_proc.communicate = AsyncMock(return_value=(error_response, b""))
            mock_subprocess.return_value = mock_proc

            result = await runner._execute_direct(
                input=mock_run_action_input,
                role=mock_role,
                registry_paths=[base_dir],
                secret_projection=_empty_secret_projection(),
                timeout=10.0,
            )

            assert isinstance(result, ExecutorActionErrorInfo)
            assert result.type == "ValueError"
            assert result.message == "Invalid input"

    @pytest.mark.anyio
    async def test_execute_action_sets_sdk_context_env(
        self,
        temp_cache_dir,
        mock_run_action_input,
        mock_role,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """Test direct subprocess execution sets SDK auth/context env vars."""
        runner = ActionRunner(cache_dir=temp_cache_dir)
        base_dir = temp_cache_dir / "base"
        base_dir.mkdir()

        import orjson

        success_response = orjson.dumps({"success": True, "result": {"data": "test"}})
        captured_env: dict[str, str] = {}
        monkeypatch.setenv("TRACECAT__API_URL", "http://internal-api.invalid")

        resolved_context = ResolvedContext(
            secrets={},
            variables={},
            action_impl=ActionImplementation(
                type="udf",
                action_name="core.table.search_rows",
                module="tracecat_registry.core.table",
                name="search_rows",
            ),
            evaluated_args={"table": "customers"},
            workspace_id=str(mock_role.workspace_id),
            workflow_id=str(mock_run_action_input.run_context.wf_id),
            run_id=str(mock_run_action_input.run_context.wf_run_id),
            executor_token="test-executor-token",
        )

        async def create_subprocess_exec_side_effect(*args, **kwargs):  # noqa: ARG001
            env = kwargs.get("env")
            assert isinstance(env, dict)
            captured_env.update(env)

            mock_proc = AsyncMock()
            mock_proc.returncode = 0
            mock_proc.communicate = AsyncMock(return_value=(success_response, b""))
            return mock_proc

        with patch(
            "asyncio.create_subprocess_exec",
            side_effect=create_subprocess_exec_side_effect,
        ):
            result = await runner._execute_direct(
                input=mock_run_action_input,
                role=mock_role,
                registry_paths=[base_dir],
                secret_projection=_empty_secret_projection(),
                timeout=10.0,
                resolved_context=resolved_context,
            )

        assert result == {"data": "test"}
        assert "TRACECAT__API_URL" not in captured_env
        assert captured_env["TRACECAT__WORKSPACE_ID"] == resolved_context.workspace_id
        assert captured_env["TRACECAT__WORKFLOW_ID"] == resolved_context.workflow_id
        assert captured_env["TRACECAT__RUN_ID"] == resolved_context.run_id
        assert captured_env["TRACECAT__WF_EXEC_ID"] == str(
            mock_run_action_input.run_context.wf_exec_id
        )
        assert (
            captured_env["TRACECAT__ENVIRONMENT"]
            == mock_run_action_input.run_context.environment
        )
        assert captured_env["TRACECAT__EXECUTOR_TOKEN"] == "test-executor-token"

    @pytest.mark.anyio
    async def test_execute_action_sets_action_gateway_env(
        self,
        temp_cache_dir,
        mock_run_action_input,
        mock_role,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """Direct execution injects the mandatory gateway socket into SDK env."""
        runner = ActionRunner(cache_dir=temp_cache_dir)
        base_dir = temp_cache_dir / "base"
        base_dir.mkdir()

        monkeypatch.setattr(
            action_runner.config,
            "TRACECAT__ACTION_GATEWAY_SOCKET",
            "/var/run/tracecat/action-gateway.sock",
        )

        success_response = orjson.dumps({"success": True, "result": {"data": "test"}})
        captured_env: dict[str, str] = {}

        resolved_context = ResolvedContext(
            secrets={},
            variables={},
            action_impl=ActionImplementation(
                type="udf",
                action_name="core.table.search_rows",
                module="tracecat_registry.core.table",
                name="search_rows",
            ),
            evaluated_args={"table": "customers"},
            workspace_id=str(mock_role.workspace_id),
            workflow_id=str(mock_run_action_input.run_context.wf_id),
            run_id=str(mock_run_action_input.run_context.wf_run_id),
            executor_token="test-executor-token",
        )

        async def create_subprocess_exec_side_effect(*args, **kwargs):  # noqa: ARG001
            env = kwargs.get("env")
            assert isinstance(env, dict)
            captured_env.update(env)

            mock_proc = AsyncMock()
            mock_proc.returncode = 0
            mock_proc.communicate = AsyncMock(return_value=(success_response, b""))
            return mock_proc

        with patch(
            "asyncio.create_subprocess_exec",
            side_effect=create_subprocess_exec_side_effect,
        ):
            result = await runner._execute_direct(
                input=mock_run_action_input,
                role=mock_role,
                registry_paths=[base_dir],
                secret_projection=_empty_secret_projection(),
                timeout=10.0,
                resolved_context=resolved_context,
            )

        assert result == {"data": "test"}
        assert (
            captured_env["TRACECAT__ACTION_GATEWAY_SOCKET"]
            == "/var/run/tracecat/action-gateway.sock"
        )

    @pytest.mark.anyio
    async def test_execute_sandboxed_omits_api_url(
        self,
        temp_cache_dir,
        mock_run_action_input,
        mock_role,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Sandboxed actions expose the gateway socket, not the API address."""
        runner = ActionRunner(cache_dir=temp_cache_dir)
        base_dir = temp_cache_dir / "base"
        base_dir.mkdir()
        captured_env: dict[str, str] = {}

        resolved_context = ResolvedContext(
            secrets={},
            variables={},
            action_impl=ActionImplementation(
                type="udf",
                action_name="core.table.search_rows",
                module="tracecat_registry.core.table",
                name="search_rows",
            ),
            evaluated_args={"table": "customers"},
            workspace_id=str(mock_role.workspace_id),
            workflow_id=str(mock_run_action_input.run_context.wf_id),
            run_id=str(mock_run_action_input.run_context.wf_run_id),
            executor_token="test-executor-token",
        )

        class FakeNsjailExecutor:
            async def execute_action(
                self,
                job_dir: Path,
                sandbox_config: action_runner.ActionSandboxConfig,
            ) -> MagicMock:
                del job_dir
                captured_env.update(sandbox_config.env_vars)
                return MagicMock(success=True, output={"data": "test"})

        monkeypatch.setattr(action_runner, "NsjailExecutor", FakeNsjailExecutor)
        monkeypatch.setattr(
            action_runner,
            "mint_executor_token",
            lambda **_kwargs: "test-executor-token",
        )

        result = await runner._execute_sandboxed(
            input=mock_run_action_input,
            role=mock_role,
            registry_paths=[base_dir],
            secret_projection=_empty_secret_projection(),
            resolved_context=resolved_context,
            env_vars={"TRACECAT__API_URL": "http://internal-api.invalid"},
            timeout=10.0,
        )

        assert result == {"data": "test"}
        assert "TRACECAT__API_URL" not in captured_env
        assert captured_env["TRACECAT__ACTION_GATEWAY_SOCKET"] == str(
            action_runner.ACTION_GATEWAY_SANDBOX_SOCKET
        )

    @pytest.mark.anyio
    async def test_execute_action_disables_new_privileges_for_direct_subprocess(
        self,
        temp_cache_dir,
        mock_run_action_input,
        mock_role,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """Test direct subprocess execution disables new Linux privileges."""
        runner = ActionRunner(cache_dir=temp_cache_dir)
        base_dir = temp_cache_dir / "base"
        base_dir.mkdir()

        import orjson

        success_response = orjson.dumps({"success": True, "result": {"data": "test"}})
        captured_args: list[str] = []

        async def create_subprocess_exec_side_effect(*args, **kwargs):  # noqa: ARG001
            captured_args[:] = list(args)

            mock_proc = AsyncMock()
            mock_proc.returncode = 0
            mock_proc.communicate = AsyncMock(return_value=(success_response, b""))
            return mock_proc

        monkeypatch.setattr(action_runner.sys, "platform", "linux")
        monkeypatch.setattr(
            action_runner.shutil,
            "which",
            lambda name: "/usr/bin/setpriv" if name == "setpriv" else None,
        )

        with patch(
            "asyncio.create_subprocess_exec",
            side_effect=create_subprocess_exec_side_effect,
        ):
            result = await runner._execute_direct(
                input=mock_run_action_input,
                role=mock_role,
                registry_paths=[base_dir],
                secret_projection=_empty_secret_projection(),
                timeout=10.0,
            )

        assert result == {"data": "test"}
        assert captured_args[:4] == [
            "/usr/bin/setpriv",
            "--no-new-privs",
            "--inh-caps=-all",
            "--ambient-caps=-all",
        ]
        assert captured_args[-2] == action_runner.sys.executable
        assert captured_args[-1].endswith("minimal_runner.py")

    @pytest.mark.anyio
    async def test_execute_action_invalid_json_response(
        self, temp_cache_dir, mock_run_action_input, mock_role
    ):
        """Test handling of invalid JSON response from subprocess."""
        runner = ActionRunner(cache_dir=temp_cache_dir)
        base_dir = temp_cache_dir / "base"
        base_dir.mkdir()

        with patch("asyncio.create_subprocess_exec") as mock_subprocess:
            mock_proc = AsyncMock()
            mock_proc.returncode = 0
            mock_proc.communicate = AsyncMock(return_value=(b"not valid json {{{", b""))
            mock_subprocess.return_value = mock_proc

            result = await runner._execute_direct(
                input=mock_run_action_input,
                role=mock_role,
                registry_paths=[base_dir],
                secret_projection=_empty_secret_projection(),
                timeout=10.0,
            )

            assert isinstance(result, ExecutorActionErrorInfo)
            assert result.type == "ProtocolError"

    @pytest.mark.anyio
    async def test_execute_action_masks_stderr_on_subprocess_crash(
        self, temp_cache_dir, mock_run_action_input, mock_role
    ) -> None:
        runner = ActionRunner(cache_dir=temp_cache_dir)
        base_dir = temp_cache_dir / "base"
        base_dir.mkdir()

        with patch("asyncio.create_subprocess_exec") as mock_subprocess:
            mock_proc = AsyncMock()
            mock_proc.returncode = 17
            mock_proc.communicate = AsyncMock(
                return_value=(b"", b"token=temp_token secret=temp_secret")
            )
            mock_subprocess.return_value = mock_proc

            result = await runner._execute_direct(
                input=mock_run_action_input,
                role=mock_role,
                registry_paths=[base_dir],
                secret_projection=SecretEnvProjection(
                    env={},
                    mask_values={"temp_token", "temp_secret"},
                ),
                timeout=10.0,
            )

        assert isinstance(result, ExecutorActionErrorInfo)
        assert result.type == "SubprocessError"
        assert "temp_token" not in result.message
        assert "temp_secret" not in result.message
        assert "***" in result.message
