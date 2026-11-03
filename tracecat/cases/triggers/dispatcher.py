"""Singleton Temporal workflow that drains the case trigger outbox.

Each background-worker replica starts the dispatcher on boot with
``USE_EXISTING``, so one run serves the whole deployment. The run long-polls
the outbox in an activity, dispatches a batch when rows exist, and continues
as new when Temporal suggests it.
"""

import asyncio
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from datetime import timedelta

from temporalio import activity, workflow
from temporalio.client import Client
from temporalio.common import RetryPolicy, WorkflowIDConflictPolicy
from temporalio.exceptions import ApplicationError

with workflow.unsafe.imports_passed_through():
    from tracecat.cases.triggers.dispatch import (
        CaseTriggerOutboxDispatcher,
        list_outbox_row_ids,
        outbox_has_rows,
    )
    from tracecat.logger import logger

CASE_TRIGGER_DISPATCHER_ID = "case-trigger-dispatcher"
_POLL_INTERVAL_SECONDS = 1.0
_IDLE_RETURN_SECONDS = 300.0
_RECONCILE_TIMEOUT_SECONDS = 30
_RETRY_DELAY_SECONDS = 30
_RECONCILE_INTERVAL_SECONDS = 600
# Retry forever: a stopped dispatcher stops every case trigger. Cap the backoff
# so a row that keeps failing delays the rest of its batch by seconds, not minutes.
_ACTIVITY_RETRY_POLICY = RetryPolicy(
    initial_interval=timedelta(seconds=1),
    maximum_interval=timedelta(seconds=30),
)


@activity.defn
async def wait_for_case_trigger_outbox() -> bool:
    """Return True as soon as outbox rows exist, or False after 5 idle minutes.

    Waiting inside one heartbeating activity keeps the poll out of workflow
    history; Temporal retries it only if the worker dies.
    """
    deadline = time.monotonic() + _IDLE_RETURN_SECONDS
    while True:
        if await outbox_has_rows():
            return True
        if time.monotonic() >= deadline:
            return False
        activity.heartbeat()
        await asyncio.sleep(_POLL_INTERVAL_SECONDS)


@activity.defn
async def dispatch_case_trigger_outbox_batch() -> int:
    """Dispatch the oldest outbox rows and return how many were handled.

    Each row commits on its own, so one failing row does not hold back the
    rest. Any failure fails the activity afterwards, and the retry picks up the
    rows that are still pending.
    """
    dispatcher = CaseTriggerOutboxDispatcher(heartbeat=activity.heartbeat)
    row_ids = await list_outbox_row_ids()
    failed = 0
    for row_id in row_ids:
        activity.heartbeat()
        try:
            await dispatcher.dispatch_row(row_id)
        except Exception as e:
            failed += 1
            logger.warning(
                "Failed to dispatch case trigger outbox row",
                outbox_row=row_id,
                error_type=type(e).__name__,
                error=str(e),
            )
    if failed:
        raise ApplicationError(
            f"{failed} of {len(row_ids)} case trigger outbox rows failed to dispatch",
            type="CaseTriggerDispatchError",
        )
    return len(row_ids)


@workflow.defn
class CaseTriggerDispatcher:
    """Start case-triggered runs for committed outbox rows."""

    @workflow.run
    async def run(self) -> None:
        while not workflow.info().is_continue_as_new_suggested():
            has_rows = await workflow.execute_activity(
                wait_for_case_trigger_outbox,
                start_to_close_timeout=timedelta(minutes=6),
                heartbeat_timeout=timedelta(seconds=60),
                retry_policy=_ACTIVITY_RETRY_POLICY,
            )
            if has_rows:
                await workflow.execute_activity(
                    dispatch_case_trigger_outbox_batch,
                    start_to_close_timeout=timedelta(minutes=10),
                    heartbeat_timeout=timedelta(seconds=60),
                    retry_policy=_ACTIVITY_RETRY_POLICY,
                )
        workflow.continue_as_new()


async def ensure_case_trigger_dispatcher(client: Client, task_queue: str) -> None:
    """Start the dispatcher unless it is already running. Safe on every replica."""
    await client.start_workflow(
        CaseTriggerDispatcher.run,
        id=CASE_TRIGGER_DISPATCHER_ID,
        task_queue=task_queue,
        id_conflict_policy=WorkflowIDConflictPolicy.USE_EXISTING,
    )


async def _keep_case_trigger_dispatcher_running(
    client: Client, task_queue: str
) -> None:
    """Re-check periodically, so a dispatcher that stopped is started again."""
    while True:
        delay = _RECONCILE_INTERVAL_SECONDS
        try:
            async with asyncio.timeout(_RECONCILE_TIMEOUT_SECONDS):
                await ensure_case_trigger_dispatcher(client, task_queue)
        except Exception as exc:
            delay = _RETRY_DELAY_SECONDS
            logger.warning(
                "Failed to start the case trigger dispatcher; retrying in 30 seconds",
                error_type=type(exc).__name__,
            )
        await asyncio.sleep(delay)


@asynccontextmanager
async def case_trigger_dispatcher_lifespan(
    client: Client, task_queue: str
) -> AsyncIterator[None]:
    """Keep the dispatcher running without delaying worker startup or shutdown."""
    task = asyncio.create_task(
        _keep_case_trigger_dispatcher_running(client, task_queue)
    )
    try:
        yield
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
