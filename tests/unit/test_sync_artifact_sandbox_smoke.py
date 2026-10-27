"""Lifecycle regressions for the standalone sync-to-sandbox smoke child."""

from __future__ import annotations

import tempfile
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import pytest
from pytest_mock import MockerFixture
from sqlalchemy.ext.asyncio import AsyncSession

from tests.integration import test_syncv2_execv2_e2e as smoke
from tracecat.auth.types import Role
from tracecat.dsl.schemas import RunActionInput
from tracecat.executor.schemas import ExecutorResultSuccess, ResolvedContext


@pytest.mark.anyio
@pytest.mark.parametrize("execution_fails", [False, True])
async def test_sync_artifact_smoke_owns_real_gateway(
    mocker: MockerFixture,
    execution_fails: bool,
) -> None:
    """The child serves a real gateway during execution and stops it on any exit."""
    session = mocker.AsyncMock(spec=AsyncSession)

    @asynccontextmanager
    async def session_context() -> AsyncIterator[AsyncSession]:
        yield session

    mocker.patch(
        "tracecat.db.engine.get_async_session_bypass_rls_context_manager",
        new=session_context,
    )
    mocker.patch("tracecat.db.engine.reset_async_engine")
    mocker.patch.object(smoke.importlib, "reload", return_value=smoke.blob)
    mocker.patch.object(smoke.blob, "file_exists", return_value=True)
    repo_service = mocker.patch.object(smoke, "PlatformRegistryReposService")
    repo_service.return_value.get_or_create_repository = mocker.AsyncMock()
    sync_service = mocker.patch.object(smoke, "PlatformRegistrySyncService")
    sync_service.return_value.sync_repository_v2 = mocker.AsyncMock(
        return_value=mocker.Mock(
            artifact_uri="s3://registry/platform/tarball-venvs/example/v1/site-packages.squashfs"
        )
    )
    # Restore the child's process-global configuration assignments after each
    # unit test. The actual Docker entry point runs in its own process.
    mocker.patch.multiple(
        smoke.config,
        TRACECAT__REGISTRY_SYNC_SANDBOX_ENABLED=False,
        TRACECAT__REGISTRY_SYNC_SQUASHFS_ENABLED=True,
        TRACECAT__REGISTRY_SYNC_BUILTIN_USE_INSTALLED_SITE_PACKAGES=True,
        TRACECAT__EXECUTOR_REGISTRY_SQUASHFS_ENABLED=True,
        TRACECAT__EXECUTOR_BACKEND=smoke.config.ExecutorBackendType.NSJAIL,
    )

    # Keep the Unix socket below the macOS pathname limit, regardless of the
    # checkout and pytest temporary-directory path lengths.
    with tempfile.TemporaryDirectory(prefix="tc-smoke-gw-", dir="/tmp") as temp_dir:
        socket_path = Path(temp_dir) / "gateway.sock"
        mocker.patch.object(
            smoke.config, "TRACECAT__ACTION_GATEWAY_SOCKET", str(socket_path)
        )

        async def execute(
            input: RunActionInput,
            role: Role,
            *,
            resolved_context: ResolvedContext,
            timeout: float,
        ) -> ExecutorResultSuccess:
            assert socket_path.is_socket()
            async with httpx.AsyncClient(
                transport=httpx.AsyncHTTPTransport(uds=str(socket_path)),
            ) as client:
                response = await client.get("http://action-gateway/internal/health")
            assert response.status_code == 200
            assert response.json() == {"status": "ok"}
            if execution_fails:
                raise RuntimeError("synthetic execution failure")
            return ExecutorResultSuccess(result={"input": "sandbox-smoke"})

        execute_action = mocker.patch.object(
            smoke.EphemeralBackend, "execute", side_effect=execute
        )
        if execution_fails:
            with pytest.raises(RuntimeError, match="synthetic execution failure"):
                await smoke._run_sync_artifact_sandbox_smoke_child()
        else:
            await smoke._run_sync_artifact_sandbox_smoke_child()

        execute_action.assert_awaited_once()
        assert not socket_path.exists()
