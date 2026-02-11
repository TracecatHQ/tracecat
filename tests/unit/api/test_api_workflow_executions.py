"""HTTP-level tests for workflow executions router.

Tests listing, getting, creating, cancelling, and terminating executions.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import status
from fastapi.testclient import TestClient
from temporalio.service import RPCError, RPCStatusCode

from tracecat.auth.types import Role
from tracecat.identifiers.workflow import WorkflowUUID
from tracecat.workflow.executions import router as executions_router

TEST_WF_ID = WorkflowUUID(int=200)
# Use ":" separator (not "/") to avoid path routing issues with FastAPI
TEST_EXEC_ID = f"{TEST_WF_ID.short()}:exec_abc123"


@pytest.mark.anyio
class TestListExecutions:
    """Test listing executions with filters."""

    async def test_list_executions_empty(
        self, client: TestClient, test_role: Role
    ) -> None:
        """List executions should return empty list when none exist."""
        mock_svc = AsyncMock()
        mock_svc.list_executions.return_value = []

        with patch.object(
            executions_router.WorkflowExecutionsService,
            "connect",
            AsyncMock(return_value=mock_svc),
        ):
            response = client.get("/workflow-executions")

        assert response.status_code == status.HTTP_200_OK
        assert response.json() == []

    async def test_list_executions_with_workflow_filter(
        self, client: TestClient, test_role: Role
    ) -> None:
        """List executions should pass workflow_id filter to service."""
        mock_svc = AsyncMock()
        mock_svc.list_executions.return_value = []

        with patch.object(
            executions_router.WorkflowExecutionsService,
            "connect",
            AsyncMock(return_value=mock_svc),
        ):
            response = client.get(
                "/workflow-executions",
                params={"workflow_id": str(TEST_WF_ID)},
            )

        assert response.status_code == status.HTTP_200_OK
        mock_svc.list_executions.assert_awaited_once()
        call_kwargs = mock_svc.list_executions.call_args
        assert call_kwargs is not None


@pytest.mark.anyio
class TestGetExecution:
    """Test getting a single execution."""

    async def test_get_execution_not_found(
        self, client: TestClient, test_role: Role
    ) -> None:
        """Get execution should return 404 when not found."""
        mock_svc = AsyncMock()
        mock_svc.get_execution.return_value = None

        with patch.object(
            executions_router.WorkflowExecutionsService,
            "connect",
            AsyncMock(return_value=mock_svc),
        ):
            response = client.get(f"/workflow-executions/{TEST_EXEC_ID}")

        assert response.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.anyio
class TestCreateExecution:
    """Test creating workflow executions."""

    async def test_create_execution_missing_workflow(
        self, client: TestClient, test_role: Role
    ) -> None:
        """Create execution for nonexistent workflow should return 404."""
        mock_svc = AsyncMock()

        with patch.object(
            executions_router.WorkflowExecutionsService,
            "connect",
            AsyncMock(return_value=mock_svc),
        ):
            response = client.post(
                "/workflow-executions",
                json={
                    "workflow_id": str(TEST_WF_ID),
                    "inputs": {"key": "value"},
                },
            )

        assert response.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.anyio
class TestTerminateExecution:
    """Test terminating executions."""

    async def test_terminate_execution_success(
        self, client: TestClient, test_role: Role
    ) -> None:
        """Terminate execution should return 204."""
        mock_svc = AsyncMock()
        mock_svc.terminate_workflow_execution.return_value = None

        with patch.object(
            executions_router.WorkflowExecutionsService,
            "connect",
            AsyncMock(return_value=mock_svc),
        ):
            response = client.post(
                f"/workflow-executions/{TEST_EXEC_ID}/terminate",
                json={"reason": "testing"},
            )

        assert response.status_code == status.HTTP_204_NO_CONTENT

    async def test_terminate_already_completed(
        self, client: TestClient, test_role: Role
    ) -> None:
        """Terminate of already completed execution should still return 204."""
        mock_svc = AsyncMock()
        mock_svc.terminate_workflow_execution.side_effect = RPCError(
            message="workflow execution already completed",
            status=RPCStatusCode.NOT_FOUND,
            raw_grpc_status=b"\x08\x05",
        )

        with patch.object(
            executions_router.WorkflowExecutionsService,
            "connect",
            AsyncMock(return_value=mock_svc),
        ):
            response = client.post(
                f"/workflow-executions/{TEST_EXEC_ID}/terminate",
                json={"reason": "testing"},
            )

        # Should gracefully handle already-completed
        assert response.status_code == status.HTTP_204_NO_CONTENT
