"""Tests for the ActionRunner.

These tests cover tarball caching, cache key computation, and execution logic.
"""

from __future__ import annotations

import asyncio
import contextlib
import tempfile
import uuid
from datetime import UTC, datetime
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
from tracecat.executor.registry_artifacts import compute_registry_artifact_cache_key
from tracecat.executor.schemas import (
    ActionImplementation,
    ExecutorActionErrorInfo,
    ResolvedContext,
)
from tracecat.executor.secret_preprocessors import SecretEnvProjection
from tracecat.identifiers.workflow import WorkflowUUID
from tracecat.registry.lock.types import RegistryLock
from tracecat.sandbox import utils as sandbox_utils
from tracecat.sandbox.types import SandboxResult


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

    @pytest.fixture(autouse=True)
    def mock_process_group_communication(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> AsyncMock:
        """Keep subprocess unit tests focused on ActionRunner behavior."""
        real_communication = action_runner.communicate_process_group

        async def communicate(
            process: asyncio.subprocess.Process | AsyncMock,
            *,
            input: bytes | None = None,  # noqa: A002
            timeout: float | None = None,
        ) -> tuple[bytes, bytes]:
            if isinstance(process, asyncio.subprocess.Process):
                return await real_communication(
                    process,
                    input=input,
                    timeout=timeout,
                )
            stdout, stderr = await asyncio.wait_for(
                process.communicate(input=input),
                timeout=timeout,
            )
            assert stdout is not None
            assert stderr is not None
            return stdout, stderr

        communication = AsyncMock(side_effect=communicate)
        monkeypatch.setattr(
            action_runner,
            "communicate_process_group",
            communication,
        )
        return communication

    @pytest.mark.anyio
    async def test_lease_without_artifacts_yields_base_pythonpath(self, temp_cache_dir):
        """Test that the base PYTHONPATH directory is used without artifacts."""
        runner = ActionRunner(cache_dir=temp_cache_dir)

        async with runner.registry_artifacts.lease(None) as registry_paths:
            assert registry_paths == [temp_cache_dir / "base"]

        async with runner.registry_artifacts.lease([]) as registry_paths:
            assert registry_paths == [temp_cache_dir / "base"]

    @pytest.mark.anyio
    async def test_execute_action_timeout(
        self,
        temp_cache_dir,
        mock_run_action_input,
        mock_role,
        mock_process_group_communication: AsyncMock,
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
            mock_process_group_communication.assert_awaited_once()

    @pytest.mark.anyio
    async def test_cancelled_direct_action_kills_and_reaps_subprocess(
        self, temp_cache_dir, mock_run_action_input, mock_role
    ) -> None:
        """Cancellation propagates only after the direct child is reaped."""
        runner = ActionRunner(cache_dir=temp_cache_dir)
        base_dir = temp_cache_dir / "base"
        base_dir.mkdir()
        real_create_subprocess_exec = asyncio.create_subprocess_exec
        process_started = asyncio.Event()
        process: asyncio.subprocess.Process | None = None

        async def capture_subprocess(*args, **kwargs):
            nonlocal process
            process = await real_create_subprocess_exec(*args, **kwargs)
            process_started.set()
            return process

        with (
            patch.object(
                action_runner,
                "_direct_subprocess_command",
                return_value=["/bin/sleep", "30"],
            ),
            patch(
                "tracecat.executor.action_runner.asyncio.create_subprocess_exec",
                side_effect=capture_subprocess,
            ),
        ):
            execution = asyncio.create_task(
                runner._execute_direct(
                    input=mock_run_action_input,
                    role=mock_role,
                    registry_paths=[base_dir],
                    secret_projection=_empty_secret_projection(),
                    timeout=60.0,
                )
            )
            try:
                await process_started.wait()
                await asyncio.sleep(0)
                execution.cancel()

                with pytest.raises(asyncio.CancelledError):
                    await execution

                assert process is not None
                assert process.returncode is not None
            finally:
                if process is not None and process.returncode is None:
                    process.kill()
                    await process.wait()

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
    async def test_sandbox_preserves_attested_executor_token(
        self,
        temp_cache_dir: Path,
        mock_run_action_input: RunActionInput,
        mock_role: Role,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        runner = ActionRunner(cache_dir=temp_cache_dir)
        base_dir = temp_cache_dir / "base"
        base_dir.mkdir()
        captured_env: dict[str, str] = {}
        resolved_context = ResolvedContext(
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
            executor_token="attested-executor-token",
        )

        async def execute_action(
            _executor: action_runner.NsjailExecutor,
            _job_dir: Path,
            sandbox_config: action_runner.ActionSandboxConfig,
        ) -> SandboxResult:
            captured_env.update(sandbox_config.env_vars)
            return SandboxResult(success=True, output={"data": "test"})

        monkeypatch.setattr(
            action_runner.NsjailExecutor,
            "execute_action",
            execute_action,
        )

        result = await runner._execute_sandboxed(
            input=mock_run_action_input,
            role=mock_role,
            registry_paths=[base_dir],
            secret_projection=_empty_secret_projection(),
            resolved_context=resolved_context,
        )

        assert result == {"data": "test"}
        assert (
            captured_env["TRACECAT__EXECUTOR_TOKEN"] == resolved_context.executor_token
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

    @pytest.mark.anyio
    async def test_execute_action_holds_registry_lease_for_whole_subprocess(
        self,
        temp_cache_dir: Path,
        mock_run_action_input: RunActionInput,
        mock_role: Role,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The artifact stays pinned until the action subprocess has exited."""
        runner = ActionRunner(cache_dir=temp_cache_dir)
        artifact_uri = "s3://bucket/execute.tar.gz"
        cache_key = compute_registry_artifact_cache_key(artifact_uri)
        entry_dir = runner.registry_artifacts._paths_for(cache_key).tarball_target_dir
        entry_dir.mkdir(parents=True)

        monkeypatch.setattr(
            action_runner.config, "TRACECAT__EXECUTOR_SANDBOX_ENABLED", False
        )

        success_response = orjson.dumps({"success": True, "result": {"data": "test"}})
        refcounts: list[int] = []
        registry_paths: list[str] = []

        resolved_context = ResolvedContext(
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
            secret_projection=_empty_secret_projection(),
        )

        async def create_subprocess_exec_side_effect(*args, **kwargs):  # noqa: ARG001
            refcounts.append(runner.registry_artifacts._refcount(cache_key))
            env = kwargs.get("env")
            assert isinstance(env, dict)
            registry_paths.append(env["PYTHONPATH"])

            mock_proc = AsyncMock()
            mock_proc.returncode = 0
            mock_proc.communicate = AsyncMock(return_value=(success_response, b""))
            return mock_proc

        with patch(
            "asyncio.create_subprocess_exec",
            side_effect=create_subprocess_exec_side_effect,
        ):
            result = await runner.execute_action(
                input=mock_run_action_input,
                role=mock_role,
                resolved_context=resolved_context,
                artifact_uris=[artifact_uri],
                timeout=10.0,
            )

        assert result == {"data": "test"}
        assert refcounts == [1]
        assert registry_paths[0].startswith(str(entry_dir))
        assert runner.registry_artifacts._refcount(cache_key) == 0

    @pytest.mark.anyio
    async def test_cancelled_action_reaps_child_before_releasing_mounted_artifact(
        self,
        temp_cache_dir: Path,
        mock_run_action_input: RunActionInput,
        mock_role: Role,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Protect the subprocess-to-cache ownership boundary on cancellation.

        Cancellation must kill and reap the action before dropping its registry
        pin; only then may final release unmount the artifact. This prevents a
        child from importing through a reclaimed mount while still retaining
        the reusable SquashFS image for the next action.
        """
        runner = ActionRunner(cache_dir=temp_cache_dir)
        cache = runner.registry_artifacts
        artifact_uri = "s3://bucket/cancelled-action.squashfs"
        cache_key = compute_registry_artifact_cache_key(artifact_uri)
        paths = cache._paths_for(cache_key)
        paths.entry_dir.mkdir(parents=True)
        paths.squashfs_image_path.write_bytes(b"squashfs")
        paths.squashfs_mount_dir.mkdir()
        (paths.squashfs_mount_dir / "module.py").write_text("VALUE = 1")
        mounted = {paths.squashfs_mount_dir}

        monkeypatch.setattr(
            action_runner.config, "TRACECAT__EXECUTOR_SANDBOX_ENABLED", False
        )

        resolved_context = ResolvedContext(
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
            secret_projection=_empty_secret_projection(),
        )

        real_create_subprocess_exec = asyncio.create_subprocess_exec
        real_terminate_process_group = sandbox_utils.terminate_process_group
        process_started = asyncio.Event()
        termination_started = asyncio.Event()
        finish_termination = asyncio.Event()
        process: asyncio.subprocess.Process | None = None
        reaped_before_unmount: list[bool] = []

        async def capture_subprocess(*args, **kwargs):
            nonlocal process
            process = await real_create_subprocess_exec(*args, **kwargs)
            process_started.set()
            return process

        async def controlled_termination(
            requested_process: asyncio.subprocess.Process,
        ) -> None:
            termination_started.set()
            await finish_termination.wait()
            await real_terminate_process_group(requested_process)

        async def release_mount(mount_dir: Path) -> bool:
            reaped_before_unmount.append(
                process is not None and process.returncode is not None
            )
            mounted.discard(mount_dir)
            return True

        with (
            patch.object(Path, "is_mount", lambda path: path in mounted),
            patch.object(
                action_runner,
                "_direct_subprocess_command",
                return_value=["/bin/sleep", "30"],
            ),
            patch(
                "tracecat.executor.action_runner.asyncio.create_subprocess_exec",
                side_effect=capture_subprocess,
            ),
            patch.object(
                sandbox_utils,
                "terminate_process_group",
                side_effect=controlled_termination,
            ),
            patch.object(cache, "_unmount", side_effect=release_mount),
        ):
            execution = asyncio.create_task(
                runner.execute_action(
                    input=mock_run_action_input,
                    role=mock_role,
                    resolved_context=resolved_context,
                    artifact_uris=[artifact_uri],
                    timeout=60.0,
                )
            )
            try:
                await asyncio.wait_for(process_started.wait(), timeout=5)
                assert cache._refcount(cache_key) == 1
                execution.cancel()
                await termination_started.wait()

                execution.cancel()
                await asyncio.sleep(0)
                assert not execution.done()
                assert cache._refcount(cache_key) == 1
                assert paths.squashfs_mount_dir in mounted

                finish_termination.set()
                with pytest.raises(asyncio.CancelledError):
                    await execution
            finally:
                finish_termination.set()
                if not execution.done():
                    execution.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await execution
                if process is not None and process.returncode is None:
                    process.kill()
                    await process.wait()

        assert process is not None
        assert process.returncode is not None
        assert reaped_before_unmount == [True]
        assert cache._refcount(cache_key) == 0
        assert paths.squashfs_mount_dir not in mounted
        assert paths.squashfs_image_path.is_file()
