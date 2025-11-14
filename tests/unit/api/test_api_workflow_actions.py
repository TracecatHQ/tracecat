"""HTTP-level tests for workflow actions API endpoints."""

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import status
from fastapi.testclient import TestClient

from tracecat.api.app import app
from tracecat.auth.types import Role
from tracecat.db.engine import get_async_session
from tracecat.db.models import Action, Workflow


@pytest.fixture
def mock_workflow(test_workspace) -> Workflow:
    """Create a mock workflow DB object."""
    workflow_id = uuid.UUID("aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa")
    return Workflow(
        id=workflow_id,
        title="Test Workflow",
        description="Test workflow description",
        status="online",
        version=1,
        owner_id=test_workspace.id,
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
def mock_action(test_workspace, mock_workflow) -> Action:
    """Create a mock action DB object."""
    action = Action(
        id="act-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        owner_id=test_workspace.id,
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
    mock_workflow,
    mock_action: Action,
) -> None:
    """Test GET /actions returns list of actions."""
    mock_session = AsyncMock()
    mock_result = AsyncMock()
    mock_result.all = MagicMock(return_value=[mock_action])
    mock_session.exec.return_value = mock_result

    # Override get_async_session dependency
    async def get_mock_session() -> AsyncMock:
        return mock_session

    app.dependency_overrides[get_async_session] = get_mock_session

    try:
        # Make request
        workflow_id = str(mock_workflow.id)
        response = client.get(
            f"/actions?workspace_id={test_admin_role.workspace_id}&workflow_id={workflow_id}"
        )

        # Assertions
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert len(data) == 1
        assert data[0]["title"] == "Test Action"
        assert data[0]["type"] == "core.http_request"
    finally:
        # Clean up override
        app.dependency_overrides.pop(get_async_session, None)


@pytest.mark.anyio
async def test_create_action_success(
    client: TestClient,
    test_admin_role: Role,
    mock_workflow,
) -> None:
    """Test POST /actions creates a new action."""
    mock_session = AsyncMock()
    mock_session.add = MagicMock()
    mock_session.commit = AsyncMock()
    mock_session.refresh = AsyncMock()
    mock_result = AsyncMock()
    mock_result.first = MagicMock(return_value=None)
    mock_session.exec.return_value = mock_result

    async def get_mock_session() -> AsyncMock:
        return mock_session

    app.dependency_overrides[get_async_session] = get_mock_session

    # Make request
    workflow_id = str(mock_workflow.id)
    try:
        response = client.post(
            f"/actions?workspace_id={test_admin_role.workspace_id}",
            json={
                "workflow_id": workflow_id,
                "type": "core.http_request",
                "title": "New Action",
                "inputs": "url: https://example.com\nmethod: GET",
                "is_interactive": False,
            },
        )
    finally:
        app.dependency_overrides.pop(get_async_session, None)

    # Assertions - This will actually hit the database
    # Just check it doesn't error
    assert response.status_code in [status.HTTP_200_OK, status.HTTP_201_CREATED]


@pytest.mark.anyio
async def test_create_action_conflict(
    client: TestClient,
    test_admin_role: Role,
    mock_workflow,
) -> None:
    """Test POST /actions with duplicate ref returns 409."""
    mock_session = AsyncMock()
    mock_session.add = MagicMock()
    mock_session.commit = AsyncMock()
    mock_session.refresh = AsyncMock()
    mock_result = AsyncMock()
    mock_result.first = MagicMock(return_value=object())
    mock_session.exec.return_value = mock_result

    async def get_mock_session() -> AsyncMock:
        return mock_session

    app.dependency_overrides[get_async_session] = get_mock_session

    workflow_id = str(mock_workflow.id)
    try:
        response = client.post(
            f"/actions?workspace_id={test_admin_role.workspace_id}",
            json={
                "workflow_id": workflow_id,
                "type": "core.http_request",
                "title": "Test Action",
                "inputs": "url: https://example.com\nmethod: GET",
                "is_interactive": False,
            },
        )
    finally:
        app.dependency_overrides.pop(get_async_session, None)

    # Should return 409 conflict
    assert response.status_code == status.HTTP_409_CONFLICT


@pytest.mark.anyio
async def test_get_action_not_found(
    client: TestClient,
    test_admin_role: Role,
    mock_workflow,
) -> None:
    """Test GET /actions/{action_id} with non-existent ID returns 404."""
    workflow_id = str(mock_workflow.id)
    # Use proper action ID format: act-[0-9a-f]{32}
    fake_action_id = "act-00000000000000000000000000000000"

    # Make request
    response = client.get(
        f"/actions/{fake_action_id}?workspace_id={test_admin_role.workspace_id}&workflow_id={workflow_id}"
    )

    # Should return 404
    assert response.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.anyio
async def test_update_action_not_found(
    client: TestClient,
    test_admin_role: Role,
) -> None:
    """Test POST /actions/{action_id} with non-existent ID returns 404."""
    # Use proper action ID format: act-[0-9a-f]{32}
    fake_action_id = "act-00000000000000000000000000000000"

    # Make request
    response = client.post(
        f"/actions/{fake_action_id}?workspace_id={test_admin_role.workspace_id}",
        json={
            "title": "Updated Title",
        },
    )

    # Should return 404
    assert response.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.anyio
async def test_delete_action_success(
    client: TestClient,
    test_admin_role: Role,
    mock_workflow,
    mock_action: Action,
) -> None:
    """Test DELETE /actions/{action_id} deletes action."""
    workflow_id = str(mock_workflow.id)

    mock_session = AsyncMock()
    mock_result = AsyncMock()
    mock_result.one = MagicMock(return_value=mock_action)
    mock_session.exec.return_value = mock_result
    mock_session.delete = AsyncMock()
    mock_session.commit = AsyncMock()

    async def get_mock_session() -> AsyncMock:
        return mock_session

    app.dependency_overrides[get_async_session] = get_mock_session

    try:
        delete_response = client.delete(
            f"/actions/{mock_action.id}?workspace_id={test_admin_role.workspace_id}&workflow_id={workflow_id}"
        )
    finally:
        app.dependency_overrides.pop(get_async_session, None)

    # Assertions
    assert delete_response.status_code == status.HTTP_204_NO_CONTENT
