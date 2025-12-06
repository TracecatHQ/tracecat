"""HTTP-level tests for workflow graph API endpoints."""

import uuid
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException, status
from fastapi.testclient import TestClient

from tracecat.auth.types import Role
from tracecat.db.models import Workflow, Workspace
from tracecat.workflow.graph.service import WorkflowGraphService
from tracecat.workflow.management.schemas import GraphResponse


@pytest.fixture
def mock_workflow(test_workspace: Workspace) -> Workflow:
    """Create a mock workflow DB object for routing tests."""

    workflow_id = uuid.UUID("aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa")
    return Workflow(
        id=workflow_id,
        title="Graph Workflow",
        description="Graph workflow description",
        status="online",
        version=1,
        workspace_id=test_workspace.id,
        entrypoint="action-1",
        expects={},
        returns=None,
        object=None,
        config={},
        alias="graph-workflow",
        error_handler=None,
        icon_url=None,
        trigger_position_x=10.0,
        trigger_position_y=20.0,
        graph_version=3,
    )


def _graph_response(version: int = 3) -> GraphResponse:
    return GraphResponse(
        version=version,
        nodes=[{"id": "trigger-1", "type": "trigger"}],
        edges=[],
        viewport={"x": 0, "y": 0, "zoom": 1},
    )


@pytest.mark.anyio
async def test_get_graph_success(
    client: TestClient, test_admin_role: Role, mock_workflow: Workflow
) -> None:
    """GET /workflows/{id}/graph returns graph projection."""

    with patch.object(
        WorkflowGraphService, "get_graph", new_callable=AsyncMock
    ) as mock_get:
        mock_get.return_value = _graph_response(version=mock_workflow.graph_version)

        response = client.get(
            f"/workflows/{mock_workflow.id}/graph",
            params={"workspace_id": str(test_admin_role.workspace_id)},
        )

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["version"] == mock_workflow.graph_version
        assert data["nodes"][0]["type"] == "trigger"


@pytest.mark.anyio
async def test_get_graph_not_found(
    client: TestClient, test_admin_role: Role, mock_workflow: Workflow
) -> None:
    """GET /workflows/{id}/graph returns 404 when missing."""

    with patch.object(
        WorkflowGraphService, "get_graph", new_callable=AsyncMock
    ) as mock_get:
        mock_get.return_value = None

        response = client.get(
            f"/workflows/{mock_workflow.id}/graph",
            params={"workspace_id": str(test_admin_role.workspace_id)},
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.anyio
async def test_apply_operations_success(
    client: TestClient, test_admin_role: Role, mock_workflow: Workflow
) -> None:
    """PATCH /workflows/{id}/graph applies operations and returns graph."""

    with patch.object(
        WorkflowGraphService, "apply_operations", new_callable=AsyncMock
    ) as mock_apply:
        mock_apply.return_value = _graph_response(version=mock_workflow.graph_version)

        response = client.patch(
            f"/workflows/{mock_workflow.id}/graph",
            params={"workspace_id": str(test_admin_role.workspace_id)},
            json={"base_version": 3, "operations": []},
        )

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["version"] == mock_workflow.graph_version


@pytest.mark.anyio
async def test_apply_operations_validation_error(
    client: TestClient, test_admin_role: Role, mock_workflow: Workflow
) -> None:
    """PATCH /workflows/{id}/graph returns 400 on validation error."""

    with patch.object(
        WorkflowGraphService, "apply_operations", new_callable=AsyncMock
    ) as mock_apply:
        mock_apply.side_effect = ValueError("bad payload")

        response = client.patch(
            f"/workflows/{mock_workflow.id}/graph",
            params={"workspace_id": str(test_admin_role.workspace_id)},
            json={"base_version": 3, "operations": []},
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.anyio
async def test_apply_operations_not_found(
    client: TestClient, test_admin_role: Role, mock_workflow: Workflow
) -> None:
    """PATCH /workflows/{id}/graph returns 404 when workflow missing."""

    with patch.object(
        WorkflowGraphService, "apply_operations", new_callable=AsyncMock
    ) as mock_apply:
        mock_apply.return_value = None

        response = client.patch(
            f"/workflows/{mock_workflow.id}/graph",
            params={"workspace_id": str(test_admin_role.workspace_id)},
            json={"base_version": 3, "operations": []},
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.anyio
async def test_apply_operations_conflict(
    client: TestClient, test_admin_role: Role, mock_workflow: Workflow
) -> None:
    """PATCH /workflows/{id}/graph surfaces 409 from service."""

    with patch.object(
        WorkflowGraphService, "apply_operations", new_callable=AsyncMock
    ) as mock_apply:
        mock_apply.side_effect = HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Version conflict"
        )

        response = client.patch(
            f"/workflows/{mock_workflow.id}/graph",
            params={"workspace_id": str(test_admin_role.workspace_id)},
            json={"base_version": 3, "operations": []},
        )

        assert response.status_code == status.HTTP_409_CONFLICT
