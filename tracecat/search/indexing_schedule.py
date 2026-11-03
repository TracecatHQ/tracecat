"""Keep the semantic-search schedule pointed at the background worker's queue."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from datetime import timedelta

from temporalio.client import (
    Client,
    Schedule,
    ScheduleActionExecutionStartWorkflow,
    ScheduleActionStartWorkflow,
    ScheduleAlreadyRunningError,
    ScheduleIntervalSpec,
    ScheduleOverlapPolicy,
    SchedulePolicy,
    ScheduleSpec,
    ScheduleUpdate,
    ScheduleUpdateInput,
    WorkflowExecutionStatus,
)

from tracecat.logger import logger
from tracecat.search.indexing_workflow import SearchIndexDispatcher

SCHEDULE_ID = "semantic-search-dispatch-v1"
_RECONCILE_TIMEOUT_SECONDS = 30
_RETRY_DELAY_SECONDS = 30
_RECONCILE_INTERVAL_SECONDS = 600


async def ensure_search_schedule(client: Client, task_queue: str) -> None:
    """Idempotent across replicas; preserve an operator's schedule pause."""
    schedule = Schedule(
        action=ScheduleActionStartWorkflow(
            SearchIndexDispatcher.run,
            None,
            id="semantic-search-dispatch",
            task_queue=task_queue,
        ),
        spec=ScheduleSpec(
            intervals=[ScheduleIntervalSpec(every=timedelta(seconds=10))]
        ),
        policy=SchedulePolicy(overlap=ScheduleOverlapPolicy.SKIP),
    )
    try:
        await client.create_schedule(SCHEDULE_ID, schedule)
    except ScheduleAlreadyRunningError:

        def update(current: ScheduleUpdateInput) -> ScheduleUpdate:
            schedule.state = current.description.schedule.state
            return ScheduleUpdate(schedule=schedule)

        handle = client.get_schedule_handle(SCHEDULE_ID)
        await handle.update(update)
        await _terminate_runs_on_other_queues(client, task_queue)


async def _terminate_runs_on_other_queues(client: Client, task_queue: str) -> None:
    """A run left on a queue that no longer hosts the dispatcher never finishes.

    The SKIP overlap policy would then block every later run. Dispatch is
    idempotent: the next run rediscovers all pending work.
    """
    description = await client.get_schedule_handle(SCHEDULE_ID).describe()
    for action in description.info.running_actions:
        if not isinstance(action, ScheduleActionExecutionStartWorkflow):
            continue
        run = client.get_workflow_handle(action.workflow_id)
        execution = await run.describe()
        if (
            execution.status == WorkflowExecutionStatus.RUNNING
            and execution.task_queue != task_queue
        ):
            await run.terminate(reason="Search dispatcher moved to another task queue")
            logger.info("Terminated search dispatcher run on a previous task queue")


async def _reconcile_search_schedule(client: Client, task_queue: str) -> None:
    """Reconcile periodically, so a restarting older worker cannot retarget it for long.

    Never log raw RPC error contents.
    """
    while True:
        delay = _RECONCILE_INTERVAL_SECONDS
        try:
            async with asyncio.timeout(_RECONCILE_TIMEOUT_SECONDS):
                await ensure_search_schedule(client, task_queue)
        except Exception as exc:
            delay = _RETRY_DELAY_SECONDS
            logger.warning(
                "Search schedule reconciliation failed; retrying in 30 seconds",
                error_type=type(exc).__name__,
            )
        await asyncio.sleep(delay)


@asynccontextmanager
async def search_schedule_lifespan(
    client: Client, task_queue: str
) -> AsyncIterator[None]:
    """Reconcile in the background without delaying worker startup or shutdown."""
    task = asyncio.create_task(_reconcile_search_schedule(client, task_queue))
    try:
        yield
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
