"""The trigger event dispatcher drains the outbox, retries, and replays cleanly."""

import asyncio
import uuid
from collections.abc import AsyncGenerator

import pytest
from temporalio import activity
from temporalio.client import WorkflowExecutionStatus
from temporalio.exceptions import ApplicationError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Replayer, Worker

from tracecat.background.sandbox import new_sandbox_runner
from tracecat.triggers.dispatcher import (
    TRIGGER_EVENT_DISPATCHER_ID,
    TriggerEventDispatcher,
    ensure_trigger_event_dispatcher,
)

pytestmark = [pytest.mark.temporal, pytest.mark.integration]


@pytest.fixture
async def env() -> AsyncGenerator[WorkflowEnvironment, None]:
    async with await WorkflowEnvironment.start_local(
        dev_server_log_level="error"
    ) as environment:
        yield environment


@pytest.mark.anyio
async def test_dispatcher_retries_failed_batches_and_replays(
    env: WorkflowEnvironment,
) -> None:
    """A failed batch is retried by Temporal; the recorded history replays."""
    task_queue = f"trigger-event-dispatcher-{uuid.uuid4()}"
    waits: list[None] = []
    batches: list[None] = []
    drained = asyncio.Event()

    @activity.defn(name="wait_for_trigger_events")
    async def wait_for_rows() -> bool:
        waits.append(None)
        # Rows on the first two polls, then idle polls.
        return len(waits) <= 2

    @activity.defn(name="dispatch_trigger_event_batch")
    async def dispatch_batch() -> int:
        batches.append(None)
        if len(batches) == 1:
            raise ApplicationError("Database unavailable", type="Transient")
        if len(batches) == 3:
            drained.set()
        return 1

    async with Worker(
        env.client,
        task_queue=task_queue,
        workflows=[TriggerEventDispatcher],
        activities=[wait_for_rows, dispatch_batch],
        workflow_runner=new_sandbox_runner(),
    ):
        await ensure_trigger_event_dispatcher(env.client, task_queue)
        handle = env.client.get_workflow_handle(TRIGGER_EVENT_DISPATCHER_ID)
        first_run = (await handle.describe()).run_id

        # Every replica starts the dispatcher on boot; the extra start reuses it.
        await ensure_trigger_event_dispatcher(env.client, task_queue)
        assert (await handle.describe()).run_id == first_run

        await asyncio.wait_for(drained.wait(), timeout=30)
        assert len(batches) == 3
        await handle.terminate(reason="test complete")

    description = await handle.describe()
    assert description.status == WorkflowExecutionStatus.TERMINATED
    history = await handle.fetch_history()
    result = await Replayer(
        workflows=[TriggerEventDispatcher],
        workflow_runner=new_sandbox_runner(),
    ).replay_workflow(history, raise_on_replay_failure=False)
    assert result.replay_failure is None
