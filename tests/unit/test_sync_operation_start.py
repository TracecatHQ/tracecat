"""Direct Temporal starts retain request identity and report ambiguous failures."""

from datetime import timedelta
from unittest.mock import AsyncMock, Mock

import pytest
from temporalio import workflow
from temporalio.common import WorkflowIDReusePolicy
from temporalio.exceptions import ActivityError, WorkflowAlreadyStartedError

from tests.unit.test_durable_workspace_sync import push_inputs
from tracecat.workspace_sync.operations.service import SyncOperationService
from tracecat.workspace_sync.operations.types import (
    SyncOperationRef,
    SyncOperationStartError,
)
from tracecat.workspace_sync.operations.workflows import WorkspaceSyncWorkflow


@pytest.mark.anyio
async def test_unknown_start_result_reuses_workflow_identity(
    session, svc_role, monkeypatch
):
    service = SyncOperationService(session, svc_role)
    operation = await service.create(push_inputs())
    workflow_id = f"workspace-sync/{svc_role.workspace_id}/{operation.id}/preview/0"
    client = AsyncMock()
    client.start_workflow.side_effect = [
        TimeoutError("Synthetic provider detail"),
        WorkflowAlreadyStartedError(workflow_id, "WorkspaceSyncWorkflow"),
    ]
    monkeypatch.setattr(
        "tracecat.workspace_sync.operations.service.get_temporal_client",
        AsyncMock(return_value=client),
    )
    with pytest.raises(SyncOperationStartError) as failure:
        await service.start_workflow(operation)
    assert failure.value.__context__ is None
    assert "Synthetic provider detail" not in str(failure.value)
    await service.start_workflow(operation)
    calls = client.start_workflow.await_args_list
    assert [call.kwargs["id"] for call in calls] == [workflow_id, workflow_id]
    assert all(
        call.kwargs["id_reuse_policy"] == WorkflowIDReusePolicy.REJECT_DUPLICATE
        for call in calls
    )
    assert (await service.get(operation.id)).attempt == 0


@pytest.mark.anyio
@pytest.mark.parametrize("committed", [False, True])
async def test_workflow_only_continues_for_committed_reconciliation(
    svc_role, monkeypatch, committed
):
    inputs = push_inputs()
    ref = SyncOperationRef(inputs.id, svc_role, "apply")
    failure = ActivityError(
        "Synthetic apply failure",
        scheduled_event_id=1,
        started_event_id=2,
        identity="test-worker",
        activity_type="workspace_sync_apply",
        activity_id="test-apply",
        retry_state=None,
    )
    execute = AsyncMock(side_effect=[failure, committed])
    sleep = AsyncMock()
    continue_as_new = Mock()
    monkeypatch.setattr(workflow, "execute_activity", execute)
    monkeypatch.setattr(workflow, "sleep", sleep)
    monkeypatch.setattr(workflow, "continue_as_new", continue_as_new)
    await WorkspaceSyncWorkflow().run(ref)
    calls = execute.await_args_list
    assert [call.args[0] for call in calls] == [
        "workspace_sync_apply",
        "workspace_sync_fail",
    ]
    if committed:
        continue_as_new.assert_called_once_with(ref)
        sleep.assert_awaited_once_with(timedelta(minutes=5))
    else:
        sleep.assert_not_awaited()
        continue_as_new.assert_not_called()
