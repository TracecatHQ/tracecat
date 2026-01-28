"""Minimal fixtures for HTTP-level API route testing."""

from collections.abc import Generator
from typing import get_args
from unittest.mock import AsyncMock

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
from tracecat.organization.router import OrgOwnerRole as OrganizationOrgOwnerRole
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
    WorkspaceUserInPath,
)


def override_role_dependency() -> Role:
    """Override role dependencies to use ctx_role from test fixtures."""
    role = ctx_role.get()
    if role is None:
        raise RuntimeError("No role set in ctx_role context")
    return role


@pytest.fixture
def client() -> Generator[TestClient, None, None]:
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
        SuperuserRole,
        AuthenticatedUserOnly,
        OrganizationUserRole,
        OrganizationAdminUserRole,
        SecretsWorkspaceUser,
        WorkspaceAdminUser,
        OrgAdminUser,
        TablesWorkspaceUser,
        TablesWorkspaceEditorUser,
        OrganizationOrgUserRole,
        OrganizationOrgAdminRole,
        OrganizationOrgOwnerRole,
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

    client = TestClient(app, raise_server_exceptions=False)
    yield client
    # Clean up overrides
    app.dependency_overrides.clear()
