"""Reconcile the semantic-search schedule whenever a DSL worker starts."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from datetime import timedelta

from temporalio.client import (
    Client,
    Schedule,
    ScheduleActionStartWorkflow,
    ScheduleAlreadyRunningError,
    ScheduleIntervalSpec,
    ScheduleOverlapPolicy,
    SchedulePolicy,
    ScheduleSpec,
    ScheduleUpdate,
    ScheduleUpdateInput,
)

from tracecat.logger import logger
from tracecat.search.indexing_workflow import SearchIndexDispatcher

SCHEDULE_ID = "semantic-search-dispatch-v1"
_RECONCILE_TIMEOUT_SECONDS = 30
_RETRY_DELAY_SECONDS = 30


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

        await client.get_schedule_handle(SCHEDULE_ID).update(update)


async def _reconcile_search_schedule(client: Client, task_queue: str) -> None:
    """Retry independently of DSL polling; never log raw RPC error contents."""
    while True:
        try:
            async with asyncio.timeout(_RECONCILE_TIMEOUT_SECONDS):
                await ensure_search_schedule(client, task_queue)
            return
        except Exception as exc:
            logger.warning(
                "Search schedule reconciliation failed; retrying in 30 seconds",
                error_type=type(exc).__name__,
            )
        await asyncio.sleep(_RETRY_DELAY_SECONDS)


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
