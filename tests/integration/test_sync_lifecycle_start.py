"""Start-first acceptance and history projection on the configured Temporal server."""

import asyncio
import uuid

import pytest
from temporalio.worker import Worker

from tracecat import config
from tracecat.auth.types import Role
from tracecat.background.sandbox import new_sandbox_runner
from tracecat.dsl.client import get_temporal_client
from tracecat.workspace_sync.operations.lifecycle import WorkspaceSyncLifecycleWorkflow
from tracecat.workspace_sync.operations.lifecycle_activities import (
    workspace_sync_apply_lifecycle,
    workspace_sync_committed_lifecycle,
    workspace_sync_finish_lifecycle,
    workspace_sync_initialize,
    workspace_sync_prepare_lifecycle,
    workspace_sync_project,
)
from tracecat.workspace_sync.operations.lifecycle_client import SyncLifecycleService
from tracecat.workspace_sync.operations.schemas import SyncOperationCreate
from tracecat.workspace_sync.operations.service import SyncOperationService
from tracecat.workspace_sync.schemas import WorkspaceSyncExportRequest


@pytest.mark.temporal
@pytest.mark.anyio
async def test_temporal_accepts_before_history_and_worker_records_revoked_access(
    svc_role: Role, monkeypatch
):
    # No User row exists for this synthetic actor, so real authorization rejects
    # preparation before any Git fetch. No third-party credentials are needed.
    queue = f"sync-start-first-{uuid.uuid4()}"
    monkeypatch.setattr(config, "TRACECAT__BACKGROUND_QUEUE", queue)
    inputs = SyncOperationCreate(
        id=uuid.uuid4(),
        direction="push",
        push=WorkspaceSyncExportRequest(message="Synthetic sync", branch="sync/test"),
    )
    async with SyncLifecycleService.with_session(svc_role) as service:
        accepted = await service.create(inputs)
        assert accepted.status == "queued"
        assert await service._history(inputs.id) is None
    client = await get_temporal_client()
    async with Worker(
        client,
        task_queue=queue,
        workflows=[WorkspaceSyncLifecycleWorkflow],
        workflow_runner=new_sandbox_runner(),
        activities=[
            workspace_sync_initialize,
            workspace_sync_prepare_lifecycle,
            workspace_sync_apply_lifecycle,
            workspace_sync_committed_lifecycle,
            workspace_sync_finish_lifecycle,
            workspace_sync_project,
        ],
    ):
        handle = client.get_workflow_handle_for(
            WorkspaceSyncLifecycleWorkflow.run,
            f"workspace-sync/v2/{svc_role.workspace_id}/{inputs.id}",
        )
        await asyncio.wait_for(handle.result(), timeout=30)
        async with SyncLifecycleService.with_session(svc_role) as service:
            response = await service.get(inputs.id)
            assert response.status == "failed" and not response.can_retry
            assert (
                response.error
                == "Access changed. Restore access and start a fresh preview."
            )
        async with SyncOperationService.with_session(svc_role) as history:
            row = await history.get(inputs.id)
            assert row.status == "failed"
            assert (row.summary or {})["lifecycle_version"] == 2
