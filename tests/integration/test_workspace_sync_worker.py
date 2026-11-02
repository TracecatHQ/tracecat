"""Exercise durable background orchestration across real Temporal worker restarts."""

import asyncio
import os
import uuid
from datetime import timedelta

import pytest
from temporalio import activity
from temporalio.client import Client
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.exceptions import ApplicationError
from temporalio.worker import Worker

from tracecat import config
from tracecat.auth.types import Role
from tracecat.background.sandbox import new_sandbox_runner
from tracecat.dsl.client import get_temporal_client
from tracecat.workspace_sync.operations.activities import (
    workspace_sync_apply,
    workspace_sync_fail,
    workspace_sync_prepare,
)
from tracecat.workspace_sync.operations.schemas import SyncOperationCreate
from tracecat.workspace_sync.operations.service import SyncOperationService
from tracecat.workspace_sync.operations.types import SyncOperationRef
from tracecat.workspace_sync.operations.workflows import WorkspaceSyncWorkflow
from tracecat.workspace_sync.schemas import WorkspaceSyncExportRequest


class RestartActivities:
    """A retry boundary representing an interrupted network fetch."""

    def __init__(self) -> None:
        self.interrupted = asyncio.Event()
        self.attempts: list[int] = []

    @activity.defn(name="workspace_sync_prepare")
    async def prepare(self, ref: SyncOperationRef) -> None:
        self.attempts.append(activity.info().attempt)
        assert ref.phase == "preview"
        activity.heartbeat()
        if activity.info().attempt == 1:
            self.interrupted.set()
            raise ApplicationError("Synthetic transient fetch failure")


@pytest.mark.temporal
@pytest.mark.anyio
async def test_sync_workflow_continues_on_replacement_worker() -> None:
    client = await Client.connect(
        f"localhost:{os.environ.get('TEMPORAL_PORT', '7233')}",
        data_converter=pydantic_data_converter,
    )
    queue = f"test-background-{uuid.uuid4()}"
    activities = RestartActivities()
    ref = SyncOperationRef(
        uuid.uuid4(),
        Role(type="user", service_id="tracecat-api", user_id=uuid.uuid4()),
        "preview",
    )
    async with Worker(
        client,
        task_queue=queue,
        workflows=[WorkspaceSyncWorkflow],
        workflow_runner=new_sandbox_runner(),
        activities=[activities.prepare],
        graceful_shutdown_timeout=timedelta(seconds=1),
    ):
        handle = await client.start_workflow(
            WorkspaceSyncWorkflow.run,
            ref,
            id=f"test-sync-{uuid.uuid4()}",
            task_queue=queue,
        )
        await asyncio.wait_for(activities.interrupted.wait(), timeout=30)
    # No original process or browser is needed when a replacement starts polling.
    async with Worker(
        client,
        task_queue=queue,
        workflows=[WorkspaceSyncWorkflow],
        workflow_runner=new_sandbox_runner(),
        activities=[activities.prepare],
    ):
        await asyncio.wait_for(handle.result(), timeout=30)
    assert activities.attempts == [1, 2]


@pytest.mark.temporal
@pytest.mark.anyio
async def test_direct_start_persists_revoked_access(svc_role: Role, monkeypatch):
    # The fixture intentionally has no User row, representing an actor removed
    # after the API accepted the operation. No Git service is required.

    queue = f"test-background-direct-{uuid.uuid4()}"
    monkeypatch.setattr(config, "TRACECAT__BACKGROUND_QUEUE", queue)
    async with SyncOperationService.with_session(svc_role) as service:
        operation = await service.create(
            SyncOperationCreate(
                id=uuid.uuid4(),
                direction="push",
                push=WorkspaceSyncExportRequest(
                    message="Synthetic sync", branch="sync/test"
                ),
            )
        )
        operation_id = operation.id
        assert operation.status == "queued"
    # API mutations start the workflow directly. Repeating a lost start response
    # targets the same workflow ID even before a worker is polling the queue.
    async with SyncOperationService.with_session(svc_role) as service:
        operation = await service.get(operation_id)
        await service.start_workflow(operation)
        await service.start_workflow(operation)
    client = await get_temporal_client()
    async with Worker(
        client,
        task_queue=queue,
        workflows=[WorkspaceSyncWorkflow],
        workflow_runner=new_sandbox_runner(),
        activities=[workspace_sync_prepare, workspace_sync_apply, workspace_sync_fail],
    ):
        async with asyncio.timeout(30):
            while True:
                async with SyncOperationService.with_session(svc_role) as service:
                    operation = await service.get(operation_id)
                    if operation.status == "failed":
                        assert not service.read(operation).can_retry
                        assert (
                            operation.error
                            == "Access changed. Restore access and start a fresh preview."
                        )
                        break
                await asyncio.sleep(0.2)
