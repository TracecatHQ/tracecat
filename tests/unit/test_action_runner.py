"""Tests for the ActionRunner.

These tests cover tarball caching, cache key computation, and execution logic.
"""

from __future__ import annotations

import asyncio
import base64
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
from tracecat.executor.action_runner import ActionRunner, _parse_s3_uri
from tracecat.executor.schemas import (
    ActionImplementation,
    ExecutorActionErrorInfo,
    ResolvedContext,
)
from tracecat.identifiers.workflow import WorkflowUUID
from tracecat.registry.lock.types import RegistryLock
from tracecat.sandbox.executor import ActionSandboxConfig, NsjailExecutor


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


@contextmanager
def _mock_outbound_http_gateway(expected_token: str):
    """Start a lightweight outbound HTTP gateway server."""
    state: dict[str, object] = {"requests": []}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            content_length = int(self.headers.get("Content-Length", "0"))
            raw_body = self.rfile.read(content_length)
            payload = json.loads(raw_body.decode() or "{}")

            requests = state["requests"]
            if isinstance(requests, list):
                requests.append(
                    {
                        "path": self.path,
                        "payload": payload,
                        "authorization": self.headers.get("Authorization"),
                        "metadata": {
                            key: value
                            for key, value in self.headers.items()
                            if key.startswith("X-Tracecat-")
                        },
                    }
                )

            if self.path != "/v1/dev-proxy/dispatch":
                self.send_response(404)
                self.end_headers()
                return

            if self.headers.get("Authorization") != f"Bearer {expected_token}":
                self.send_response(401)
                self.end_headers()
                return

            response_payload = {
                "status_code": 200,
                "headers": {"Content-Type": "application/json"},
                "body_base64": base64.b64encode(b'{"proxied":true}').decode("ascii"),
                "url": payload.get("url"),
                "reason_phrase": "OK",
            }
            encoded = json.dumps(response_payload).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def log_message(self, *args, **kwargs) -> None:  # noqa: ANN002, ANN003
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{server.server_address[1]}"
    dispatch_url = f"{base_url}/v1/dev-proxy/dispatch"
    try:
        yield dispatch_url, state
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


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


class TestParseS3Uri:
    """Tests for _parse_s3_uri function."""

    def test_valid_uri(self):
        """Test parsing a valid S3 URI."""
        bucket, key = _parse_s3_uri("s3://my-bucket/path/to/file.tar.gz")
        assert bucket == "my-bucket"
        assert key == "path/to/file.tar.gz"

    def test_uri_with_nested_path(self):
        """Test parsing URI with deeply nested path."""
        bucket, key = _parse_s3_uri("s3://bucket/a/b/c/d/e/file.tar.gz")
        assert bucket == "bucket"
        assert key == "a/b/c/d/e/file.tar.gz"

    def test_invalid_uri_no_prefix(self):
        """Test that non-S3 URIs raise ValueError."""
        with pytest.raises(ValueError, match="Invalid S3 URI"):
            _parse_s3_uri("https://bucket/key")

    def test_invalid_uri_no_key(self):
        """Test that URIs without keys raise ValueError."""
        with pytest.raises(ValueError, match="Invalid S3 URI"):
            _parse_s3_uri("s3://bucket")

    def test_invalid_uri_empty_bucket(self):
        """Test that URIs with empty bucket raise ValueError."""
        with pytest.raises(ValueError, match="Invalid S3 URI"):
            _parse_s3_uri("s3:///key")


class TestActionRunner:
    """Tests for ActionRunner class."""

    def test_compute_tarball_cache_key_deterministic(self, temp_cache_dir):
        """Test that cache key computation is deterministic."""
        runner = ActionRunner(cache_dir=temp_cache_dir)
        uri = "s3://bucket/path/to/registry-v1.2.3.tar.gz"

        key1 = runner.compute_tarball_cache_key(uri)
        key2 = runner.compute_tarball_cache_key(uri)

        assert key1 == key2
        assert len(key1) == 16  # SHA256[:16]

    def test_compute_tarball_cache_key_case_sensitive(self, temp_cache_dir):
        """Test that cache key is case-sensitive (S3 keys are case-sensitive)."""
        runner = ActionRunner(cache_dir=temp_cache_dir)

        key1 = runner.compute_tarball_cache_key("s3://BUCKET/PATH/FILE.tar.gz")
        key2 = runner.compute_tarball_cache_key("s3://bucket/path/file.tar.gz")

        # S3 keys are case-sensitive, so different cases should produce different keys
        assert key1 != key2

    def test_build_action_env_map_preserves_registry_pythonpath_when_user_value_empty(
        self, tmp_path: Path
    ) -> None:
        executor = NsjailExecutor()
        registry_path = tmp_path / "registry"
        registry_path.mkdir()

        env_map = executor._build_action_env_map(
            ActionSandboxConfig(
                registry_paths=[registry_path],
                tracecat_app_dir=tmp_path,
                env_vars={"PYTHONPATH": ""},
            )
        )

        assert env_map["PYTHONPATH"] == "/packages/0"

    def test_compute_tarball_cache_key_different_uris(self, temp_cache_dir):
        """Test that different URIs produce different cache keys."""
        runner = ActionRunner(cache_dir=temp_cache_dir)

        key1 = runner.compute_tarball_cache_key("s3://bucket/v1.tar.gz")
        key2 = runner.compute_tarball_cache_key("s3://bucket/v2.tar.gz")

        assert key1 != key2

    def test_compute_tarball_cache_key_empty(self, temp_cache_dir):
        """Test that empty URI returns 'base'."""
        runner = ActionRunner(cache_dir=temp_cache_dir)

        key = runner.compute_tarball_cache_key("")
        assert key == "base"

    def test_compute_tarball_cache_key_strips_whitespace(self, temp_cache_dir):
        """Test that whitespace is stripped."""
        runner = ActionRunner(cache_dir=temp_cache_dir)

        key1 = runner.compute_tarball_cache_key("s3://bucket/file.tar.gz")
        key2 = runner.compute_tarball_cache_key("  s3://bucket/file.tar.gz  ")

        assert key1 == key2

    @pytest.mark.anyio
    async def test_execute_action_errors_when_outbound_http_gateway_missing(
        self,
        temp_cache_dir: Path,
        mock_role: Role,
        mock_run_action_input: RunActionInput,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        runner = ActionRunner(cache_dir=temp_cache_dir)
        resolved_context = ResolvedContext(
            secrets={},
            variables={},
            action_impl=ActionImplementation(
                type="udf",
                action_name=mock_run_action_input.task.action,
                module="tracecat_registry.core.http",
                name="http_request",
                origin="tracecat_registry",
            ),
            evaluated_args=dict(mock_run_action_input.task.args),
            workspace_id=str(mock_role.workspace_id),
            workflow_id=str(mock_run_action_input.run_context.wf_id),
            run_id=str(mock_run_action_input.run_context.wf_run_id),
            executor_token="token",
            logical_time=datetime.now(UTC),
        )
        monkeypatch.setattr(
            "tracecat.executor.action_runner.config.TRACECAT__OUTBOUND_HTTP_GATEWAY_URL",
            None,
        )

        result = await runner.execute_action(
            input=mock_run_action_input.model_copy(
                update={"outbound_http_interception_enabled": True}
            ),
            role=mock_role,
            resolved_context=resolved_context,
            tarball_uris=[],
            force_sandbox=False,
        )

        assert isinstance(result, ExecutorActionErrorInfo)
        assert result.type == "OutboundHTTPInterceptionConfigurationError"

    @pytest.mark.anyio
    async def test_execute_direct_injects_outbound_http_gateway_bootstrap(
        self,
        temp_cache_dir: Path,
        mock_role: Role,
        mock_run_action_input: RunActionInput,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        runner = ActionRunner(cache_dir=temp_cache_dir)
        resolved_context = ResolvedContext(
            secrets={},
            variables={},
            action_impl=ActionImplementation(
                type="udf",
                action_name=mock_run_action_input.task.action,
                module="tracecat_registry.core.http",
                name="http_request",
                origin="tracecat_registry",
            ),
            evaluated_args=dict(mock_run_action_input.task.args),
            workspace_id=str(mock_role.workspace_id),
            workflow_id=str(mock_run_action_input.run_context.wf_id),
            run_id=str(mock_run_action_input.run_context.wf_run_id),
            executor_token="token",
            logical_time=datetime.now(UTC),
        )
        input_with_outbound_http_interception = mock_run_action_input.model_copy(
            update={"outbound_http_interception_enabled": True}
        )
        captured_env: dict[str, str] = {}

        class FakeProcess:
            returncode = 0

            async def communicate(
                self, input: bytes | None = None
            ) -> tuple[bytes, bytes]:
                _ = input
                return (
                    orjson.dumps({"success": True, "result": {"ok": True}}),
                    b"",
                )

        async def fake_create_subprocess_exec(
            *args: object, **kwargs: object
        ) -> FakeProcess:
            _ = args
            env = kwargs["env"]
            assert isinstance(env, dict)
            captured_env.update(env)
            pythonpath = env.get("PYTHONPATH", "")
            bootstrap_dir = Path(pythonpath.split(":")[0])
            assert (bootstrap_dir / "sitecustomize.py").exists()
            assert (bootstrap_dir / "outbound_http_gateway_bootstrap.py").exists()
            return FakeProcess()

        monkeypatch.setattr(
            "tracecat.executor.action_runner.asyncio.create_subprocess_exec",
            fake_create_subprocess_exec,
        )
        monkeypatch.setattr(
            "tracecat.executor.action_runner.config.TRACECAT__OUTBOUND_HTTP_GATEWAY_URL",
            "https://gateway.example.com/v1/dev-proxy/dispatch",
        )

        result = await runner._execute_direct(
            input=input_with_outbound_http_interception,
            role=mock_role,
            registry_paths=[temp_cache_dir],
            resolved_context=resolved_context,
        )

        assert result == {"ok": True}
        assert captured_env["TRACECAT__OUTBOUND_HTTP_GATEWAY_ENABLED"] == "1"
        assert (
            captured_env["TRACECAT__OUTBOUND_HTTP_GATEWAY_URL"]
            == "https://gateway.example.com/v1/dev-proxy/dispatch"
        )

    @pytest.mark.anyio
    async def test_ensure_tarball_extracted_caches_result(self, temp_cache_dir):
        """Test that tarball extraction is cached."""
        runner = ActionRunner(cache_dir=temp_cache_dir)
        cache_key = "test-cache-key"

        # Create the target directory to simulate cached result
        target_dir = temp_cache_dir / f"tarball-{cache_key}"
        target_dir.mkdir(parents=True)

        # Should return immediately without downloading
        result = await runner.ensure_tarball_extracted(
            cache_key, "s3://bucket/test.tar.gz"
        )

        assert result == target_dir

    @pytest.mark.anyio
    async def test_ensure_tarball_extracted_concurrent_requests(self, temp_cache_dir):
        """Test that concurrent requests for same tarball don't race."""
        runner = ActionRunner(cache_dir=temp_cache_dir)
        cache_key = "concurrent-test"
        download_count = 0

        async def mock_download(_url, path):
            nonlocal download_count
            download_count += 1
            await asyncio.sleep(0.1)  # Simulate download time
            path.write_bytes(b"fake tarball content")

        async def mock_extract(_tarball_path, target_dir):
            # Create a dummy file to simulate extraction
            (target_dir / "extracted.txt").write_text("extracted")

        with (
            patch.object(runner, "_download_file", mock_download),
            patch.object(runner, "_extract_tarball", mock_extract),
            patch.object(
                runner,
                "_tarball_uri_to_http_url",
                new_callable=AsyncMock,
                return_value="http://test",
            ),
        ):
            # Start multiple concurrent requests
            results = await asyncio.gather(
                runner.ensure_tarball_extracted(cache_key, "s3://bucket/test.tar.gz"),
                runner.ensure_tarball_extracted(cache_key, "s3://bucket/test.tar.gz"),
                runner.ensure_tarball_extracted(cache_key, "s3://bucket/test.tar.gz"),
            )

            # All should return the same path
            assert all(r == results[0] for r in results)

            # Should only download once due to locking
            assert download_count == 1

    @pytest.mark.anyio
    async def test_ensure_registry_environment_no_tarball(self, temp_cache_dir):
        """Test that None is returned when no tarball URI provided."""
        runner = ActionRunner(cache_dir=temp_cache_dir)

        result = await runner.ensure_registry_environment(None)
        assert result is None

        result = await runner.ensure_registry_environment("")
        assert result is None

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
    async def test_execute_action_sets_outbound_http_gateway_env(
        self, temp_cache_dir, mock_run_action_input, mock_role, monkeypatch
    ) -> None:
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
                action_name="core.http_request",
                module="tracecat_registry.core.http",
                name="http_request",
            ),
            evaluated_args={"url": "https://example.com"},
            workspace_id=str(mock_role.workspace_id),
            workflow_id=str(mock_run_action_input.run_context.wf_id),
            run_id=str(mock_run_action_input.run_context.wf_run_id),
            executor_token="test-executor-token",
        )
        input_with_outbound_http_interception = mock_run_action_input.model_copy(
            update={
                "outbound_http_interception_enabled": True,
                "run_context": mock_run_action_input.run_context.model_copy(
                    update={
                        "trigger_type": "manual",
                        "execution_type": "draft",
                    }
                ),
            }
        )

        async def create_subprocess_exec_side_effect(*args, **kwargs):  # noqa: ARG001
            env = kwargs.get("env")
            assert isinstance(env, dict)
            captured_env.update(env)
            mock_proc = AsyncMock()
            mock_proc.returncode = 0
            mock_proc.communicate = AsyncMock(return_value=(success_response, b""))
            return mock_proc

        monkeypatch.setattr(
            config,
            "TRACECAT__OUTBOUND_HTTP_GATEWAY_URL",
            "http://127.0.0.1:9999/v1/dev-proxy/dispatch",
        )

        with patch(
            "asyncio.create_subprocess_exec",
            side_effect=create_subprocess_exec_side_effect,
        ):
            result = await runner._execute_direct(
                input=input_with_outbound_http_interception,
                role=mock_role,
                registry_paths=[base_dir],
                timeout=10.0,
                resolved_context=resolved_context,
            )

        assert result == {"data": "test"}
        assert captured_env["TRACECAT__OUTBOUND_HTTP_GATEWAY_ENABLED"] == "1"
        assert (
            captured_env["TRACECAT__OUTBOUND_HTTP_GATEWAY_URL"]
            == "http://127.0.0.1:9999/v1/dev-proxy/dispatch"
        )
        assert (
            captured_env["TRACECAT__OUTBOUND_HTTP_GATEWAY_AUTH_TOKEN"]
            == "test-executor-token"
        )
        assert captured_env["TRACECAT__OUTBOUND_HTTP_GATEWAY_SOURCE"] == "workflow"
        assert (
            captured_env["TRACECAT__OUTBOUND_HTTP_GATEWAY_ACTION_NAME"]
            == "core.http_request"
        )
        assert captured_env["TRACECAT__OUTBOUND_HTTP_GATEWAY_EXECUTION_TYPE"] == "draft"
        assert captured_env["TRACECAT__OUTBOUND_HTTP_GATEWAY_TRIGGER_TYPE"] == "manual"
        assert "tracecat_outbound_http_gateway_" in captured_env["PYTHONPATH"]

    @pytest.mark.anyio
    async def test_execute_sandboxed_sets_outbound_http_gateway_env(
        self, temp_cache_dir, mock_run_action_input, mock_role, monkeypatch
    ) -> None:
        runner = ActionRunner(cache_dir=temp_cache_dir)
        base_dir = temp_cache_dir / "base"
        base_dir.mkdir()

        resolved_context = ResolvedContext(
            secrets={},
            variables={},
            action_impl=ActionImplementation(
                type="udf",
                action_name="core.http_request",
                module="tracecat_registry.core.http",
                name="http_request",
            ),
            evaluated_args={"url": "https://example.com"},
            workspace_id=str(mock_role.workspace_id),
            workflow_id=str(mock_run_action_input.run_context.wf_id),
            run_id=str(mock_run_action_input.run_context.wf_run_id),
            executor_token="test-executor-token",
        )
        input_with_outbound_http_interception = mock_run_action_input.model_copy(
            update={
                "outbound_http_interception_enabled": True,
                "run_context": mock_run_action_input.run_context.model_copy(
                    update={
                        "trigger_type": "manual",
                        "execution_type": "draft",
                    }
                ),
            }
        )
        captured: dict[str, object] = {}

        async def fake_execute_action(job_dir: Path, sandbox_config: object) -> object:
            captured["job_dir"] = job_dir
            captured["sandbox_config"] = sandbox_config
            assert (job_dir / "sitecustomize.py").exists()
            assert (job_dir / "outbound_http_gateway_bootstrap.py").exists()
            return MagicMock(success=True, output={"ok": True}, exit_code=0)

        monkeypatch.setattr(
            config,
            "TRACECAT__OUTBOUND_HTTP_GATEWAY_URL",
            "https://gateway.example.com/v1/dev-proxy/dispatch",
        )
        monkeypatch.setattr(
            "tracecat.executor.action_runner.mint_executor_token",
            lambda **_kwargs: "sandbox-token",
        )
        with patch.object(
            NsjailExecutor,
            "execute_action",
            side_effect=fake_execute_action,
        ):
            result = await runner._execute_sandboxed(
                input=input_with_outbound_http_interception,
                role=mock_role,
                registry_paths=[base_dir],
                timeout=10.0,
                resolved_context=resolved_context,
            )

        assert result == {"ok": True}
        sandbox_config = captured["sandbox_config"]
        assert isinstance(sandbox_config, ActionSandboxConfig)
        assert sandbox_config.env_vars["PYTHONPATH"] == "/work"
        assert sandbox_config.env_vars["TRACECAT__OUTBOUND_HTTP_GATEWAY_ENABLED"] == "1"
        assert (
            sandbox_config.env_vars["TRACECAT__OUTBOUND_HTTP_GATEWAY_URL"]
            == "https://gateway.example.com/v1/dev-proxy/dispatch"
        )
        assert (
            sandbox_config.env_vars["TRACECAT__OUTBOUND_HTTP_GATEWAY_SOURCE"]
            == "workflow"
        )
        assert (
            sandbox_config.env_vars["TRACECAT__OUTBOUND_HTTP_GATEWAY_EXECUTION_TYPE"]
            == "draft"
        )
        assert (
            sandbox_config.env_vars["TRACECAT__OUTBOUND_HTTP_GATEWAY_TRIGGER_TYPE"]
            == "manual"
        )
        assert (
            sandbox_config.env_vars["TRACECAT__OUTBOUND_HTTP_GATEWAY_BACKEND"]
            == "ephemeral"
        )
        assert (
            sandbox_config.env_vars["TRACECAT__OUTBOUND_HTTP_GATEWAY_AUTH_TOKEN"]
            == "sandbox-token"
        )

    @pytest.mark.anyio
    async def test_execute_action_dispatches_requests_through_outbound_http_gateway(
        self, temp_cache_dir, mock_run_action_input, mock_role, monkeypatch
    ) -> None:
        runner = ActionRunner(cache_dir=temp_cache_dir)
        package_dir = temp_cache_dir / "package"
        package_dir.mkdir()
        (package_dir / "dev_proxy_action.py").write_text(
            "import requests\n"
            "\n"
            "def make_request(url: str, header_value: str) -> dict:\n"
            "    response = requests.get(url, headers={'X-Test': header_value}, timeout=5)\n"
            "    response.raise_for_status()\n"
            "    return response.json()\n",
            encoding="utf-8",
        )

        resolved_context = ResolvedContext(
            secrets={},
            variables={},
            action_impl=ActionImplementation(
                type="udf",
                action_name="testing.dev_proxy.make_request",
                module="dev_proxy_action",
                name="make_request",
            ),
            evaluated_args={
                "url": "https://example.com/inspect?hello=world",
                "header_value": "tracecat",
            },
            workspace_id=str(mock_role.workspace_id),
            workflow_id=str(mock_run_action_input.run_context.wf_id),
            run_id=str(mock_run_action_input.run_context.wf_run_id),
            executor_token="test-executor-token",
        )
        input_with_outbound_http_interception = mock_run_action_input.model_copy(
            update={
                "outbound_http_interception_enabled": True,
                "task": mock_run_action_input.task.model_copy(
                    update={"action": "testing.dev_proxy.make_request"}
                ),
                "run_context": mock_run_action_input.run_context.model_copy(
                    update={
                        "trigger_type": "manual",
                        "execution_type": "draft",
                    }
                ),
            }
        )

        with _mock_outbound_http_gateway("test-executor-token") as (
            gateway_url,
            state,
        ):
            monkeypatch.setattr(
                config,
                "TRACECAT__OUTBOUND_HTTP_GATEWAY_URL",
                gateway_url,
            )
            result = await runner._execute_direct(
                input=input_with_outbound_http_interception,
                role=mock_role,
                registry_paths=[package_dir],
                timeout=10.0,
                resolved_context=resolved_context,
            )

        assert result == {"proxied": True}
        requests = state["requests"]
        assert isinstance(requests, list)
        assert len(requests) == 1
        request = requests[0]
        assert request["path"] == "/v1/dev-proxy/dispatch"
        assert request["authorization"] == "Bearer test-executor-token"
        assert request["payload"]["method"] == "GET"
        assert request["payload"]["url"] == "https://example.com/inspect?hello=world"
        assert request["payload"]["headers"]["X-Test"] == "tracecat"
        assert request["metadata"]["X-Tracecat-Source"] == "workflow"

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
            result = await runner._execute_direct(
                input=mock_run_action_input,
                role=mock_role,
                registry_paths=[base_dir],
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
                timeout=10.0,
            )

            assert isinstance(result, ExecutorActionErrorInfo)
            assert result.type == "ProtocolError"

    @pytest.mark.anyio
    async def test_get_extraction_lock_same_key(self, temp_cache_dir):
        """Test that same cache key returns same lock."""
        runner = ActionRunner(cache_dir=temp_cache_dir)

        lock1 = await runner._get_extraction_lock("key1")
        lock2 = await runner._get_extraction_lock("key1")

        assert lock1 is lock2

    @pytest.mark.anyio
    async def test_get_extraction_lock_different_keys(self, temp_cache_dir):
        """Test that different cache keys return different locks."""
        runner = ActionRunner(cache_dir=temp_cache_dir)

        lock1 = await runner._get_extraction_lock("key1")
        lock2 = await runner._get_extraction_lock("key2")

        assert lock1 is not lock2
