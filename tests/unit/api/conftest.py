"""Minimal fixtures for HTTP-level API route testing."""

from collections.abc import Generator
from typing import get_args
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from tracecat.agent.router import (
    OrganizationAdminUserRole,
    OrganizationUserRole,
)
from tracecat.api.app import app
from tracecat.auth.credentials import AuthenticatedUserOnly, SuperuserRole
from tracecat.auth.dependencies import ExecutorWorkspaceRole, WorkspaceUserRole
from tracecat.auth.types import Role
from tracecat.cases.router import WorkspaceUser
from tracecat.contexts import ctx_role
from tracecat.db.engine import get_async_session
from tracecat.organization.router import OrgAdminRole as OrganizationOrgAdminRole
from tracecat.organization.router import OrgUserRole as OrganizationOrgUserRole
from tracecat.secrets.router import (
    OrgAdminUser,
    WorkspaceAdminUser,
)
from tracecat.secrets.router import (
    WorkspaceUser as SecretsWorkspaceUser,
)
from tracecat.tables.router import (
    WorkspaceEditorUser as TablesWorkspaceEditorUser,
)
from tracecat.tables.router import (
    WorkspaceUser as TablesWorkspaceUser,
)
from tracecat.workspaces.router import (
    OrgAdminUser as WorkspacesOrgAdminUser,
)
from tracecat.workspaces.router import (
    OrgUser,
    WorkspaceAdminUserInPath,
    WorkspaceUserInPath,
)


def override_role_dependency() -> Role:
    """Override role dependencies to use ctx_role from test fixtures."""
    role = ctx_role.get()
    if role is None:
        raise RuntimeError("No role set in ctx_role context")
    return role


@pytest.fixture
def mock_session() -> AsyncMock:
    """Mock DB session for HTTP tests.

    Configured so sync method chains work: ``result = await session.execute(...)``
    then ``result.scalars().first()`` returns ``None`` by default.
    """
    session = AsyncMock(name="mock_async_session")
    # session.execute is async, but its return value's .scalars()/.first() are sync
    mock_execute_result = MagicMock()
    mock_execute_result.scalars.return_value.first.return_value = None
    session.execute.return_value = mock_execute_result
    return session


@pytest.fixture
def client(mock_session: AsyncMock) -> Generator[TestClient, None, None]:
    """Create FastAPI test client.

    Uses the existing app instance and relies on ctx_role context
    from test_role/test_admin_role fixtures for authentication.
    """

    # List of Annotated role dependencies to override
    role_dependencies = [
        WorkspaceUserRole,
        ExecutorWorkspaceRole,
        WorkspaceUser,
        WorkspaceUserInPath,
        WorkspaceAdminUserInPath,
        SuperuserRole,
        AuthenticatedUserOnly,
        OrganizationUserRole,
        OrganizationAdminUserRole,
        OrgUser,
        WorkspacesOrgAdminUser,
        SecretsWorkspaceUser,
        WorkspaceAdminUser,
        OrgAdminUser,
        TablesWorkspaceUser,
        TablesWorkspaceEditorUser,
        OrganizationOrgUserRole,
        OrganizationOrgAdminRole,
    ]

    for annotated_type in role_dependencies:
        # Extract the Depends object from the Annotated type
        metadata = get_args(annotated_type)
        if metadata and hasattr(metadata[1], "dependency"):
            original_dependency = metadata[1].dependency
            # Override the actual dependency function
            app.dependency_overrides[original_dependency] = override_role_dependency

    async def override_get_async_session() -> AsyncMock:
        """Return a mock DB session so HTTP tests do not hit Postgres."""
        return mock_session

    app.dependency_overrides[get_async_session] = override_get_async_session

    client = TestClient(app, raise_server_exceptions=False)
    yield client
    # Clean up overrides
    app.dependency_overrides.clear()
