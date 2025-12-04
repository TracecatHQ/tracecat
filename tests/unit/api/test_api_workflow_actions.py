"""HTTP-level tests for workflow actions API endpoints."""

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import status
from fastapi.testclient import TestClient

from tracecat.auth.types import Role
from tracecat.db.models import Action, Workflow, Workspace
from tracecat.workflow.actions.service import WorkflowActionService


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
        object={"nodes": [], "edges": []},
        config={},
        alias="test-workflow",
        error_handler=None,
        icon_url="https://example.com/icon.png",
        created_at=datetime(2024, 1, 1, tzinfo=UTC),
        updated_at=datetime(2024, 1, 1, tzinfo=UTC),
        tags=[],
    )


@pytest.fixture
def mock_action(test_workspace: Workspace, mock_workflow: Workflow) -> Action:
    """Create a mock action DB object."""
    action = Action(
        id=uuid.UUID("aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaab"),
        workspace_id=test_workspace.id,
        workflow_id=mock_workflow.id,
        type="core.http_request",
        title="Test Action",
        description="Test action description",
        status="online",
        inputs="url: https://example.com\nmethod: GET",
        control_flow={},
        is_interactive=False,
        interaction=None,
        created_at=datetime(2024, 1, 1, tzinfo=UTC),
        updated_at=datetime(2024, 1, 1, tzinfo=UTC),
    )
    return action


@pytest.mark.anyio
async def test_list_actions_success(
    client: TestClient,
    test_admin_role: Role,
    mock_workflow: Workflow,
    mock_action: Action,
) -> None:
    """Test GET /actions returns list of actions."""
    with patch.object(
        WorkflowActionService, "list_actions", new_callable=AsyncMock
    ) as mock_list:
        mock_list.return_value = [mock_action]

        workflow_id = str(mock_workflow.id)
        response = client.get(
            "/actions",
            params={
                "workspace_id": str(test_admin_role.workspace_id),
                "workflow_id": workflow_id,
            },
        )

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert len(data) == 1
        assert data[0]["title"] == "Test Action"
        assert data[0]["type"] == "core.http_request"


@pytest.mark.anyio
async def test_create_action_success(
    client: TestClient,
    test_admin_role: Role,
    mock_workflow: Workflow,
    mock_action: Action,
) -> None:
    """Test POST /actions creates a new action."""
    created_action = mock_action
    created_action.title = "New Action"

    with patch.object(
        WorkflowActionService, "create_action", new_callable=AsyncMock
    ) as mock_create:
        mock_create.return_value = created_action

        workflow_id = str(mock_workflow.id)
        response = client.post(
            "/actions",
            params={
                "workspace_id": str(test_admin_role.workspace_id),
            },
            json={
                "workflow_id": workflow_id,
                "type": "core.http_request",
                "title": "New Action",
                "inputs": "url: https://example.com\nmethod: GET",
                "is_interactive": False,
            },
        )

        assert response.status_code in [status.HTTP_200_OK, status.HTTP_201_CREATED]


@pytest.mark.anyio
async def test_create_action_conflict(
    client: TestClient,
    test_admin_role: Role,
    mock_workflow: Workflow,
) -> None:
    """Test POST /actions with duplicate ref returns 409."""
    with patch.object(
        WorkflowActionService, "create_action", new_callable=AsyncMock
    ) as mock_create:
        from tracecat.exceptions import TracecatValidationError

        mock_create.side_effect = TracecatValidationError("ref exists")

        workflow_id = str(mock_workflow.id)
        response = client.post(
            "/actions",
            params={
                "workspace_id": str(test_admin_role.workspace_id),
            },
            json={
                "workflow_id": workflow_id,
                "type": "core.http_request",
                "title": "Test Action",
                "inputs": "url: https://example.com\nmethod: GET",
                "is_interactive": False,
            },
        )

        assert response.status_code == status.HTTP_409_CONFLICT


@pytest.mark.anyio
async def test_get_action_not_found(
    client: TestClient,
    test_admin_role: Role,
    mock_workflow: Workflow,
) -> None:
    """Test GET /actions/{action_id} with non-existent ID returns 404."""
    with patch.object(
        WorkflowActionService, "get_action", new_callable=AsyncMock
    ) as mock_get:
        mock_get.return_value = None

        workflow_id = str(mock_workflow.id)
        fake_action_id = "00000000-0000-4000-8000-000000000000"

        response = client.get(
            f"/actions/{fake_action_id}",
            params={
                "workspace_id": str(test_admin_role.workspace_id),
                "workflow_id": workflow_id,
            },
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.anyio
async def test_update_action_not_found(
    client: TestClient,
    test_admin_role: Role,
    mock_workflow: Workflow,
) -> None:
    """Test POST /actions/{action_id} with non-existent ID returns 404."""
    with patch.object(
        WorkflowActionService, "get_action", new_callable=AsyncMock
    ) as mock_get:
        mock_get.return_value = None

        workflow_id = str(mock_workflow.id)
        fake_action_id = "00000000-0000-4000-8000-000000000000"

        response = client.post(
            f"/actions/{fake_action_id}",
            params={
                "workspace_id": str(test_admin_role.workspace_id),
                "workflow_id": workflow_id,
            },
            json={
                "title": "Updated Title",
            },
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.anyio
async def test_delete_action_success(
    client: TestClient,
    test_admin_role: Role,
    mock_workflow: Workflow,
    mock_action: Action,
) -> None:
    """Test DELETE /actions/{action_id} deletes action."""
    workflow_id = str(mock_workflow.id)

    with patch.object(
        WorkflowActionService, "get_action", new_callable=AsyncMock
    ) as mock_get:
        mock_get.return_value = mock_action

        delete_response = client.delete(
            f"/actions/{mock_action.id}",
            params={
                "workspace_id": str(test_admin_role.workspace_id),
                "workflow_id": workflow_id,
            },
        )

        assert delete_response.status_code == status.HTTP_204_NO_CONTENT
