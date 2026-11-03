"""The case trigger dispatcher drains the outbox, retries, and replays cleanly."""

import asyncio
import uuid
from collections.abc import AsyncGenerator
from datetime import timedelta

import pytest
from temporalio import activity
from temporalio.client import WorkflowExecutionStatus
from temporalio.exceptions import ApplicationError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Replayer, Worker

from tracecat.background.sandbox import new_sandbox_runner
from tracecat.cases.triggers.dispatcher import (
    CASE_TRIGGER_DISPATCHER_ID,
    CaseTriggerDispatcher,
    ensure_case_trigger_dispatcher,
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
    task_queue = f"case-trigger-dispatcher-{uuid.uuid4()}"
    waits: list[None] = []
    batches: list[None] = []
    drained = asyncio.Event()

    @activity.defn(name="wait_for_case_trigger_outbox")
    async def wait_for_rows() -> bool:
        waits.append(None)
        # Rows on the first two polls, then an idle poll.
        return len(waits) <= 2

    @activity.defn(name="dispatch_case_trigger_outbox_batch")
    async def dispatch_batch() -> int:
        batches.append(None)
        if len(batches) == 1:
            raise ApplicationError("Temporal start failed", type="Transient")
        if len(batches) == 3:
            drained.set()
        return 1

    async with Worker(
        env.client,
        task_queue=task_queue,
        workflows=[CaseTriggerDispatcher],
        activities=[wait_for_rows, dispatch_batch],
        workflow_runner=new_sandbox_runner(),
    ):
        await ensure_case_trigger_dispatcher(env.client, task_queue)
        handle = env.client.get_workflow_handle(CASE_TRIGGER_DISPATCHER_ID)
        first_run = (await handle.describe()).run_id

        # Every replica starts the dispatcher on boot; the extra start reuses it.
        await ensure_case_trigger_dispatcher(env.client, task_queue)
        assert (await handle.describe()).run_id == first_run

        await asyncio.wait_for(drained.wait(), timeout=30)
        # The failed batch was retried, then the second poll's batch ran.
        assert len(batches) == 3
        await handle.terminate(reason="test complete")

    description = await handle.describe()
    assert description.status == WorkflowExecutionStatus.TERMINATED
    history = await handle.fetch_history()
    result = await Replayer(
        workflows=[CaseTriggerDispatcher],
        workflow_runner=new_sandbox_runner(),
    ).replay_workflow(history, raise_on_replay_failure=False)
    assert result.replay_failure is None


@pytest.mark.anyio
async def test_dispatcher_restarts_after_it_stops(env: WorkflowEnvironment) -> None:
    """A dispatcher that stopped is started again by the next boot check."""
    task_queue = f"case-trigger-dispatcher-{uuid.uuid4()}"

    @activity.defn(name="wait_for_case_trigger_outbox")
    async def wait_for_rows() -> bool:
        await asyncio.sleep(0.1)
        return False

    @activity.defn(name="dispatch_case_trigger_outbox_batch")
    async def dispatch_batch() -> int:
        return 0

    async with Worker(
        env.client,
        task_queue=task_queue,
        workflows=[CaseTriggerDispatcher],
        activities=[wait_for_rows, dispatch_batch],
        workflow_runner=new_sandbox_runner(),
    ):
        await ensure_case_trigger_dispatcher(env.client, task_queue)
        handle = env.client.get_workflow_handle(CASE_TRIGGER_DISPATCHER_ID)
        first_run = (await handle.describe()).run_id
        await handle.terminate(reason="operator stop")

        await ensure_case_trigger_dispatcher(env.client, task_queue)
        restarted = await handle.describe()
        assert restarted.run_id != first_run
        assert restarted.status == WorkflowExecutionStatus.RUNNING
        await asyncio.sleep(timedelta(milliseconds=300).total_seconds())
        await handle.terminate(reason="test complete")


@pytest.mark.anyio
async def test_dispatcher_on_a_previous_queue_is_replaced(
    env: WorkflowEnvironment,
) -> None:
    """A run left on a queue without workers is moved to the current queue."""
    old_queue = f"case-trigger-dispatcher-old-{uuid.uuid4()}"
    new_queue = f"case-trigger-dispatcher-{uuid.uuid4()}"

    @activity.defn(name="wait_for_case_trigger_outbox")
    async def wait_for_rows() -> bool:
        await asyncio.sleep(0.1)
        return False

    @activity.defn(name="dispatch_case_trigger_outbox_batch")
    async def dispatch_batch() -> int:
        return 0

    # Nothing polls the old queue, as after a queue rename.
    await ensure_case_trigger_dispatcher(env.client, old_queue)
    handle = env.client.get_workflow_handle(CASE_TRIGGER_DISPATCHER_ID)
    stranded = await handle.describe()
    assert stranded.task_queue == old_queue

    async with Worker(
        env.client,
        task_queue=new_queue,
        workflows=[CaseTriggerDispatcher],
        activities=[wait_for_rows, dispatch_batch],
        workflow_runner=new_sandbox_runner(),
    ):
        await ensure_case_trigger_dispatcher(env.client, new_queue)
        replaced = await handle.describe()
        assert replaced.run_id != stranded.run_id
        assert replaced.task_queue == new_queue
        assert replaced.status == WorkflowExecutionStatus.RUNNING
        await handle.terminate(reason="test complete")
