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

import pytest

from tracecat import config
from tracecat.auth.types import Role
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


@pytest.fixture
def mock_role() -> Role:
    """Create a mock role for testing."""
    return Role(
        type="service",
        service_id="tracecat-executor",
        workspace_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
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
