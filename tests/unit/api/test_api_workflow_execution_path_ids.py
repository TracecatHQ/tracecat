"""HTTP-level tests for workflow execution routes that accept slash-delimited IDs."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, Mock, patch
from urllib.parse import quote

import pytest
from fastapi import status
from fastapi.testclient import TestClient
from temporalio.client import WorkflowExecutionStatus

from tracecat.auth.types import Role
from tracecat.workflow.executions import internal_router as internal_executions_router
from tracecat.workflow.executions import router as executions_router

# --- Internal Router: GET /executions/{execution_id} ---


@pytest.mark.anyio
async def test_internal_get_execution_status_accepts_slash_id(
    client: TestClient,
    test_admin_role: Role,
) -> None:
    """Test that the internal router accepts execution IDs with slash delimiter."""
    wf_exec_id = "wf_abc/exec_def"

    mock_execution = Mock()
    mock_execution.status = WorkflowExecutionStatus.RUNNING
    mock_execution.start_time = datetime(2024, 1, 1, tzinfo=UTC)
    mock_execution.close_time = None

    mock_svc = AsyncMock()
    mock_svc.get_execution.return_value = mock_execution

    with patch.object(
        internal_executions_router.WorkflowExecutionsService,
        "connect",
        AsyncMock(return_value=mock_svc),
    ):
        response = client.get(f"/internal/workflows/executions/{wf_exec_id}")

    assert response.status_code == status.HTTP_200_OK
    payload = response.json()
    assert payload["workflow_execution_id"] == wf_exec_id
    assert payload["status"] == "RUNNING"
    mock_svc.get_execution.assert_awaited_once_with(wf_exec_id)


@pytest.mark.anyio
async def test_internal_get_execution_status_accepts_url_encoded_slash(
    client: TestClient,
    test_admin_role: Role,
) -> None:
    """Test that URL-encoded slash (%2F) is decoded correctly."""
    wf_exec_id = "wf_abc/exec_def"
    encoded_id = quote(wf_exec_id, safe="")  # wf_abc%2Fexec_def

    mock_execution = Mock()
    mock_execution.status = WorkflowExecutionStatus.COMPLETED
    mock_execution.start_time = datetime(2024, 1, 1, tzinfo=UTC)
    mock_execution.close_time = datetime(2024, 1, 1, 0, 5, tzinfo=UTC)

    # Setup mock handle with async result method
    mock_handle = Mock()
    mock_handle.result = AsyncMock(return_value={"output": "success"})

    mock_svc = AsyncMock()
    mock_svc.get_execution.return_value = mock_execution
    mock_svc.handle = Mock(return_value=mock_handle)

    with patch.object(
        internal_executions_router.WorkflowExecutionsService,
        "connect",
        AsyncMock(return_value=mock_svc),
    ):
        response = client.get(f"/internal/workflows/executions/{encoded_id}")

    assert response.status_code == status.HTTP_200_OK
    payload = response.json()
    # The path parameter should be URL-decoded
    assert payload["workflow_execution_id"] == wf_exec_id
    assert payload["status"] == "COMPLETED"


@pytest.mark.anyio
async def test_internal_get_execution_status_accepts_colon_delimiter(
    client: TestClient,
    test_admin_role: Role,
) -> None:
    """Test that execution IDs with colon delimiter are accepted."""
    wf_exec_id = "wf_abc:exec_def"

    mock_execution = Mock()
    mock_execution.status = WorkflowExecutionStatus.RUNNING
    mock_execution.start_time = datetime(2024, 1, 1, tzinfo=UTC)
    mock_execution.close_time = None

    mock_svc = AsyncMock()
    mock_svc.get_execution.return_value = mock_execution

    with patch.object(
        internal_executions_router.WorkflowExecutionsService,
        "connect",
        AsyncMock(return_value=mock_svc),
    ):
        response = client.get(f"/internal/workflows/executions/{wf_exec_id}")

    assert response.status_code == status.HTTP_200_OK
    payload = response.json()
    assert payload["workflow_execution_id"] == wf_exec_id
    mock_svc.get_execution.assert_awaited_once_with(wf_exec_id)


@pytest.mark.anyio
async def test_internal_get_execution_status_returns_404_when_not_found(
    client: TestClient,
    test_admin_role: Role,
) -> None:
    """Test that 404 is returned when execution is not found."""
    wf_exec_id = "wf_notfound/exec_notfound"

    mock_svc = AsyncMock()
    mock_svc.get_execution.return_value = None

    with patch.object(
        internal_executions_router.WorkflowExecutionsService,
        "connect",
        AsyncMock(return_value=mock_svc),
    ):
        response = client.get(f"/internal/workflows/executions/{wf_exec_id}")

    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert "not found" in response.json()["detail"].lower()


@pytest.mark.anyio
async def test_internal_get_execution_status_returns_error_for_failed_workflow(
    client: TestClient,
    test_admin_role: Role,
) -> None:
    """Test that error details are returned for failed workflows."""
    from temporalio.client import WorkflowFailureError
    from temporalio.exceptions import ApplicationError

    wf_exec_id = "wf_abc/exec_failed"

    mock_execution = Mock()
    mock_execution.status = WorkflowExecutionStatus.FAILED
    mock_execution.start_time = datetime(2024, 1, 1, tzinfo=UTC)
    mock_execution.close_time = datetime(2024, 1, 1, 0, 1, tzinfo=UTC)

    # WorkflowFailureError requires a real exception as cause
    cause_error = ApplicationError("Activity failed: connection timeout")

    # Setup mock handle with async result method
    mock_handle = Mock()
    mock_handle.result = AsyncMock(side_effect=WorkflowFailureError(cause=cause_error))

    mock_svc = AsyncMock()
    mock_svc.get_execution.return_value = mock_execution
    mock_svc.handle = Mock(return_value=mock_handle)

    with patch.object(
        internal_executions_router.WorkflowExecutionsService,
        "connect",
        AsyncMock(return_value=mock_svc),
    ):
        response = client.get(f"/internal/workflows/executions/{wf_exec_id}")

    assert response.status_code == status.HTTP_200_OK
    payload = response.json()
    assert payload["status"] == "FAILED"
    assert payload["error"] is not None
    assert "connection timeout" in payload["error"]


@pytest.mark.anyio
async def test_internal_get_execution_status_unwraps_nested_failure_cause(
    client: TestClient,
    test_admin_role: Role,
) -> None:
    """Test that nested ActivityError-style causes are unwrapped to root message."""
    from temporalio.client import WorkflowFailureError
    from temporalio.exceptions import ApplicationError

    wf_exec_id = "wf_abc/exec_failed_nested"

    mock_execution = Mock()
    mock_execution.status = WorkflowExecutionStatus.FAILED
    mock_execution.start_time = datetime(2024, 1, 1, tzinfo=UTC)
    mock_execution.close_time = datetime(2024, 1, 1, 0, 1, tzinfo=UTC)

    class FakeActivityError(Exception):
        def __init__(self, message: str, cause: Exception | None = None) -> None:
            super().__init__(message)
            self.cause = cause

    root_cause = ApplicationError(
        "EntitlementRequired: Feature 'custom_registry' requires an upgraded plan"
    )
    activity_wrapper = FakeActivityError("Activity task failed", cause=root_cause)

    mock_handle = Mock()
    mock_handle.result = AsyncMock(
        side_effect=WorkflowFailureError(cause=activity_wrapper)
    )

    mock_svc = AsyncMock()
    mock_svc.get_execution.return_value = mock_execution
    mock_svc.handle = Mock(return_value=mock_handle)

    with patch.object(
        internal_executions_router.WorkflowExecutionsService,
        "connect",
        AsyncMock(return_value=mock_svc),
    ):
        response = client.get(f"/internal/workflows/executions/{wf_exec_id}")

    assert response.status_code == status.HTTP_200_OK
    payload = response.json()
    assert payload["status"] == "FAILED"
    assert payload["error"] is not None
    assert "custom_registry" in payload["error"]
    assert "Activity task failed" not in payload["error"]


# --- Internal Router: POST /executions ---


@pytest.mark.anyio
async def test_internal_execute_workflow_by_id(
    client: TestClient,
    test_admin_role: Role,
) -> None:
    """Test executing a workflow by workflow_id."""
    # Use a proper short ID format that won't be transformed
    workflow_id = "wf_4itKqkgCZrLhgYiq5L211X"
    wf_exec_id = f"{workflow_id}/exec_abc"

    mock_defn = Mock()
    mock_defn.content = {
        "title": "Test Workflow",
        "description": "Test",
        "entrypoint": {"ref": "start"},
        "actions": [{"ref": "start", "action": "core.noop"}],
        "config": {"enable_runtime_tests": False},
    }
    mock_defn.registry_lock = None

    mock_defn_service = AsyncMock()
    mock_defn_service.get_definition_by_workflow_id.return_value = mock_defn

    # create_workflow_execution_nowait is synchronous, use Mock not AsyncMock
    mock_exec_service = AsyncMock()
    mock_exec_service.create_workflow_execution_nowait = Mock(
        return_value={
            "wf_id": workflow_id,
            "wf_exec_id": wf_exec_id,
            "message": "Workflow execution started",
        }
    )

    with (
        patch.object(
            internal_executions_router.WorkflowDefinitionsService,
            "__init__",
            lambda self, session, role: None,
        ),
        patch.object(
            internal_executions_router.WorkflowDefinitionsService,
            "get_definition_by_workflow_id",
            mock_defn_service.get_definition_by_workflow_id,
        ),
        patch.object(
            internal_executions_router.WorkflowExecutionsService,
            "connect",
            AsyncMock(return_value=mock_exec_service),
        ),
    ):
        response = client.post(
            "/internal/workflows/executions",
            json={"workflow_id": workflow_id, "trigger_inputs": {"key": "value"}},
        )

    assert response.status_code == status.HTTP_201_CREATED
    payload = response.json()
    assert "workflow_id" in payload
    assert payload["workflow_execution_id"] == wf_exec_id


@pytest.mark.anyio
async def test_internal_execute_workflow_by_alias(
    client: TestClient,
    test_admin_role: Role,
) -> None:
    """Test executing a workflow by workflow_alias."""
    workflow_alias = "my-workflow"
    # Use a proper short ID format
    workflow_id = "wf_4itKqkgCZrLhgYiq5L211X"
    wf_exec_id = f"{workflow_id}/exec_xyz"

    mock_wf_service = AsyncMock()
    mock_wf_service.resolve_workflow_alias.return_value = workflow_id

    mock_defn = Mock()
    mock_defn.content = {
        "title": "Test Workflow",
        "description": "Test",
        "entrypoint": {"ref": "start"},
        "actions": [{"ref": "start", "action": "core.noop"}],
        "config": {"enable_runtime_tests": False},
    }
    mock_defn.registry_lock = None

    mock_defn_service = AsyncMock()
    mock_defn_service.get_definition_by_workflow_id.return_value = mock_defn

    # create_workflow_execution_nowait is synchronous, use Mock not AsyncMock
    mock_exec_service = AsyncMock()
    mock_exec_service.create_workflow_execution_nowait = Mock(
        return_value={
            "wf_id": workflow_id,
            "wf_exec_id": wf_exec_id,
            "message": "Workflow execution started",
        }
    )

    with (
        patch.object(
            internal_executions_router.WorkflowsManagementService,
            "__init__",
            lambda self, session, role: None,
        ),
        patch.object(
            internal_executions_router.WorkflowsManagementService,
            "resolve_workflow_alias",
            mock_wf_service.resolve_workflow_alias,
        ),
        patch.object(
            internal_executions_router.WorkflowDefinitionsService,
            "__init__",
            lambda self, session, role: None,
        ),
        patch.object(
            internal_executions_router.WorkflowDefinitionsService,
            "get_definition_by_workflow_id",
            mock_defn_service.get_definition_by_workflow_id,
        ),
        patch.object(
            internal_executions_router.WorkflowExecutionsService,
            "connect",
            AsyncMock(return_value=mock_exec_service),
        ),
    ):
        response = client.post(
            "/internal/workflows/executions",
            json={"workflow_alias": workflow_alias},
        )

    assert response.status_code == status.HTTP_201_CREATED
    payload = response.json()
    assert "workflow_id" in payload
    assert payload["workflow_execution_id"] == wf_exec_id


@pytest.mark.anyio
async def test_internal_execute_workflow_returns_404_for_unknown_alias(
    client: TestClient,
    test_admin_role: Role,
) -> None:
    """Test that 404 is returned when workflow alias is not found."""
    mock_wf_service = AsyncMock()
    mock_wf_service.resolve_workflow_alias.return_value = None

    with (
        patch.object(
            internal_executions_router.WorkflowsManagementService,
            "__init__",
            lambda self, session, role: None,
        ),
        patch.object(
            internal_executions_router.WorkflowsManagementService,
            "resolve_workflow_alias",
            mock_wf_service.resolve_workflow_alias,
        ),
    ):
        response = client.post(
            "/internal/workflows/executions",
            json={"workflow_alias": "nonexistent-alias"},
        )

    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert "not found" in response.json()["detail"].lower()


@pytest.mark.anyio
async def test_internal_execute_workflow_returns_400_when_no_id_or_alias(
    client: TestClient,
    test_admin_role: Role,
) -> None:
    """Test that 400 is returned when neither workflow_id nor workflow_alias provided."""
    response = client.post(
        "/internal/workflows/executions",
        json={},
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert "either workflow_id or workflow_alias" in response.json()["detail"].lower()


@pytest.mark.anyio
async def test_internal_execute_workflow_returns_404_for_missing_definition(
    client: TestClient,
    test_admin_role: Role,
) -> None:
    """Test that 404 is returned when workflow definition is not found."""
    workflow_id = "wf_nodefinition"

    mock_defn_service = AsyncMock()
    mock_defn_service.get_definition_by_workflow_id.return_value = None

    with (
        patch.object(
            internal_executions_router.WorkflowDefinitionsService,
            "__init__",
            lambda self, session, role: None,
        ),
        patch.object(
            internal_executions_router.WorkflowDefinitionsService,
            "get_definition_by_workflow_id",
            mock_defn_service.get_definition_by_workflow_id,
        ),
    ):
        response = client.post(
            "/internal/workflows/executions",
            json={"workflow_id": workflow_id},
        )

    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert "definition" in response.json()["detail"].lower()


@pytest.mark.anyio
async def test_get_workflow_execution_compact_accepts_slash_id(
    client: TestClient,
    test_admin_role: Role,
) -> None:
    wf_exec_id = "wf_abc/exec_def"

    mock_execution = Mock()
    mock_execution.id = wf_exec_id
    mock_execution.parent_id = None
    mock_execution.run_id = "run_123"
    mock_execution.start_time = datetime(2024, 1, 1, tzinfo=UTC)
    mock_execution.execution_time = None
    mock_execution.close_time = None
    mock_execution.status = WorkflowExecutionStatus.RUNNING
    mock_execution.workflow_type = "DSLWorkflow"
    mock_execution.task_queue = "tracecat-task-queue"
    mock_execution.history_length = 0
    mock_execution.typed_search_attributes = {}

    mock_svc = AsyncMock()
    mock_svc.get_execution.return_value = mock_execution
    mock_svc.list_workflow_execution_events_compact.return_value = []

    with (
        patch.object(
            executions_router.WorkflowExecutionsService,
            "connect",
            AsyncMock(return_value=mock_svc),
        ),
        patch.object(
            executions_router,
            "_list_interactions",
            AsyncMock(return_value=[]),
        ),
    ):
        response = client.get(f"/workflow-executions/{wf_exec_id}/compact")

    assert response.status_code == status.HTTP_200_OK
    payload = response.json()
    assert payload["id"] == wf_exec_id
    assert payload["status"] == "RUNNING"
    mock_svc.get_execution.assert_awaited_once_with(wf_exec_id)
    mock_svc.list_workflow_execution_events_compact.assert_awaited_once_with(wf_exec_id)
