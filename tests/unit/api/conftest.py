"""Minimal fixtures for HTTP-level API route testing."""

import uuid
from collections.abc import Generator
from typing import get_args
from unittest.mock import AsyncMock

import pytest
from _pytest.fixtures import FixtureRequest
from fastapi.testclient import TestClient

from tracecat.api.app import app
from tracecat.auth.credentials import AuthenticatedUserOnly, SuperuserRole
from tracecat.auth.dependencies import (
    ExecutorWorkspaceRole,
    OrgActorRole,
    OrganizationServiceAccountRole,
    OrgUserOnlyRole,
    OrgUserRole,
    WorkspaceActorRole,
    WorkspaceActorRouteRole,
    WorkspaceServiceAccountRole,
    WorkspaceUserRole,
    WorkspaceUserRouteRole,
)
from tracecat.auth.types import Role
from tracecat.authz.scopes import (
    ADMIN_SCOPES,
    ORG_ADMIN_SCOPES,
    SERVICE_PRINCIPAL_SCOPES,
)
from tracecat.cases.router import WorkspaceActor
from tracecat.contexts import ctx_role
from tracecat.db.engine import get_async_session, get_async_session_bypass_rls
from tracecat.db.models import Workspace
from tracecat.service_accounts.router import WorkspaceUserOnlyInPath
from tracecat.tables.router import (
    WorkspaceEditorUser as TablesWorkspaceEditorUser,
)
from tracecat.tables.router import (
    WorkspaceUser as TablesWorkspaceUser,
)
from tracecat.workspaces.router import (
    WorkspaceUserInPath,
)

_FALLBACK_ROLE: Role | None = None


def override_role_dependency() -> Role:
    """Override role dependencies to use ctx_role from test fixtures."""
    role = ctx_role.get() or _FALLBACK_ROLE
    if role is None:
        raise RuntimeError("No role set in ctx_role context")
    ctx_role.set(role)
    return role


async def _inject_test_role_middleware(request, call_next):
    role = ctx_role.get() or _FALLBACK_ROLE
    if role is None:
        return await call_next(request)

    token = ctx_role.set(role)
    try:
        return await call_next(request)
    finally:
        ctx_role.reset(token)


@pytest.fixture
def client(request: FixtureRequest) -> Generator[TestClient, None, None]:
    """Create FastAPI test client.

    Uses the existing app instance and relies on ctx_role context
    from test_role/test_admin_role fixtures for authentication.
    """

    for role_fixture in ("test_admin_role", "test_role"):
        if role_fixture in request.fixturenames:
            request.getfixturevalue(role_fixture)
            break

    if not getattr(app.state, "_api_test_role_middleware_installed", False):
        app.middleware("http")(_inject_test_role_middleware)
        app.state._api_test_role_middleware_installed = True

    # List of Annotated role dependencies to override
    role_dependencies = [
        WorkspaceUserRole,
        WorkspaceUserRouteRole,
        WorkspaceActorRole,
        WorkspaceActorRouteRole,
        WorkspaceServiceAccountRole,
        ExecutorWorkspaceRole,
        WorkspaceActor,
        SuperuserRole,
        AuthenticatedUserOnly,
        OrgActorRole,
        OrganizationServiceAccountRole,
        OrgUserOnlyRole,
        OrgUserRole,
        TablesWorkspaceUser,
        TablesWorkspaceEditorUser,
        WorkspaceUserInPath,
        WorkspaceUserOnlyInPath,
    ]

    for annotated_type in role_dependencies:
        # Extract the Depends object from the Annotated type
        metadata = get_args(annotated_type)
        if metadata and hasattr(metadata[1], "dependency"):
            original_dependency = metadata[1].dependency
            # Override the actual dependency function
            app.dependency_overrides[original_dependency] = override_role_dependency

    mock_session = AsyncMock(name="mock_async_session")

    async def override_get_async_session() -> AsyncMock:
        """Return a mock DB session so HTTP tests do not hit Postgres."""
        return mock_session

    app.dependency_overrides[get_async_session] = override_get_async_session
    app.dependency_overrides[get_async_session_bypass_rls] = override_get_async_session

    client = TestClient(app, raise_server_exceptions=False)
    yield client
    # Clean up overrides
    app.dependency_overrides.clear()


@pytest.fixture
def test_admin_role(
    test_workspace: Workspace, mock_org_id: uuid.UUID
) -> Generator[Role, None, None]:
    global _FALLBACK_ROLE
    role = Role(
        type="user",
        user_id=uuid.uuid4(),
        organization_id=mock_org_id,
        workspace_id=test_workspace.id,
        service_id="tracecat-api",
        scopes=ADMIN_SCOPES | ORG_ADMIN_SCOPES,
    )
    token = ctx_role.set(role)
    previous_role = _FALLBACK_ROLE
    _FALLBACK_ROLE = role
    try:
        yield role
    finally:
        _FALLBACK_ROLE = previous_role
        ctx_role.reset(token)


@pytest.fixture
def test_role(
    test_workspace: Workspace, mock_org_id: uuid.UUID
) -> Generator[Role, None, None]:
    global _FALLBACK_ROLE
    role = Role(
        type="service",
        user_id=mock_org_id,
        organization_id=mock_org_id,
        workspace_id=test_workspace.id,
        service_id="tracecat-runner",
        scopes=SERVICE_PRINCIPAL_SCOPES["tracecat-runner"],
    )
    token = ctx_role.set(role)
    previous_role = _FALLBACK_ROLE
    _FALLBACK_ROLE = role
    try:
        yield role
    finally:
        _FALLBACK_ROLE = previous_role
        ctx_role.reset(token)
