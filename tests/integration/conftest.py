"""Fixtures for integration tests that require real infrastructure."""

from __future__ import annotations

import importlib
import shutil
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from tracecat.auth.types import Role
from tracecat.authz.scopes import SERVICE_PRINCIPAL_SCOPES
from tracecat.dsl.schemas import ExecutionContext
from tracecat.executor.schemas import ActionImplementation, ResolvedContext

# =============================================================================
# Environment Setup for Integration Tests
# =============================================================================


@pytest.fixture(scope="class")
def anyio_backend():
    """Class-scoped anyio backend to support class-scoped async fixtures.

    This overrides the function-scoped fixture from root conftest.py.
    """
    return "asyncio"


@pytest.fixture(scope="session")
def monkeypatch_session():
    """Session-scoped monkeypatch fixture."""
    from _pytest.monkeypatch import MonkeyPatch

    mpatch = MonkeyPatch()
    yield mpatch
    mpatch.undo()


@pytest.fixture(scope="session", autouse=True)
def disable_nsjail_for_tests(monkeypatch_session):
    """Disable nsjail sandbox for integration tests."""
    monkeypatch_session.setenv("TRACECAT__DISABLE_NSJAIL", "true")

    # Reload config to pick up the new value
    from tracecat import config as tracecat_config

    importlib.reload(tracecat_config)


# =============================================================================
# Multi-Tenant Role Fixtures
# =============================================================================


@pytest.fixture
def role_workspace_a() -> Role:
    """Role for workspace A (test tenant 1)."""
    return Role(
        type="service",
        service_id="tracecat-executor",
        workspace_id=uuid.UUID("aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa"),
        user_id=uuid.uuid4(),
        scopes=SERVICE_PRINCIPAL_SCOPES["tracecat-executor"],
    )


@pytest.fixture
def role_workspace_b() -> Role:
    """Role for workspace B (test tenant 2)."""
    return Role(
        type="service",
        service_id="tracecat-executor",
        workspace_id=uuid.UUID("bbbbbbbb-bbbb-4bbb-bbbb-bbbbbbbbbbbb"),
        user_id=uuid.uuid4(),
        scopes=SERVICE_PRINCIPAL_SCOPES["tracecat-executor"],
    )


@pytest.fixture
def role_workspace_agent_a() -> Role:
    """Role for workspace A (test tenant 1) for agent workflows."""
    return Role(
        type="service",
        service_id="tracecat-agent-executor",
        workspace_id=uuid.UUID("aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa"),
        organization_id=uuid.UUID("aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa"),
        user_id=uuid.uuid4(),
        scopes=SERVICE_PRINCIPAL_SCOPES["tracecat-agent-executor"],
    )


@pytest.fixture
def role_workspace_agent_b() -> Role:
    """Role for workspace B (test tenant 2) for agent workflows."""
    return Role(
        type="service",
        service_id="tracecat-agent-executor",
        workspace_id=uuid.UUID("bbbbbbbb-bbbb-4bbb-bbbb-bbbbbbbbbbbb"),
        organization_id=uuid.UUID("bbbbbbbb-bbbb-4bbb-bbbb-bbbbbbbbbbbb"),
        user_id=uuid.uuid4(),
        scopes=SERVICE_PRINCIPAL_SCOPES["tracecat-agent-executor"],
    )


# =============================================================================
# Mock Module Fixtures
# =============================================================================


@pytest.fixture
def mock_modules_dir(tmp_path: Path) -> Path:
    """Create dummy Python modules for each workspace.

    Creates a directory structure with mock Python modules that can be
    used to simulate different registry versions for each workspace.

    Structure:
        {tmp_path}/
            workspace_a/
                tracecat_custom/
                    __init__.py
                    actions.py  # Returns "WORKSPACE_A_V1", adds 1000
            workspace_b/
                tracecat_custom/
                    __init__.py
                    actions.py  # Returns "WORKSPACE_B_V2", adds 2000
    """
    # Workspace A module
    ws_a = tmp_path / "workspace_a" / "tracecat_custom"
    ws_a.mkdir(parents=True)
    (ws_a / "__init__.py").write_text("")
    (ws_a / "actions.py").write_text('''
def workspace_identifier() -> str:
    """Return workspace identifier for verification."""
    return "WORKSPACE_A_V1"

def transform_value(x: int) -> int:
    """Transform value with workspace-specific logic."""
    return x + 1000  # Workspace A adds 1000
''')

    # Workspace B module
    ws_b = tmp_path / "workspace_b" / "tracecat_custom"
    ws_b.mkdir(parents=True)
    (ws_b / "__init__.py").write_text("")
    (ws_b / "actions.py").write_text('''
def workspace_identifier() -> str:
    """Return workspace identifier for verification."""
    return "WORKSPACE_B_V2"

def transform_value(x: int) -> int:
    """Transform value with workspace-specific logic."""
    return x + 2000  # Workspace B adds 2000
''')

    return tmp_path


@pytest.fixture
def temp_registry_cache(tmp_path: Path) -> Path:
    """Create a temporary registry cache directory.

    This simulates the cache directory where extracted tarballs are stored.
    """
    cache_dir = tmp_path / "registry-cache"
    cache_dir.mkdir(parents=True)
    return cache_dir


@pytest.fixture
def staged_cache_dirs(
    temp_registry_cache: Path, mock_modules_dir: Path
) -> tuple[Path, Path]:
    """Pre-stage cache directories with mock modules for each workspace.

    Returns tuple of (path_a, path_b) where each path contains the
    extracted mock modules for that workspace.
    """
    path_a = temp_registry_cache / "entries" / "workspace-a" / "tarball"
    path_b = temp_registry_cache / "entries" / "workspace-b" / "tarball"

    shutil.copytree(mock_modules_dir / "workspace_a", path_a)
    shutil.copytree(mock_modules_dir / "workspace_b", path_b)

    return path_a, path_b


# =============================================================================
# RunActionInput Factory
# =============================================================================


@pytest.fixture
def run_action_input_factory():
    """Factory for creating RunActionInput objects for testing."""
    from tracecat.dsl.schemas import ActionStatement, RunActionInput, RunContext
    from tracecat.identifiers.workflow import WorkflowUUID
    from tracecat.registry.lock.types import RegistryLock

    def _create(
        action: str = "core.transform",
        args: dict | None = None,
        registry_lock: RegistryLock | None = None,
    ) -> RunActionInput:
        wf_id = WorkflowUUID.new_uuid4()
        # Provide a default registry lock for testing
        if registry_lock is None:
            registry_lock = RegistryLock(
                origins={"tracecat_registry": "test-version"},
                actions={action: "tracecat_registry"},
            )
        return RunActionInput(
            task=ActionStatement(
                action=action,
                args=args or {"value": {"test": True}},
                ref="test_action",
            ),
            exec_context=ExecutionContext(
                ACTIONS={},
                TRIGGER=None,
            ),
            run_context=RunContext(
                wf_id=wf_id,
                wf_exec_id=f"{wf_id.short()}/exec_test",
                wf_run_id=uuid.uuid4(),
                environment="test",
                logical_time=datetime.now(UTC),
            ),
            registry_lock=registry_lock,
        )

    return _create


# =============================================================================
# ResolvedContext Factory
# =============================================================================


@pytest.fixture
def resolved_context_factory():
    """Create minimal mock ResolvedContext objects for integration tests."""

    def _create(
        role: Role,
        args: dict[str, Any] | None = None,
    ) -> ResolvedContext:
        return ResolvedContext(
            secrets={},
            variables={},
            action_impl=ActionImplementation(
                type="udf",
                module="tracecat_registry.core.transform",
                name="reshape",
            ),
            evaluated_args=args or {"value": {"test": True}},
            workspace_id=str(role.workspace_id),
            workflow_id=str(uuid.uuid4()),
            run_id=str(uuid.uuid4()),
            executor_token="mock-token-for-testing",
        )

    return _create
