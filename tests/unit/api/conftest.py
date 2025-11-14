"""Minimal fixtures for HTTP-level API route testing."""

from collections.abc import Generator
from typing import get_args

import pytest
from fastapi.testclient import TestClient
from syrupy.assertion import SnapshotAssertion

from tracecat.agent.router import (
    OrganizationAdminUserRole,
    OrganizationUserRole,
)
from tracecat.api.app import app
from tracecat.auth.dependencies import WorkspaceUserRole
from tracecat.auth.types import Role
from tracecat.cases.router import WorkspaceUser
from tracecat.contexts import ctx_role
from tracecat.secrets.router import (
    OrgAdminUser,
    WorkspaceAdminUser,
)
from tracecat.secrets.router import (
    WorkspaceUser as SecretsWorkspaceUser,
)
from tracecat.tables.router import (
    WorkspaceAdminUser as TablesWorkspaceAdminUser,
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
def client() -> Generator[TestClient, None, None]:
    """Create FastAPI test client.

    Uses the existing app instance and relies on ctx_role context
    from test_role/test_admin_role fixtures for authentication.
    """

    # List of Annotated role dependencies to override
    role_dependencies = [
        WorkspaceUserRole,
        WorkspaceUser,
        WorkspaceUserInPath,
        WorkspaceAdminUserInPath,
        OrganizationUserRole,
        OrganizationAdminUserRole,
        OrgUser,
        WorkspacesOrgAdminUser,
        SecretsWorkspaceUser,
        WorkspaceAdminUser,
        OrgAdminUser,
        TablesWorkspaceUser,
        TablesWorkspaceAdminUser,
    ]

    for annotated_type in role_dependencies:
        # Extract the Depends object from the Annotated type
        metadata = get_args(annotated_type)
        if metadata and hasattr(metadata[1], "dependency"):
            original_dependency = metadata[1].dependency
            # Override the actual dependency function
            app.dependency_overrides[original_dependency] = override_role_dependency

    client = TestClient(app, raise_server_exceptions=False)
    yield client
    # Clean up overrides
    app.dependency_overrides.clear()


@pytest.fixture
def snapshot(snapshot: SnapshotAssertion) -> Generator[SnapshotAssertion, None, None]:
    """Provide snapshot fixture with proper type hints."""
    yield snapshot
