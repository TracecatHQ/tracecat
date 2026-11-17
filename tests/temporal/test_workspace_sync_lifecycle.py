"""Compatibility check on the configured Temporal server used by deployed workers."""

import asyncio
import uuid

import pytest

from tests.unit.test_sync_lifecycle_workflow import (
    LifecycleActivities,
    ready,
    start_input,
)
from tracecat.dsl.client import get_temporal_client
from tracecat.workspace_sync.operations.lifecycle import WorkspaceSyncLifecycleWorkflow
from tracecat.workspace_sync.operations.lifecycle_types import SyncCommand

pytestmark = [pytest.mark.anyio, pytest.mark.temporal]


async def test_lifecycle_updates_on_configured_local_temporal_server():
    # Exercise the actual supported server, not just the SDK test server.
    client = await get_temporal_client()
    tasks = LifecycleActivities()
    queue = f"sync-compatibility-{uuid.uuid4()}"
    async with tasks.worker(client, queue):
        handle = await client.start_workflow(
            WorkspaceSyncLifecycleWorkflow.run,
            start_input(),
            id=str(uuid.uuid4()),
            task_queue=queue,
        )
        try:
            state = (await ready(handle)).state
            await handle.execute_update(
                WorkspaceSyncLifecycleWorkflow.apply,
                SyncCommand(uuid.uuid4(), state.preview_revision, state.revision),
            )
            await asyncio.wait_for(handle.result(), timeout=30)
            final = await handle.query(WorkspaceSyncLifecycleWorkflow.snapshot)
            assert final.state.status == "completed"
            assert tasks.applies == tasks.finishes == 1
        finally:
            if not tasks.finishes:
                await handle.terminate("Synthetic local compatibility test cleanup")
