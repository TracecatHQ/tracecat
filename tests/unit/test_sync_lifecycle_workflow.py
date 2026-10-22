"""Exercise native Updates, recorded timers, and replay across worker replacement."""

import asyncio
import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from temporalio import activity
from temporalio.client import Client, WorkflowUpdateFailedError
from temporalio.exceptions import ApplicationError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from tracecat.auth.types import Role
from tracecat.background.sandbox import new_sandbox_runner
from tracecat.dsl._converter import get_data_converter
from tracecat.workspace_sync.operations.lifecycle import WorkspaceSyncLifecycleWorkflow
from tracecat.workspace_sync.operations.lifecycle_types import (
    SyncApplyInput,
    SyncCommand,
    SyncLifecycleInput,
    SyncLifecycleSnapshot,
    SyncPreparedRef,
)
from tracecat.workspace_sync.operations.schemas import SyncOperationCreate
from tracecat.workspace_sync.schemas import WorkspaceSyncExportRequest

pytestmark = [pytest.mark.anyio, pytest.mark.temporal]


def start_input() -> SyncLifecycleInput:
    return SyncLifecycleInput(
        SyncOperationCreate(
            id=uuid.uuid4(),
            direction="push",
            push=WorkspaceSyncExportRequest(
                message="Synthetic sync", branch="sync/demo"
            ),
        ),
        Role(
            type="user",
            service_id="tracecat-api",
            user_id=uuid.uuid4(),
            organization_id=uuid.uuid4(),
            workspace_id=uuid.uuid4(),
        ),
        "synthetic-request-hash",
    )


class LifecycleActivities:
    def __init__(
        self,
        *,
        lose_commit_response: bool = False,
        fail_history: bool = False,
        prepare_failures: int = 0,
        terminal_copy_failures: int = 0,
    ) -> None:
        self.terminal_copy_failures = terminal_copy_failures
        self.prepare_failures = prepare_failures
        self.fail_history = fail_history
        self.applies = 0
        self.finishes = 0
        self.committed = False
        self.lose_commit_response = lose_commit_response
        self.copies: list[SyncLifecycleSnapshot] = []

    @activity.defn(name="workspace_sync_initialize")
    async def initialize(self, start: SyncLifecycleInput) -> None:
        pass

    @activity.defn(name="workspace_sync_prepare_lifecycle")
    async def prepare(self, start: SyncLifecycleInput) -> SyncPreparedRef:
        if self.prepare_failures:
            self.prepare_failures -= 1
            raise ApplicationError("Synthetic temporary fetch failure")
        return SyncPreparedRef(
            "synthetic-artifact",
            "a" * 40,
            3,
            [],
            datetime.now(UTC) + timedelta(hours=24),
        )

    @activity.defn(name="workspace_sync_apply_lifecycle")
    async def apply(self, params: SyncApplyInput) -> bool:
        self.applies += 1
        self.committed = True
        if self.lose_commit_response:
            # Simulate retries exhausted after the database committed, without
            # waiting through the real activity backoff in this lifecycle test.
            raise ApplicationError("Synthetic lost acknowledgement", non_retryable=True)
        return False

    @activity.defn(name="workspace_sync_committed_lifecycle")
    async def receipt(self, start: SyncLifecycleInput) -> str:
        return "committed" if self.committed else "uncommitted"

    @activity.defn(name="workspace_sync_finish_lifecycle")
    async def finish(self, start: SyncLifecycleInput) -> None:
        self.finishes += 1

    @activity.defn(name="workspace_sync_project")
    async def project(self, snapshot: SyncLifecycleSnapshot) -> None:
        if self.fail_history and snapshot.state.status != "completed":
            raise ApplicationError("Synthetic history outage", non_retryable=True)
        if snapshot.state.status == "completed" and self.terminal_copy_failures:
            self.terminal_copy_failures -= 1
            raise ApplicationError("Synthetic terminal history outage")
        self.copies.append(snapshot)

    def worker(self, client: Client, queue: str) -> Worker:
        return Worker(
            client,
            task_queue=queue,
            workflows=[WorkspaceSyncLifecycleWorkflow],
            workflow_runner=new_sandbox_runner(),
            # Replay from recorded history instead of reusing an in-memory cache.
            max_cached_workflows=0,
            activities=[
                self.initialize,
                self.prepare,
                self.apply,
                self.receipt,
                self.finish,
                self.project,
            ],
        )


async def ready(handle):
    async with asyncio.timeout(15):
        while True:
            snapshot = await handle.query(WorkspaceSyncLifecycleWorkflow.snapshot)
            if snapshot.state.status == "ready":
                return snapshot
            await asyncio.sleep(0.01)


async def test_confirmation_survives_worker_replacement_and_is_idempotent():
    async with await WorkflowEnvironment.start_time_skipping(
        data_converter=get_data_converter()
    ) as env:
        tasks = LifecycleActivities()
        queue = f"sync-test-{uuid.uuid4()}"
        async with tasks.worker(env.client, queue):
            handle = await env.client.start_workflow(
                WorkspaceSyncLifecycleWorkflow.run,
                start_input(),
                id=str(uuid.uuid4()),
                task_queue=queue,
            )
            preview = await ready(handle)
        # The workflow remains open without its original worker or browser.
        async with tasks.worker(env.client, queue):
            current = await handle.query(WorkspaceSyncLifecycleWorkflow.snapshot)
            assert current.state == preview.state
            wrong = SyncCommand(uuid.uuid4(), 999, current.state.revision)
            with pytest.raises(WorkflowUpdateFailedError):
                await handle.execute_update(WorkspaceSyncLifecycleWorkflow.apply, wrong)
            with pytest.raises(WorkflowUpdateFailedError):
                await handle.execute_update(
                    WorkspaceSyncLifecycleWorkflow.apply,
                    SyncCommand(
                        uuid.uuid4(),
                        current.state.preview_revision,
                        current.state.revision - 1,
                    ),
                )
            command = SyncCommand(
                uuid.uuid4(), current.state.preview_revision, current.state.revision
            )
            accepted = await handle.execute_update(
                WorkspaceSyncLifecycleWorkflow.apply,
                command,
                id=str(command.command_id),
            )
            assert accepted.status == "applying"
            # The same Temporal Update identity returns its original result.
            again = await handle.execute_update(
                WorkspaceSyncLifecycleWorkflow.apply,
                command,
                id=str(command.command_id),
            )
            assert again == accepted
            await handle.result()
            final = await handle.query(WorkspaceSyncLifecycleWorkflow.snapshot)
            assert final.state.status == "completed"
            assert tasks.applies == 1
            assert tasks.finishes == 1


async def test_confirmation_deadline_uses_temporal_timer():
    async with await WorkflowEnvironment.start_time_skipping(
        data_converter=get_data_converter()
    ) as env:
        tasks = LifecycleActivities()
        queue = f"sync-test-{uuid.uuid4()}"
        async with tasks.worker(env.client, queue):
            handle = await env.client.start_workflow(
                WorkspaceSyncLifecycleWorkflow.run,
                start_input(),
                id=str(uuid.uuid4()),
                task_queue=queue,
            )
            await ready(handle)
            # Awaiting the result advances the test server to the recorded timer.
            await handle.result()
            state = (await handle.query(WorkspaceSyncLifecycleWorkflow.snapshot)).state
            assert state.status == "expired"
            assert not tasks.applies
            assert not tasks.finishes


async def test_lost_commit_response_finishes_receipt_without_user_retry():
    async with await WorkflowEnvironment.start_time_skipping(
        data_converter=get_data_converter()
    ) as env:
        tasks = LifecycleActivities(lose_commit_response=True)
        queue = f"sync-test-{uuid.uuid4()}"
        async with tasks.worker(env.client, queue):
            handle = await env.client.start_workflow(
                WorkspaceSyncLifecycleWorkflow.run,
                start_input(),
                id=str(uuid.uuid4()),
                task_queue=queue,
            )
            state = (await ready(handle)).state
            await handle.execute_update(
                WorkspaceSyncLifecycleWorkflow.apply,
                SyncCommand(uuid.uuid4(), state.preview_revision, state.revision),
            )
            await handle.result()
            assert (
                await handle.query(WorkspaceSyncLifecycleWorkflow.snapshot)
            ).state.status == "completed"
            assert tasks.applies == 1
            assert tasks.finishes == 1
            assert not any(s.state.can_retry for s in tasks.copies)


async def test_delayed_history_never_blocks_confirmation():
    async with await WorkflowEnvironment.start_time_skipping(
        data_converter=get_data_converter()
    ) as env:
        tasks = LifecycleActivities(fail_history=True, terminal_copy_failures=3)
        queue = f"sync-test-{uuid.uuid4()}"
        async with tasks.worker(env.client, queue):
            handle = await env.client.start_workflow(
                WorkspaceSyncLifecycleWorkflow.run,
                start_input(),
                id=str(uuid.uuid4()),
                task_queue=queue,
            )
            state = (await ready(handle)).state
            await handle.execute_update(
                WorkspaceSyncLifecycleWorkflow.apply,
                SyncCommand(uuid.uuid4(), state.preview_revision, state.revision),
            )
            await handle.result()
            assert (
                await handle.query(WorkspaceSyncLifecycleWorkflow.snapshot)
            ).state.status == "completed"
            assert tasks.applies == 1
            assert tasks.copies[-1].state.status == "completed"


async def test_retry_requires_current_failure_revision_and_stays_in_same_execution():
    async with await WorkflowEnvironment.start_time_skipping(
        data_converter=get_data_converter()
    ) as env:
        tasks = LifecycleActivities(prepare_failures=5)
        queue = f"sync-test-{uuid.uuid4()}"
        async with tasks.worker(env.client, queue):
            handle = await env.client.start_workflow(
                WorkspaceSyncLifecycleWorkflow.run,
                start_input(),
                id=str(uuid.uuid4()),
                task_queue=queue,
            )
            await env.sleep(timedelta(minutes=2))
            async with asyncio.timeout(15):
                while True:
                    failed = (
                        await handle.query(WorkspaceSyncLifecycleWorkflow.snapshot)
                    ).state
                    if failed.status == "failed":
                        break
                    await asyncio.sleep(0.01)
            assert failed.can_retry
            with pytest.raises(WorkflowUpdateFailedError):
                await handle.execute_update(
                    WorkspaceSyncLifecycleWorkflow.retry,
                    SyncCommand(uuid.uuid4(), 0, failed.revision - 1),
                )
            command = SyncCommand(uuid.uuid4(), 0, failed.revision)
            await handle.execute_update(
                WorkspaceSyncLifecycleWorkflow.retry,
                command,
                id=str(command.command_id),
            )
            state = (await ready(handle)).state
            await handle.execute_update(
                WorkspaceSyncLifecycleWorkflow.apply,
                SyncCommand(uuid.uuid4(), state.preview_revision, state.revision),
            )
            await handle.result()
            assert tasks.applies == 1


async def test_lifecycle_updates_on_configured_local_temporal_server():
    # Exercise the actual supported server, not just the SDK test server.
    client = await Client.connect(
        f"localhost:{os.environ.get('TEMPORAL_PORT', '7233')}",
        data_converter=get_data_converter(),
    )
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
