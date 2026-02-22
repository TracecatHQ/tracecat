"""HTTP-level tests for workflow management API endpoints."""

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest
from asyncpg import UniqueViolationError as AsyncpgUniqueViolationError
from fastapi import status
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError, NoResultFound

from tracecat.auth.types import Role
from tracecat.db.models import (
    Action,
    Schedule,
    Webhook,
    Workflow,
    WorkflowTag,
    Workspace,
)
from tracecat.pagination import CursorPaginatedResponse
from tracecat.workflow.management import router as workflow_management_router
from tracecat.workflow.management.types import WorkflowDefinitionMinimal


@pytest.fixture
def mock_workflow(test_workspace: Workspace) -> Workflow:
    """Create a mock workflow DB object."""
    workflow_id = uuid.UUID("aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa")
    return Workflow(
        id=workflow_id,
        title="Test Workflow",
        description="Test workflow description",
        status="online",
        version=1,
        workspace_id=test_workspace.id,
        entrypoint="action-1",
        expects={"input": {"type": "string"}},
        returns=None,
        config={},
        alias="test-workflow",
        error_handler=None,
        icon_url="https://example.com/icon.png",
        trigger_position_x=0.0,
        trigger_position_y=0.0,
        graph_version=1,
        created_at=datetime(2024, 1, 1, tzinfo=UTC),
        updated_at=datetime(2024, 1, 1, tzinfo=UTC),
        tags=[],
    )


@pytest.fixture
def mock_webhook(test_workspace: Workspace, mock_workflow: Workflow) -> Webhook:
    """Create a mock webhook DB object."""
    return Webhook(
        id=uuid.UUID("aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaac"),
        workspace_id=test_workspace.id,
        workflow_id=mock_workflow.id,
        status="online",
        methods=["POST"],
        filters={},
        allowlisted_cidrs=[],
        created_at=datetime(2024, 1, 1, tzinfo=UTC),
        updated_at=datetime(2024, 1, 1, tzinfo=UTC),
    )


@pytest.mark.anyio
async def test_list_workflows_success(
    client: TestClient,
    test_admin_role: Role,
    mock_workflow: Workflow,
) -> None:
    """Test GET /workflows returns paginated list of workflows."""
    # Mock service layer
    with patch.object(
        workflow_management_router, "WorkflowsManagementService"
    ) as MockService:
        # Create mock service instance
        mock_svc = AsyncMock()
        mock_definition = WorkflowDefinitionMinimal(
            id=str(uuid.uuid4()),
            version=1,
            created_at=datetime(2024, 1, 1, tzinfo=UTC),
        )

        # Mock paginated response
        mock_response = CursorPaginatedResponse(
            items=[(mock_workflow, mock_definition)],
            next_cursor=None,
            prev_cursor=None,
            has_more=False,
            has_previous=False,
        )
        mock_svc.list_workflows.return_value = mock_response

        # Set up service mock
        MockService.return_value = mock_svc

        # Make request
        response = client.get(
            "/workflows",
            params={"workspace_id": str(test_admin_role.workspace_id)},
        )

        # Assertions
        assert response.status_code == status.HTTP_200_OK
        data = response.json()

        # Verify response structure
        assert "items" in data
        assert len(data["items"]) == 1
        assert "has_more" in data
        assert "next_cursor" in data

        # Verify workflow data
        workflow = data["items"][0]
        assert workflow["title"] == "Test Workflow"
        assert workflow["description"] == "Test workflow description"
        assert workflow["status"] == "online"
        assert workflow["alias"] == "test-workflow"
        assert "latest_definition" in workflow


@pytest.mark.anyio
async def test_list_workflows_with_pagination(
    client: TestClient,
    test_admin_role: Role,
    mock_workflow: Workflow,
) -> None:
    """Test GET /workflows with pagination parameters."""
    with (
        patch(
            "tracecat.workflow.management.router.WorkflowsManagementService"
        ) as MockService,
    ):
        mock_svc = AsyncMock()
        mock_response = CursorPaginatedResponse(
            items=[(mock_workflow, None)],
            next_cursor="next-cursor",
            prev_cursor=None,
            has_more=True,
            has_previous=False,
        )
        mock_svc.list_workflows.return_value = mock_response
        MockService.return_value = mock_svc

        # Make request with pagination params
        response = client.get(
            "/workflows",
            params={
                "workspace_id": str(test_admin_role.workspace_id),
                "limit": 10,
                "cursor": "some-cursor",
            },
        )

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["has_more"] is True
        assert data["next_cursor"] == "next-cursor"


@pytest.mark.anyio
async def test_list_workflows_with_tag_filter(
    client: TestClient,
    test_admin_role: Role,
    mock_workflow: Workflow,
) -> None:
    """Test GET /workflows with tag filtering."""
    with (
        patch(
            "tracecat.workflow.management.router.WorkflowsManagementService"
        ) as MockService,
    ):
        mock_svc = AsyncMock()
        # Add tag to workflow
        mock_tag = WorkflowTag(
            id=uuid.uuid4(),
            name="test-tag",
            ref="test-tag",
            workspace_id=mock_workflow.workspace_id,
            created_at=datetime(2024, 1, 1, tzinfo=UTC),
            updated_at=datetime(2024, 1, 1, tzinfo=UTC),
        )
        mock_workflow.tags = [mock_tag]

        mock_response = CursorPaginatedResponse(
            items=[(mock_workflow, None)],
            next_cursor=None,
            prev_cursor=None,
            has_more=False,
            has_previous=False,
        )
        mock_svc.list_workflows.return_value = mock_response
        MockService.return_value = mock_svc

        # Make request with tag filter
        response = client.get(
            "/workflows",
            params={
                "workspace_id": str(test_admin_role.workspace_id),
                "tag": "test-tag",
            },
        )

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert len(data["items"]) == 1
        assert data["items"][0]["tags"][0]["name"] == "test-tag"


@pytest.mark.anyio
async def test_create_workflow_success(
    client: TestClient,
    test_admin_role: Role,
    mock_workflow: Workflow,
) -> None:
    """Test POST /workflows creates a new workflow."""
    with (
        patch(
            "tracecat.workflow.management.router.WorkflowsManagementService"
        ) as MockService,
    ):
        mock_svc = AsyncMock()
        mock_svc.create_workflow.return_value = mock_workflow
        MockService.return_value = mock_svc

        # Make request
        response = client.post(
            "/workflows",
            params={"workspace_id": str(test_admin_role.workspace_id)},
            data={
                "title": "Test Workflow",
                "description": "Test workflow description",
            },
        )

        # Assertions
        assert response.status_code == status.HTTP_201_CREATED
        data = response.json()

        # Verify workflow data
        assert data["title"] == "Test Workflow"
        assert data["description"] == "Test workflow description"
        assert data["status"] == "online"
        assert "id" in data
        assert "created_at" in data


@pytest.mark.anyio
async def test_create_workflow_validation_error(
    client: TestClient,
    test_admin_role: Role,
) -> None:
    """Test POST /workflows with invalid data returns 422."""
    # Make request with title that's too long (> 100 chars)
    response = client.post(
        "/workflows",
        params={"workspace_id": str(test_admin_role.workspace_id)},
        data={
            "title": "a" * 101,
            "description": "Test description",
        },
    )

    # Should return validation error
    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY


@pytest.mark.anyio
async def test_get_workflow_success(
    client: TestClient,
    test_admin_role: Role,
    mock_workflow: Workflow,
    mock_webhook: Webhook,
) -> None:
    """Test GET /workflows/{id} returns workflow details."""
    with (
        patch(
            "tracecat.workflow.management.router.WorkflowsManagementService"
        ) as MockService,
    ):
        mock_svc = AsyncMock()
        # Add relationships
        mock_workflow.actions = []
        mock_workflow.webhook = mock_webhook
        mock_workflow.schedules = []
        mock_svc.get_workflow.return_value = mock_workflow
        MockService.return_value = mock_svc

        # Make request
        workflow_id = str(mock_workflow.id)
        response = client.get(
            f"/workflows/{workflow_id}",
            params={"workspace_id": str(test_admin_role.workspace_id)},
        )

        # Assertions
        assert response.status_code == status.HTTP_200_OK
        data = response.json()

        # Verify workflow data
        assert data["title"] == "Test Workflow"
        assert data["description"] == "Test workflow description"
        assert data["status"] == "online"
        assert data["version"] == 1
        assert "webhook" in data
        assert "actions" in data
        assert "schedules" in data


@pytest.mark.anyio
async def test_get_workflow_not_found(
    client: TestClient,
    test_admin_role: Role,
) -> None:
    """Test GET /workflows/{id} with non-existent ID returns 404."""
    with (
        patch(
            "tracecat.workflow.management.router.WorkflowsManagementService"
        ) as MockService,
    ):
        mock_svc = AsyncMock()
        mock_svc.get_workflow.return_value = None
        MockService.return_value = mock_svc

        # Make request with non-existent ID
        fake_id = str(uuid.uuid4())
        response = client.get(
            f"/workflows/{fake_id}",
            params={"workspace_id": str(test_admin_role.workspace_id)},
        )

        # Should return 404
        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert "not found" in response.json()["detail"].lower()


@pytest.mark.anyio
async def test_update_workflow_success(
    client: TestClient,
    test_admin_role: Role,
    mock_workflow: Workflow,
) -> None:
    """Test PATCH /workflows/{id} updates workflow."""
    with (
        patch(
            "tracecat.workflow.management.router.WorkflowsManagementService"
        ) as MockService,
    ):
        mock_svc = AsyncMock()
        mock_svc.update_workflow.return_value = None
        MockService.return_value = mock_svc

        # Make request
        workflow_id = str(mock_workflow.id)
        response = client.patch(
            f"/workflows/{workflow_id}",
            params={"workspace_id": str(test_admin_role.workspace_id)},
            json={
                "title": "Updated Title",
                "description": "Updated description",
            },
        )

        # Assertions
        assert response.status_code == status.HTTP_204_NO_CONTENT

        # Verify service was called with correct params
        mock_svc.update_workflow.assert_called_once()


@pytest.mark.anyio
async def test_update_workflow_not_found(
    client: TestClient,
    test_admin_role: Role,
) -> None:
    """Test PATCH /workflows/{id} with non-existent ID returns 404."""
    with (
        patch(
            "tracecat.workflow.management.router.WorkflowsManagementService"
        ) as MockService,
    ):
        mock_svc = AsyncMock()
        mock_svc.update_workflow.side_effect = NoResultFound("Workflow not found")
        MockService.return_value = mock_svc

        # Make request
        fake_id = str(uuid.uuid4())
        response = client.patch(
            f"/workflows/{fake_id}",
            params={"workspace_id": str(test_admin_role.workspace_id)},
            json={"title": "Updated Title"},
        )

        # Should return 404
        assert response.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.anyio
async def test_update_workflow_duplicate_alias(
    client: TestClient,
    test_admin_role: Role,
    mock_workflow: Workflow,
) -> None:
    """Test PATCH /workflows/{id} with duplicate alias returns 409."""
    with (
        patch(
            "tracecat.workflow.management.router.WorkflowsManagementService"
        ) as MockService,
    ):
        mock_svc = AsyncMock()
        # Create a proper IntegrityError with UniqueViolationError as cause
        unique_error = AsyncpgUniqueViolationError("uq_workflow_alias_workspace_id")
        integrity_error = IntegrityError("", {}, unique_error)
        mock_svc.update_workflow.side_effect = integrity_error
        MockService.return_value = mock_svc

        # Make request
        workflow_id = str(mock_workflow.id)
        response = client.patch(
            f"/workflows/{workflow_id}",
            params={"workspace_id": str(test_admin_role.workspace_id)},
            json={"alias": "duplicate-alias"},
        )

        # Should return 409 conflict
        assert response.status_code == status.HTTP_409_CONFLICT


@pytest.mark.anyio
async def test_delete_workflow_success(
    client: TestClient,
    test_admin_role: Role,
    mock_workflow: Workflow,
) -> None:
    """Test DELETE /workflows/{id} deletes workflow."""
    with (
        patch(
            "tracecat.workflow.management.router.WorkflowsManagementService"
        ) as MockService,
    ):
        mock_svc = AsyncMock()
        mock_svc.delete_workflow.return_value = None
        MockService.return_value = mock_svc

        # Make request
        workflow_id = str(mock_workflow.id)
        response = client.delete(
            f"/workflows/{workflow_id}",
            params={"workspace_id": str(test_admin_role.workspace_id)},
        )

        # Assertions
        assert response.status_code == status.HTTP_204_NO_CONTENT

        # Verify service was called
        mock_svc.delete_workflow.assert_called_once()


@pytest.mark.anyio
async def test_delete_workflow_not_found(
    client: TestClient,
    test_admin_role: Role,
) -> None:
    """Test DELETE /workflows/{id} with non-existent ID returns 404."""
    with (
        patch(
            "tracecat.workflow.management.router.WorkflowsManagementService"
        ) as MockService,
    ):
        mock_svc = AsyncMock()
        mock_svc.delete_workflow.side_effect = NoResultFound("Workflow not found")
        MockService.return_value = mock_svc

        # Make request
        fake_id = str(uuid.uuid4())
        response = client.delete(
            f"/workflows/{fake_id}",
            params={"workspace_id": str(test_admin_role.workspace_id)},
        )

        # Should return 404
        assert response.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.anyio
async def test_get_workflow_with_relationships(
    client: TestClient,
    test_admin_role: Role,
    test_workspace: Workspace,
    mock_workflow: Workflow,
    mock_webhook: Webhook,
) -> None:
    """Test GET /workflows/{id} properly serializes relationships (tags, actions, schedules)."""
    with (
        patch(
            "tracecat.workflow.management.router.WorkflowsManagementService"
        ) as MockService,
    ):
        mock_svc = AsyncMock()

        # Add relationships to workflow
        mock_tag = WorkflowTag(
            id=uuid.uuid4(),
            name="production",
            ref="production",
            workspace_id=test_workspace.id,
            created_at=datetime(2024, 1, 1, tzinfo=UTC),
            updated_at=datetime(2024, 1, 1, tzinfo=UTC),
        )
        mock_action = Action(
            id=uuid.UUID("12345678-1234-4123-8123-123456789012"),
            type="webhook",
            title="Test Action",
            description="Test action description",
            status="online",
            inputs="",  # inputs is a YAML string, not dict
            control_flow={},
            is_interactive=False,
            workspace_id=test_workspace.id,
            workflow_id=mock_workflow.id,
            position_x=100.0,
            position_y=200.0,
            upstream_edges=[],
            created_at=datetime(2024, 1, 1, tzinfo=UTC),
            updated_at=datetime(2024, 1, 1, tzinfo=UTC),
        )
        mock_schedule = Schedule(
            id=uuid.UUID("12345678-1234-4123-8123-123456789013"),
            status="online",
            workspace_id=test_workspace.id,
            workflow_id=mock_workflow.id,
            cron="0 0 * * *",
            inputs={},
            offset=None,
            start_at=None,
            end_at=None,
            timeout=None,
            created_at=datetime(2024, 1, 1, tzinfo=UTC),
            updated_at=datetime(2024, 1, 1, tzinfo=UTC),
        )

        mock_workflow.tags = [mock_tag]
        mock_workflow.actions = [mock_action]
        mock_workflow.schedules = [mock_schedule]
        mock_workflow.webhook = mock_webhook

        mock_svc.get_workflow.return_value = mock_workflow
        MockService.return_value = mock_svc

        # Make request
        workflow_id = str(mock_workflow.id)
        response = client.get(
            f"/workflows/{workflow_id}",
            params={"workspace_id": str(test_admin_role.workspace_id)},
        )

        # Assertions
        assert response.status_code == status.HTTP_200_OK
        data = response.json()

        # Verify relationships are properly serialized
        assert "actions" in data
        assert "12345678-1234-4123-8123-123456789012" in data["actions"]
        assert (
            data["actions"]["12345678-1234-4123-8123-123456789012"]["title"]
            == "Test Action"
        )

        assert "schedules" in data
        assert len(data["schedules"]) == 1
        assert data["schedules"][0]["cron"] == "0 0 * * *"

        assert "webhook" in data
        assert data["webhook"]["status"] == "online"
