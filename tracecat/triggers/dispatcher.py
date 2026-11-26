"""Singleton Temporal workflow that drains the trigger event outbox.

Hosted like the case trigger dispatcher: each background-worker replica starts
it on boot with ``USE_EXISTING``, so one run serves the deployment. The run
long-polls the outbox in an activity, dispatches a batch while rows are ready,
and continues as new when Temporal suggests it. A worker lost mid-batch costs
only a lease: its claimed rows become ready again after 60 seconds.
"""

import asyncio
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from datetime import timedelta

from temporalio import activity, workflow
from temporalio.client import Client
from temporalio.common import RetryPolicy, WorkflowIDConflictPolicy

with workflow.unsafe.imports_passed_through():
    from tracecat.db.engine import get_async_session_bypass_rls_context_manager
    from tracecat.logger import logger
    from tracecat.triggers.dispatch import (
        TriggerOutboxDispatcher,
        read_backlog,
        record_backlog,
    )

TRIGGER_EVENT_DISPATCHER_ID = "trigger-event-dispatcher"
_POLL_INTERVAL_SECONDS = 1.0
_IDLE_RETURN_SECONDS = 300.0
_STOPPED_COUNT_INTERVAL_SECONDS = 60.0
_RECONCILE_TIMEOUT_SECONDS = 30
_RETRY_DELAY_SECONDS = 30
_RECONCILE_INTERVAL_SECONDS = 600
# Retry forever: a stopped dispatcher stops every trigger. Failing rows do not
# fail a batch, so this covers the batch itself failing (a database outage, a
# lost worker); the cap keeps recovery within seconds.
_ACTIVITY_RETRY_POLICY = RetryPolicy(
    initial_interval=timedelta(seconds=1),
    maximum_interval=timedelta(seconds=30),
)


@activity.defn
async def wait_for_trigger_events() -> bool:
    """Return True as soon as a row is ready, or False after 5 idle minutes.

    Each poll also records the oldest ready row's wait, and once a minute the
    stopped rows, so the alert inputs stay current while the outbox is idle.
    """
    deadline = time.monotonic() + _IDLE_RETURN_SECONDS
    stopped_counted_at: float | None = None
    while True:
        now = time.monotonic()
        count_stopped = (
            stopped_counted_at is None
            or now - stopped_counted_at >= _STOPPED_COUNT_INTERVAL_SECONDS
        )
        async with get_async_session_bypass_rls_context_manager() as session:
            backlog = await read_backlog(session, count_stopped=count_stopped)
        if count_stopped:
            stopped_counted_at = now
        record_backlog(backlog)
        if backlog.has_ready_rows:
            return True
        if time.monotonic() >= deadline:
            return False
        activity.heartbeat()
        await asyncio.sleep(_POLL_INTERVAL_SECONDS)


@activity.defn
async def dispatch_trigger_event_batch() -> int:
    """Dispatch the next batch and return how many rows it claimed.

    A row whose starts fail is left with its error and a backoff; it does not
    fail the activity, so retry backoff never delays healthy rows.
    """
    dispatcher = TriggerOutboxDispatcher(heartbeat=activity.heartbeat)
    result = await dispatcher.dispatch_batch()
    if result.claimed:
        logger.info(
            "Dispatched trigger events",
            picked=result.picked,
            claimed=result.claimed,
            delivered=result.delivered,
            retrying=result.retrying,
            stopped=result.stopped,
        )
    return result.claimed


@workflow.defn
class TriggerEventDispatcher:
    """Start the runs that committed trigger outbox rows wait for."""

    @workflow.run
    async def run(self) -> None:
        while not workflow.info().is_continue_as_new_suggested():
            has_rows = await workflow.execute_activity(
                wait_for_trigger_events,
                start_to_close_timeout=timedelta(minutes=6),
                heartbeat_timeout=timedelta(seconds=60),
                retry_policy=_ACTIVITY_RETRY_POLICY,
            )
            if has_rows:
                # A batch is bounded by its starts (at most 30 s each, 16 at
                # once); heartbeats before every start detect a dead worker.
                await workflow.execute_activity(
                    dispatch_trigger_event_batch,
                    start_to_close_timeout=timedelta(minutes=30),
                    heartbeat_timeout=timedelta(seconds=60),
                    retry_policy=_ACTIVITY_RETRY_POLICY,
                )
        workflow.continue_as_new()


async def ensure_trigger_event_dispatcher(client: Client, task_queue: str) -> None:
    """Start the dispatcher unless it is already running. Safe on every replica.

    A run left on a queue that no longer has workers would never make
    progress, so it is replaced. Dispatch is idempotent: the new run picks up
    every pending row.
    """
    handle = await client.start_workflow(
        TriggerEventDispatcher.run,
        id=TRIGGER_EVENT_DISPATCHER_ID,
        task_queue=task_queue,
        id_conflict_policy=WorkflowIDConflictPolicy.USE_EXISTING,
    )
    description = await handle.describe()
    if description.task_queue == task_queue:
        return
    await handle.terminate(
        reason="Trigger event dispatcher moved to another task queue"
    )
    logger.info("Replaced the trigger event dispatcher on a previous task queue")
    await client.start_workflow(
        TriggerEventDispatcher.run,
        id=TRIGGER_EVENT_DISPATCHER_ID,
        task_queue=task_queue,
        id_conflict_policy=WorkflowIDConflictPolicy.USE_EXISTING,
    )


async def _keep_trigger_event_dispatcher_running(
    client: Client, task_queue: str
) -> None:
    """Re-check periodically, so a dispatcher that stopped is started again."""
    while True:
        delay = _RECONCILE_INTERVAL_SECONDS
        try:
            async with asyncio.timeout(_RECONCILE_TIMEOUT_SECONDS):
                await ensure_trigger_event_dispatcher(client, task_queue)
        except Exception as exc:
            delay = _RETRY_DELAY_SECONDS
            logger.warning(
                "Failed to start the trigger event dispatcher; retrying in 30 seconds",
                error_type=type(exc).__name__,
            )
        await asyncio.sleep(delay)


@asynccontextmanager
async def trigger_event_dispatcher_lifespan(
    client: Client, task_queue: str
) -> AsyncIterator[None]:
    """Keep the dispatcher running without delaying worker startup or shutdown."""
    task = asyncio.create_task(
        _keep_trigger_event_dispatcher_running(client, task_queue)
    )
    try:
        yield
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
