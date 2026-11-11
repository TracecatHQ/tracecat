"""HTTP-level tests for workflow graph API endpoints."""

import uuid
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException, status
from fastapi.testclient import TestClient

from tracecat.auth.types import Role
from tracecat.contexts import ctx_role
from tracecat.db.models import Workflow, Workspace
from tracecat.workflow.graph.service import WorkflowGraphService


@pytest.fixture(autouse=True)
def no_managed_lookup(monkeypatch: pytest.MonkeyPatch) -> None:
    """These tests use mocked sessions, which cannot answer managed lookups."""
    monkeypatch.setattr(
        "tracecat.managed_resources.guard.is_managed",
        AsyncMock(return_value=False),
    )


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
        config={},
        alias="graph-workflow",
        error_handler=None,
        icon_url=None,
        trigger_position_x=10.0,
        trigger_position_y=20.0,
        graph_version=3,
    )


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
async def test_get_graph_requires_read_scope(
    client: TestClient, test_admin_role: Role, mock_workflow: Workflow
) -> None:
    """GET /workflows/{id}/graph requires workflow:read."""

    role = test_admin_role.model_copy(update={"scopes": frozenset({"case:read"})})
    token = ctx_role.set(role)
    try:
        with patch.object(
            WorkflowGraphService, "get_graph", new_callable=AsyncMock
        ) as mock_get:
            response = client.get(
                f"/workflows/{mock_workflow.id}/graph",
                params={"workspace_id": str(test_admin_role.workspace_id)},
            )
    finally:
        ctx_role.reset(token)

    assert response.status_code == status.HTTP_403_FORBIDDEN
    mock_get.assert_not_called()


@pytest.mark.anyio
async def test_apply_operations_requires_update_scope(
    client: TestClient, test_admin_role: Role, mock_workflow: Workflow
) -> None:
    """PATCH /workflows/{id}/graph requires workflow:update."""

    role = test_admin_role.model_copy(update={"scopes": frozenset({"workflow:read"})})
    token = ctx_role.set(role)
    try:
        with patch.object(
            WorkflowGraphService, "apply_operations", new_callable=AsyncMock
        ) as mock_apply:
            response = client.patch(
                f"/workflows/{mock_workflow.id}/graph",
                params={"workspace_id": str(test_admin_role.workspace_id)},
                json={"base_version": 3, "operations": []},
            )
    finally:
        ctx_role.reset(token)

    assert response.status_code == status.HTTP_403_FORBIDDEN
    mock_apply.assert_not_called()


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
