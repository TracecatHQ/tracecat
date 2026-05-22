"""Tests for the ActionRunner.

These tests cover tarball caching, cache key computation, and execution logic.
"""

from __future__ import annotations

import asyncio
import json
import tempfile
import threading
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import orjson
import pytest

from tracecat import config
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


@contextmanager
def _mock_tracecat_api_server(expected_token: str):
    """Start a lightweight HTTP server for SDK integration testing."""
    state: dict[str, object] = {"requests": []}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            content_length = int(self.headers.get("Content-Length", "0"))
            raw_body = self.rfile.read(content_length)
            payload = json.loads(raw_body.decode() or "{}")
            authorization = self.headers.get("Authorization")

            requests = state["requests"]
            if isinstance(requests, list):
                requests.append(
                    {
                        "path": self.path,
                        "payload": payload,
                        "authorization": authorization,
                    }
                )

            if self.path != "/internal/tables/customers/search":
                self.send_response(404)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"detail":"Not Found"}')
                return

            if authorization != f"Bearer {expected_token}":
                self.send_response(401)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"detail":"Unauthorized"}')
                return

            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'[{"id":"row-1","name":"Alice"}]')

        def log_message(self, *args, **kwargs) -> None:  # noqa: ANN002, ANN003
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        yield base_url, state
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


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
        self, temp_cache_dir, mock_run_action_input, mock_role
    ):
        """Test direct subprocess execution sets SDK auth/context env vars."""
        runner = ActionRunner(cache_dir=temp_cache_dir)
        base_dir = temp_cache_dir / "base"
        base_dir.mkdir()

        import orjson

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
        assert captured_env["TRACECAT__API_URL"] == config.TRACECAT__API_URL
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
    async def test_execute_action_sets_action_gateway_env_when_enabled(
        self,
        temp_cache_dir,
        mock_run_action_input,
        mock_role,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """Action gateway mode injects the local socket path into action SDK env."""
        runner = ActionRunner(cache_dir=temp_cache_dir)
        base_dir = temp_cache_dir / "base"
        base_dir.mkdir()

        monkeypatch.setattr(
            action_runner.config, "TRACECAT__ACTION_GATEWAY_ENABLED", True
        )
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
    async def test_execute_action_registry_sdk_call_succeeds(
        self, temp_cache_dir, mock_run_action_input, mock_role, monkeypatch
    ):
        """Test direct subprocess can execute a real registry SDK table call."""
        runner = ActionRunner(cache_dir=temp_cache_dir)
        base_dir = temp_cache_dir / "base"
        base_dir.mkdir()

        resolved_context = ResolvedContext(
            secrets={},
            variables={},
            action_impl=ActionImplementation(
                type="udf",
                action_name="core.table.search_rows",
                module="tracecat_registry.core.table",
                name="search_rows",
            ),
            evaluated_args={"table": "customers", "search_term": "alice", "limit": 1},
            workspace_id=str(mock_role.workspace_id),
            workflow_id=str(mock_run_action_input.run_context.wf_id),
            run_id=str(mock_run_action_input.run_context.wf_run_id),
            executor_token="test-executor-token",
        )

        with _mock_tracecat_api_server("test-executor-token") as (api_url, state):
            monkeypatch.setattr(config, "TRACECAT__API_URL", api_url)
            monkeypatch.setattr(config, "TRACECAT__ACTION_GATEWAY_ENABLED", False)
            result = await runner._execute_direct(
                input=mock_run_action_input,
                role=mock_role,
                registry_paths=[base_dir],
                secret_projection=_empty_secret_projection(),
                timeout=10.0,
                resolved_context=resolved_context,
            )

        assert result == [{"id": "row-1", "name": "Alice"}]
        requests = state["requests"]
        assert isinstance(requests, list)
        assert len(requests) == 1
        request = requests[0]
        assert request["path"] == "/internal/tables/customers/search"
        assert request["authorization"] == "Bearer test-executor-token"
        assert request["payload"] == {
            "search_term": "alice",
            "limit": 1,
            "reverse": False,
        }

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
