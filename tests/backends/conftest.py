"""Fixtures for backend testing.

Core fixtures for benchmarking and testing executor backends:
- `backend_type` - Parametrized fixture running tests across all backends
- `executor_backend` - Creates/yields/shuts down backend based on type
- `sandboxed_backend_type` - For isolation-only tests (POOL, EPHEMERAL)
- `simple_action_input_factory` - Factory for test RunActionInput objects
- `resolved_context_factory` - Factory for ResolvedContext objects

Prerequisites for running benchmarks with real action execution:
1. Start the dev stack: `just dev`
2. Sync the registry to build tarball: via UI or API
3. Run benchmarks inside Docker: `just bench`

Note: Sandboxed backends (pool, ephemeral) require nsjail which only
runs on Linux. Use `just bench` to run benchmarks inside Docker on macOS.
"""

from __future__ import annotations

import importlib
import os
import uuid
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

from tracecat.auth.types import Role
from tracecat.authz.scopes import SERVICE_PRINCIPAL_SCOPES
from tracecat.dsl.schemas import ExecutionContext
from tracecat.executor.schemas import (
    ActionImplementation,
    ExecutorBackendType,
    ResolvedContext,
)

if TYPE_CHECKING:
    from tracecat.auth.types import Role
    from tracecat.dsl.schemas import RunActionInput
    from tracecat.executor.backends.base import ExecutorBackend


def _check_nsjail_available() -> bool:
    """Check if nsjail is available in the current environment.

    Validates that nsjail_path is an executable file and rootfs_path is a directory.
    Returns False on macOS since nsjail requires Linux namespaces.
    """
    import platform

    if platform.system() != "Linux":
        return False

    from tracecat import config

    nsjail_path = Path(config.TRACECAT__SANDBOX_NSJAIL_PATH)
    rootfs_path = Path(config.TRACECAT__SANDBOX_ROOTFS_PATH)
    return nsjail_path.is_file() and rootfs_path.is_dir()


async def _check_registry_synced() -> bool:
    """Check if the registry has been synced and tarballs are available.

    Queries the database for registry versions with tarball URIs.
    Returns True if at least one tarball is available.
    """
    try:
        from sqlalchemy import select

        from tracecat.db.engine import get_async_session_context_manager
        from tracecat.db.models import RegistryVersion

        async with get_async_session_context_manager() as session:
            # Check if any registry version has a tarball (regardless of org)
            statement = select(RegistryVersion).where(
                RegistryVersion.tarball_uri.isnot(None),  # type: ignore[union-attr]
            )
            result = await session.execute(statement)
            versions = result.scalars().all()
            return len(versions) > 0
    except Exception:
        return False


# =============================================================================
# Backend Parametrization Fixtures
# =============================================================================


@pytest.fixture(params=["test", "direct", "pool", "ephemeral"])
def backend_type(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> str:
    """Parametrized fixture for running tests across all backend types.

    This fixture sets the appropriate environment variables and skips
    sandboxed backends when nsjail is not available.

    Yields:
        The backend type string (test, direct, pool, or ephemeral)
    """
    backend = request.param

    # Set environment variables
    monkeypatch.setenv("TRACECAT__EXECUTOR_BACKEND", backend)

    # Skip sandboxed backends if nsjail not available
    if backend in ("pool", "ephemeral"):
        if not _check_nsjail_available():
            pytest.skip(f"nsjail not available for {backend} backend")

    # Reload config to pick up new environment
    from tracecat import config as tracecat_config

    importlib.reload(tracecat_config)

    return backend


@pytest.fixture(params=["pool", "ephemeral"])
def sandboxed_backend_type(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> str:
    """Parametrized fixture for sandbox-only tests.

    Use this for tests that specifically verify sandbox isolation properties.
    Skips if nsjail is not available.

    Yields:
        The backend type string (pool or ephemeral)
    """
    backend = request.param

    if not _check_nsjail_available():
        pytest.skip(f"nsjail not available for {backend} backend")

    monkeypatch.setenv("TRACECAT__EXECUTOR_BACKEND", backend)

    from tracecat import config as tracecat_config

    importlib.reload(tracecat_config)

    return backend


@pytest.fixture
def test_backend_type(monkeypatch: pytest.MonkeyPatch) -> str:
    """Fixture for test backend only tests.

    Use this for tests that specifically test the in-process test backend
    without sandbox overhead.
    """
    monkeypatch.setenv("TRACECAT__EXECUTOR_BACKEND", "test")
    monkeypatch.setenv("TRACECAT__DISABLE_NSJAIL", "true")

    from tracecat import config as tracecat_config

    importlib.reload(tracecat_config)

    return "test"


@pytest.fixture
def direct_backend_type(monkeypatch: pytest.MonkeyPatch) -> str:
    """Fixture for direct backend only tests.

    Use this for tests that specifically test the subprocess direct backend
    without sandbox overhead.
    """
    monkeypatch.setenv("TRACECAT__EXECUTOR_BACKEND", "direct")
    monkeypatch.setenv("TRACECAT__DISABLE_NSJAIL", "true")

    from tracecat import config as tracecat_config

    importlib.reload(tracecat_config)

    return "direct"


# =============================================================================
# Registry Sync Check Fixture
# =============================================================================


@pytest.fixture(scope="session")
def event_loop_policy():
    """Use default event loop policy for session-scoped async fixtures."""
    import asyncio

    return asyncio.DefaultEventLoopPolicy()


@pytest.fixture(scope="module")
async def registry_synced() -> bool:
    """Check if the registry has been synced with tarballs available.

    This is a module-scoped fixture that checks once per test module.
    Tests can use this to skip or adapt behavior based on registry state.
    """
    return await _check_registry_synced()


@pytest.fixture
def require_registry_sync(registry_synced: bool) -> None:
    """Skip test if registry is not synced.

    Use this fixture for tests that require real action execution
    with registry tarballs.
    """
    if not registry_synced:
        pytest.skip(
            "Registry not synced. Run 'just dev' and sync registry via UI/API first."
        )


# =============================================================================
# Backend Lifecycle Fixtures
# =============================================================================


@pytest.fixture
async def executor_backend(
    backend_type: str,
) -> AsyncIterator[ExecutorBackend]:
    """Create and manage an executor backend based on backend_type.

    This fixture:
    1. Creates the appropriate backend based on backend_type
    2. Starts the backend
    3. Yields it for use in tests
    4. Shuts it down after tests complete

    Yields:
        The initialized ExecutorBackend instance
    """
    from tracecat.executor.backends import _create_backend

    backend_enum = ExecutorBackendType(backend_type)
    backend = _create_backend(backend_enum)

    await backend.start()
    try:
        yield backend
    finally:
        await backend.shutdown()


@pytest.fixture
async def test_backend() -> AsyncIterator[ExecutorBackend]:
    """Create a test backend for tests that don't need parametrization.

    This is useful for benchmarks that want to measure in-process backend
    performance without the overhead of parametrization.
    """
    from tracecat.executor.backends.test import TestBackend

    backend = TestBackend()
    await backend.start()
    try:
        yield backend
    finally:
        await backend.shutdown()


@pytest.fixture
async def direct_backend() -> AsyncIterator[ExecutorBackend]:
    """Create a direct backend for subprocess benchmark tests."""
    from tracecat.executor.backends.direct import DirectBackend

    backend = DirectBackend()
    await backend.start()
    try:
        yield backend
    finally:
        await backend.shutdown()


# =============================================================================
# Test Data Factories
# =============================================================================


@pytest.fixture
def simple_action_input_factory() -> Callable[..., RunActionInput]:
    """Factory for creating simple RunActionInput objects for testing.

    Returns a factory function that creates RunActionInput with
    sensible defaults for benchmarking.

    Usage:
        input = simple_action_input_factory(
            action="core.transform.reshape",
            args={"value": {"key": "value"}}
        )
    """
    from tracecat.dsl.schemas import ActionStatement, RunActionInput, RunContext
    from tracecat.identifiers.workflow import ExecutionUUID, WorkflowUUID
    from tracecat.registry.lock.types import RegistryLock

    def _create(
        action: str = "core.transform.reshape",
        args: dict | None = None,
        ref: str = "benchmark_action",
        registry_lock: RegistryLock | None = None,
    ) -> RunActionInput:
        wf_id = WorkflowUUID.new_uuid4()
        exec_id = ExecutionUUID.new_uuid4()
        # Provide a default registry lock for testing
        if registry_lock is None:
            registry_lock = RegistryLock(
                origins={"tracecat_registry": "test-version"},
                actions={action: "tracecat_registry"},
            )
        return RunActionInput(
            task=ActionStatement(
                action=action,
                args=args or {"value": {"benchmark": True}},
                ref=ref,
            ),
            exec_context=ExecutionContext(ACTIONS={}, TRIGGER=None),
            run_context=RunContext(
                wf_id=wf_id,
                wf_exec_id=f"{wf_id.short()}/{exec_id.short()}",
                wf_run_id=uuid.uuid4(),
                environment="default",
                logical_time=datetime.now(UTC),
            ),
            registry_lock=registry_lock,
        )

    return _create


@pytest.fixture
def benchmark_role() -> Role:
    """Role for benchmark tests.

    Uses the default workspace that has the registry synced.
    """
    return Role(
        type="service",
        service_id="tracecat-executor",
        workspace_id=uuid.UUID("38be3315-c172-4332-aea6-53fc4b93f053"),
        organization_id=uuid.UUID("00000000-0000-4444-aaaa-000000000000"),
        user_id=uuid.uuid4(),
        scopes=SERVICE_PRINCIPAL_SCOPES["tracecat-executor"],
    )


@pytest.fixture
def resolved_context_factory() -> Callable[..., ResolvedContext]:
    """Factory for creating ResolvedContext objects for benchmark testing.

    Returns a factory function that creates ResolvedContext with
    sensible defaults for benchmarking.
    """

    def _create(
        role: Role,
        action: str = "core.transform.reshape",
        args: dict[str, Any] | None = None,
    ) -> ResolvedContext:
        return ResolvedContext(
            secrets={},
            variables={},
            action_impl=ActionImplementation(
                type="udf",
                action_name=action,
                module="tracecat_registry.core.transform",
                name="reshape",
            ),
            evaluated_args=args or {"value": {"benchmark": True}},
            workspace_id=str(role.workspace_id),
            workflow_id=str(uuid.uuid4()),
            run_id=str(uuid.uuid4()),
            executor_token="mock-token-for-benchmarks",
        )

    return _create


# =============================================================================
# Environment Fixtures
# =============================================================================


@pytest.fixture(scope="module")
def anyio_backend():
    """Module-scoped anyio backend to support module-scoped async fixtures."""
    return "asyncio"


@pytest.fixture(scope="session")
def monkeypatch_session():
    """Session-scoped monkeypatch fixture."""
    from _pytest.monkeypatch import MonkeyPatch

    mpatch = MonkeyPatch()
    yield mpatch
    mpatch.undo()


@pytest.fixture(scope="session", autouse=True)
def setup_benchmark_environment(monkeypatch_session):
    """Set up environment for benchmark tests.

    Does NOT enable test mode - benchmarks run with real action execution
    when the registry is synced.

    nsjail is enabled by default for sandboxed backends (pool, ephemeral).
    Tests will skip on platforms where nsjail is not available (macOS).
    """
    # Default to test backend if not specified
    if not os.environ.get("TRACECAT__EXECUTOR_BACKEND"):
        monkeypatch_session.setenv("TRACECAT__EXECUTOR_BACKEND", "test")

    from tracecat import config as tracecat_config

    importlib.reload(tracecat_config)


# =============================================================================
# Multi-Backend Fixtures (Native)
# =============================================================================


@pytest.fixture
async def pool_backend() -> AsyncIterator[ExecutorBackend]:
    """Create a pool backend for benchmarking.

    Requires nsjail to be available (Linux only).
    Skips on macOS where nsjail is not supported.
    """
    if not _check_nsjail_available():
        pytest.skip("nsjail not available for pool backend (requires Linux)")

    from tracecat.executor.backends.pool import PoolBackend

    backend = PoolBackend()
    await backend.start()
    try:
        yield backend
    finally:
        await backend.shutdown()


@pytest.fixture
async def ephemeral_backend() -> AsyncIterator[ExecutorBackend]:
    """Create an ephemeral backend for benchmarking.

    Requires nsjail to be available (Linux only).
    Skips on macOS where nsjail is not supported.
    """
    if not _check_nsjail_available():
        pytest.skip("nsjail not available for ephemeral backend (requires Linux)")

    from tracecat.executor.backends.ephemeral import EphemeralBackend

    backend = EphemeralBackend()
    await backend.start()
    try:
        yield backend
    finally:
        await backend.shutdown()
