"""HTTP coverage for platform maintenance endpoints."""

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
from fastapi import status
from fastapi.testclient import TestClient
from temporalio.client import WorkflowExecutionStatus
from temporalio.common import WorkflowIDConflictPolicy, WorkflowIDReusePolicy

import tracecat.admin.maintenance.router as maintenance_router_module
from tracecat.auth.types import Role
from tracecat.cases.agent_sessions.types import (
    CaseAgentSessionBackfillReport,
    CaseAgentSessionBackfillSkipReason,
)


@pytest.mark.anyio
async def test_case_agent_session_interaction_backfill(
    client: TestClient,
    test_admin_role: Role,
) -> None:
    report = CaseAgentSessionBackfillReport(
        batches_processed=2,
        sessions_scanned=3,
        history_rows_scanned=6,
        mutation_candidates=4,
        inserted=3,
        existing=1,
        skipped={CaseAgentSessionBackfillSkipReason.FAILED_TOOL_CALL: 1},
    )
    operation_id = uuid.uuid4()
    handle = SimpleNamespace(
        result_run_id=str(operation_id),
        describe=AsyncMock(
            side_effect=[
                SimpleNamespace(status=WorkflowExecutionStatus.RUNNING),
                SimpleNamespace(status=WorkflowExecutionStatus.COMPLETED),
            ]
        ),
        result=AsyncMock(return_value=report),
    )
    temporal_client = Mock(
        start_workflow=AsyncMock(return_value=handle),
        get_workflow_handle_for=Mock(return_value=handle),
    )

    with patch.object(
        maintenance_router_module,
        "get_temporal_client",
        AsyncMock(return_value=temporal_client),
    ):
        start_response = client.post(
            "/admin/maintenance/case-agent-session-interactions/backfill"
        )
        running_response = client.get(
            "/admin/maintenance/case-agent-session-interactions/backfill/"
            f"{operation_id}"
        )
        completed_response = client.get(
            "/admin/maintenance/case-agent-session-interactions/backfill/"
            f"{operation_id}"
        )

    assert start_response.status_code == status.HTTP_202_ACCEPTED
    assert start_response.json() == {"operation_id": str(operation_id)}
    assert running_response.json() == {
        "operation_id": str(operation_id),
        "status": "running",
        "report": None,
    }
    assert completed_response.json() == {
        "operation_id": str(operation_id),
        "status": "completed",
        "report": {
            "batches_processed": 2,
            "sessions_scanned": 3,
            "history_rows_scanned": 6,
            "mutation_candidates": 4,
            "inserted": 3,
            "existing": 1,
            "skipped": {"failed_tool_call": 1},
        },
    }
    start_call = temporal_client.start_workflow.await_args
    assert (
        start_call.kwargs["task_queue"]
        == maintenance_router_module.config.TEMPORAL__CLUSTER_QUEUE
    )
    assert start_call.kwargs["id_reuse_policy"] == (
        WorkflowIDReusePolicy.ALLOW_DUPLICATE
    )
    assert start_call.kwargs["id_conflict_policy"] == (
        WorkflowIDConflictPolicy.USE_EXISTING
    )
    assert temporal_client.get_workflow_handle_for.call_args.kwargs["run_id"] == str(
        operation_id
    )
    handle.result.assert_awaited_once_with()
