"""ExecutorWorker - Temporal worker for action execution.

This worker listens on the 'shared-action-queue' and executes actions
dispatched from DSL workflows. It uses the configured executor backend
for action execution.

Supported backends (via TRACECAT__EXECUTOR_BACKEND):
- sandboxed_pool: Warm nsjail workers (single-tenant, high throughput)
- ephemeral: Cold nsjail subprocess per action (multitenant, full isolation)
- direct: In-process execution (development only)
- auto: Auto-select based on environment

Architecture:
    DSLWorkflow (tracecat-task-queue)
        |
        v
    workflow.execute_activity("execute_action_activity", task_queue="shared-action-queue")
        |
        v
    ExecutorWorker picks up task
        |
        v
    dispatch_action() -> backend.execute() -> action result
"""

from __future__ import annotations

import asyncio
import os
import signal
from concurrent.futures import ThreadPoolExecutor

import uvloop
from temporalio.worker import Worker

from tracecat import config
from tracecat.dsl.client import get_temporal_client
from tracecat.executor.activities import ExecutorActivities
from tracecat.executor.backends import (
    initialize_executor_backend,
    shutdown_executor_backend,
)
from tracecat.logger import logger

interrupt_event = asyncio.Event()

asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())


async def main() -> None:
    """Run the ExecutorWorker."""
    # Get configuration
    task_queue = config.TRACECAT__EXECUTOR_QUEUE
    max_concurrent = int(
        os.environ.get("TRACECAT__EXECUTOR_MAX_CONCURRENT_ACTIVITIES", 100)
    )
    threadpool_max_workers = int(
        os.environ.get("TRACECAT__EXECUTOR_THREADPOOL_MAX_WORKERS", 100)
    )

    logger.info(
        "Starting ExecutorWorker",
        task_queue=task_queue,
        max_concurrent_activities=max_concurrent,
        threadpool_max_workers=threadpool_max_workers,
        executor_backend=config.TRACECAT__EXECUTOR_BACKEND,
    )

    # Initialize the executor backend before accepting tasks
    await initialize_executor_backend()

    try:
        client = await get_temporal_client()
        activities = ExecutorActivities.get_activities()

        logger.debug(
            "Activities loaded",
            activities=[
                getattr(a, "__temporal_activity_definition").name for a in activities
            ],
        )

        with ThreadPoolExecutor(max_workers=threadpool_max_workers) as executor:
            async with Worker(
                client,
                task_queue=task_queue,
                activities=activities,
                activity_executor=executor,
                max_concurrent_activities=max_concurrent,
            ):
                logger.info(
                    "ExecutorWorker started, ctrl+c to exit",
                    task_queue=task_queue,
                    max_concurrent_activities=max_concurrent,
                )
                # Wait until interrupted
                await interrupt_event.wait()
                logger.info("Shutting down ExecutorWorker")
    finally:
        logger.info("Shutting down executor backend")
        await shutdown_executor_backend()


def _signal_handler(sig: int, _frame: object) -> None:
    """Handle shutdown signals gracefully."""
    logger.info("Received shutdown signal", signal=sig)
    interrupt_event.set()


if __name__ == "__main__":
    # Install signal handlers before starting the event loop
    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    loop = asyncio.new_event_loop()
    loop.set_task_factory(asyncio.eager_task_factory)
    try:
        loop.run_until_complete(main())
    except KeyboardInterrupt:
        interrupt_event.set()
    finally:
        loop.run_until_complete(loop.shutdown_asyncgens())
