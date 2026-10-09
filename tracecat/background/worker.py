"""Dedicated Temporal worker for generic platform background tasks."""

import asyncio
from contextlib import AsyncExitStack
from datetime import timedelta

import uvloop
from temporalio.worker import Worker

from tracecat import config
from tracecat.background.sandbox import new_sandbox_runner
from tracecat.dsl.client import get_temporal_client
from tracecat.logger import logger
from tracecat.observability.otel import (
    initialize_platform_tracing,
    shutdown_platform_tracing,
)
from tracecat.observability.sentry import initialize_worker_sentry_from_environment
from tracecat.storage.blob import close_storage_client_cache
from tracecat.temporal.worker_lifecycle import run_worker_entrypoint
from tracecat.workspace_sync.operations.activities import (
    workspace_sync_apply,
    workspace_sync_fail,
    workspace_sync_prepare,
)
from tracecat.workspace_sync.operations.workflows import WorkspaceSyncWorkflow


async def main(shutdown_event: asyncio.Event | None = None) -> None:
    """Poll the shared background queue with bounded activity concurrency."""
    shutdown_event = shutdown_event or asyncio.Event()
    initialize_platform_tracing("tracecat-background-worker")
    initialize_worker_sentry_from_environment()
    async with AsyncExitStack() as cleanup:
        cleanup.callback(shutdown_platform_tracing)
        cleanup.push_async_callback(close_storage_client_cache)
        client = await get_temporal_client()
        async with Worker(
            client,
            task_queue=config.TRACECAT__BACKGROUND_QUEUE,
            workflows=[WorkspaceSyncWorkflow],
            workflow_runner=new_sandbox_runner(),
            activities=[
                workspace_sync_prepare,
                workspace_sync_apply,
                workspace_sync_fail,
            ],
            max_concurrent_activities=config.TRACECAT__BACKGROUND_MAX_CONCURRENT_ACTIVITIES,
            disable_eager_activity_execution=True,
            graceful_shutdown_timeout=timedelta(seconds=30),
        ):
            logger.info("Background worker started")
            await shutdown_event.wait()


if __name__ == "__main__":
    asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())
    run_worker_entrypoint(main)
