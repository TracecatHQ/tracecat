"""History copies cannot authorize work or override immutable write receipts."""

from datetime import UTC, datetime, timedelta

import pytest
from temporalio.exceptions import ApplicationError
from temporalio.testing import ActivityEnvironment

from tests.unit.test_durable_workspace_sync import push_inputs
from tracecat.workspace_sync.operations.lifecycle_activities import (
    workspace_sync_apply_lifecycle,
    workspace_sync_committed_lifecycle,
    workspace_sync_finish_lifecycle,
    workspace_sync_initialize,
    workspace_sync_project,
)
from tracecat.workspace_sync.operations.lifecycle_types import (
    SyncApplyInput,
    SyncLifecycleInput,
    SyncLifecycleSnapshot,
    SyncLifecycleState,
    SyncPreparedRef,
)
from tracecat.workspace_sync.operations.service import SyncOperationService


@pytest.mark.anyio
async def test_write_receipt_survives_delayed_history_status(svc_role):
    start = SyncLifecycleInput(push_inputs(), svc_role, "synthetic-hash")
    env = ActivityEnvironment()
    await env.run(workspace_sync_initialize, start)
    async with SyncOperationService.with_session(svc_role) as service:
        row = await service.get(start.inputs.id)
        row.status = "failed"  # A history copy must never rerun a committed write.
        row.result = {"commit": {"sha": "a" * 40}}
        await service.session.commit()
    preview = SyncPreparedRef("unused", "a" * 40, 0, [], datetime.now(UTC))
    assert not await env.run(
        workspace_sync_apply_lifecycle,
        SyncApplyInput(start, preview),
    )


@pytest.mark.anyio
async def test_old_history_publication_cannot_replace_new_state(svc_role):
    start = SyncLifecycleInput(push_inputs(), svc_role, "synthetic-hash")
    env = ActivityEnvironment()
    await env.run(workspace_sync_initialize, start)
    now = datetime.now(UTC)
    completed = SyncLifecycleState(
        "completed", "finished", 10, now, now + timedelta(hours=24)
    )
    old = SyncLifecycleState(
        "ready", "awaiting_confirmation", 9, now, now + timedelta(hours=24)
    )
    await env.run(workspace_sync_project, SyncLifecycleSnapshot(start, completed))
    await env.run(workspace_sync_project, SyncLifecycleSnapshot(start, old))
    async with SyncOperationService.with_session(svc_role) as service:
        row = await service.get(start.inputs.id)
        assert row.status == "completed"
        assert row.summary is not None
        assert row.summary["lifecycle_revision"] == 10


@pytest.mark.anyio
async def test_start_cannot_adopt_a_legacy_operation(svc_role):
    inputs = push_inputs()
    async with SyncOperationService.with_session(svc_role) as service:
        await service.create(inputs)
    with pytest.raises(ApplicationError) as failure:
        await ActivityEnvironment().run(
            workspace_sync_initialize,
            SyncLifecycleInput(inputs, svc_role, "synthetic-hash"),
        )
    assert failure.value.non_retryable


@pytest.mark.anyio
async def test_deleted_operation_does_not_retry_receipt_check_forever(svc_role):
    start = SyncLifecycleInput(push_inputs(), svc_role, "synthetic-hash")
    env = ActivityEnvironment()
    await env.run(workspace_sync_initialize, start)
    async with SyncOperationService.with_session(svc_role) as service:
        row = await service.get(start.inputs.id)
        await service.session.delete(row)
        await service.session.commit()
    assert await env.run(workspace_sync_committed_lifecycle, start) == "missing"
    await env.run(workspace_sync_finish_lifecycle, start)
