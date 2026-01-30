"""HTTP-level tests for workspaces API endpoints."""

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import status
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError

from tracecat.auth.types import Role
from tracecat.authz.service import MembershipWithOrg
from tracecat.db.models import Workspace
from tracecat.logger import logger
from tracecat.workspaces import router as workspaces_router


@pytest.fixture
def mock_workspace_data() -> Workspace:
    """Create a mock workspace DB object."""
    workspace = Workspace(
        id=uuid.UUID("bbbbbbbb-bbbb-4bbb-bbbb-bbbbbbbbbbbb"),
        name="Test Workspace",
        settings={},
        organization_id=uuid.uuid4(),
        created_at=datetime(2024, 1, 1, tzinfo=UTC),
        updated_at=datetime(2024, 1, 1, tzinfo=UTC),
    )
    return workspace


@pytest.mark.anyio
async def test_list_workspaces_admin_success(
    client: TestClient,
    test_admin_role: Role,
    mock_workspace_data: Workspace,
) -> None:
    """Test GET /workspaces returns all workspaces for admin."""
    with patch.object(workspaces_router, "WorkspaceService") as MockService:
        mock_svc = AsyncMock()
        mock_svc.admin_list_workspaces.return_value = [mock_workspace_data]
        MockService.return_value = mock_svc

        # Make request
        response = client.get("/workspaces")

        # Assertions
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert len(data) == 1
        assert data[0]["name"] == "Test Workspace"


@pytest.mark.anyio
async def test_list_workspaces_user_success(
    client: TestClient,
    test_role: Role,
    test_workspace: Workspace,
) -> None:
    """Test GET /workspaces returns user's workspaces."""
    with patch.object(workspaces_router, "WorkspaceService") as MockService:
        mock_svc = AsyncMock()
        mock_svc.list_workspaces.return_value = [test_workspace]
        MockService.return_value = mock_svc

        # Make request - test_role fixture is used for non-admin user
        response = client.get("/workspaces")

        # Assertions
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert len(data) == 1
        assert data[0]["id"] == str(test_workspace.id)
        assert data[0]["name"] == test_workspace.name


@pytest.mark.anyio
async def test_create_workspace_success(
    client: TestClient,
    test_admin_role: Role,
    mock_workspace_data: Workspace,
) -> None:
    """Test POST /workspaces creates a new workspace."""
    with patch.object(workspaces_router, "WorkspaceService") as MockService:
        mock_svc = AsyncMock()
        mock_svc.create_workspace.return_value = mock_workspace_data
        MockService.return_value = mock_svc

        # Make request
        organization_id = str(uuid.uuid4())
        response = client.post(
            "/workspaces",
            json={
                "name": "New Workspace",
                "organization_id": organization_id,
            },
        )

        # Assertions
        assert response.status_code == status.HTTP_201_CREATED
        data = response.json()
        assert data["name"] == "Test Workspace"


@pytest.mark.anyio
async def test_create_workspace_conflict(
    client: TestClient,
    test_admin_role: Role,
) -> None:
    """Test POST /workspaces with duplicate name returns 409."""
    with patch.object(workspaces_router, "WorkspaceService") as MockService:
        mock_svc = AsyncMock()
        mock_svc.create_workspace.side_effect = IntegrityError(
            "", {}, Exception("Duplicate")
        )
        MockService.return_value = mock_svc

        # Make request
        organization_id = str(uuid.uuid4())
        response = client.post(
            "/workspaces",
            json={
                "name": "Duplicate Workspace",
                "organization_id": organization_id,
            },
        )

        # Should return 409
        assert response.status_code == status.HTTP_409_CONFLICT


@pytest.mark.anyio
async def test_search_workspaces_success(
    client: TestClient,
    test_admin_role: Role,
    mock_workspace_data: Workspace,
) -> None:
    """Test GET /workspaces/search with search term."""
    with patch.object(workspaces_router, "WorkspaceService") as MockService:
        mock_svc = AsyncMock()
        mock_svc.search_workspaces.return_value = [mock_workspace_data]
        MockService.return_value = mock_svc

        # Make request
        response = client.get(
            "/workspaces/search",
            params={"name": "Test"},
        )

        # Assertions
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert len(data) == 1
        assert data[0]["name"] == "Test Workspace"


@pytest.mark.anyio
async def test_get_workspace_success(
    client: TestClient, test_admin_role: Role, mock_workspace_data: Workspace
) -> None:
    """Test GET /workspaces/{workspace_id} returns workspace details."""
    with (
        patch.object(workspaces_router, "WorkspaceService") as MockService,
        patch.object(workspaces_router, "MembershipService") as MockMembershipService,
    ):
        # Mock workspace service
        mock_svc = AsyncMock()
        mock_svc.get_workspace.return_value = mock_workspace_data
        MockService.return_value = mock_svc

        # Mock membership service to allow access
        mock_membership_svc = AsyncMock()
        mock_membership = AsyncMock()
        mock_membership.user_id = test_admin_role.user_id
        mock_membership.workspace_id = mock_workspace_data.id
        mock_membership_svc.get_membership.return_value = MembershipWithOrg(
            membership=mock_membership, org_id=mock_workspace_data.organization_id
        )
        MockMembershipService.return_value = mock_membership_svc

        # Make request - use test_workspace which matches the test_admin_role's workspace
        workspace_id = str(mock_workspace_data.id)
        response = client.get(f"/workspaces/{workspace_id}")

        # Assertions
        data = response.json()
        logger.info("DATA", data=data)
        assert response.status_code == status.HTTP_200_OK
        assert data["name"] == mock_workspace_data.name


@pytest.mark.anyio
async def test_get_workspace_not_found(
    client: TestClient,
    test_admin_role: Role,
) -> None:
    """Test GET /workspaces/{workspace_id} with non-existent ID returns 404."""
    with patch.object(workspaces_router, "WorkspaceService") as MockService:
        mock_svc = AsyncMock()
        # Return None to mimic service behavior when workspace is not found
        mock_svc.get_workspace.return_value = None
        MockService.return_value = mock_svc

        # Make request
        fake_id = str(uuid.uuid4())
        response = client.get(f"/workspaces/{fake_id}")

        # Should return 404
        assert response.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.anyio
async def test_update_workspace_success(
    client: TestClient,
    test_admin_role: Role,
    mock_workspace_data: Workspace,
) -> None:
    """Test PATCH /workspaces/{workspace_id} updates workspace."""
    with patch.object(workspaces_router, "WorkspaceService") as MockService:
        mock_svc = AsyncMock()
        mock_svc.update_workspace.return_value = None
        MockService.return_value = mock_svc

        # Make request
        workspace_id = str(mock_workspace_data.id)
        response = client.patch(
            f"/workspaces/{workspace_id}",
            json={"name": "Updated Workspace Name"},
        )

        # Assertions
        assert response.status_code == status.HTTP_204_NO_CONTENT


@pytest.mark.anyio
async def test_delete_workspace_success(
    client: TestClient,
    test_admin_role: Role,
    mock_workspace_data: Workspace,
) -> None:
    """Test DELETE /workspaces/{workspace_id} deletes workspace."""
    with patch.object(workspaces_router, "WorkspaceService") as MockService:
        mock_svc = AsyncMock()
        mock_svc.delete_workspace.return_value = None
        MockService.return_value = mock_svc

        # Make request
        workspace_id = str(mock_workspace_data.id)
        response = client.delete(f"/workspaces/{workspace_id}")

        # Assertions
        assert response.status_code == status.HTTP_204_NO_CONTENT
