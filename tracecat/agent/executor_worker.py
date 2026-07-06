"""AgentExecutorWorker - Temporal worker for `run_agent_activity` execution."""

from __future__ import annotations

import asyncio
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from typing import TYPE_CHECKING

import uvloop
from temporalio.worker import Worker

from tracecat import config
from tracecat.agent.executor.activity import (
    probe_stdio_mcp_connection_activity,
    probe_stdio_mcp_draft_connection_activity,
    run_agent_activity,
)
from tracecat.agent.runtime_services import (
    start_claude_runtime_broker,
    start_mcp_server,
    stop_claude_runtime_broker,
    stop_mcp_server,
)
from tracecat.agent.worker import new_sandbox_runner
from tracecat.dsl.client import get_temporal_client
from tracecat.logger import logger
from tracecat.storage.blob import close_storage_client_cache
from tracecat.temporal.worker_lifecycle import run_worker_entrypoint

if TYPE_CHECKING:
    from temporalio.client import Client

runtime_failure_reason: str | None = None


def get_activities() -> list:
    """Load runtime activities registered by the agent-executor worker."""
    return [
        run_agent_activity,
        probe_stdio_mcp_connection_activity,
        probe_stdio_mcp_draft_connection_activity,
    ]


async def _start_runtime_services() -> Client:
    """Start shared runtime services needed by the agent executor worker."""
    logger.info("Starting runtime services")
    _, _, client = await asyncio.gather(
        start_claude_runtime_broker(),
        start_mcp_server(),
        get_temporal_client(),
    )
    return client


async def _stop_runtime_services() -> None:
    """Stop runtime services without letting one failure skip the others."""
    logger.info("Shutting down runtime services")
    results = await asyncio.gather(
        stop_claude_runtime_broker(),
        stop_mcp_server(),
        return_exceptions=True,
    )
    for service_name, result in zip(
        ("Claude runtime broker", "MCP server"),
        results,
        strict=True,
    ):
        if isinstance(result, Exception):
            logger.warning(
                "Runtime service shutdown failed",
                service=service_name,
                error=str(result),
            )


async def main(shutdown_event: asyncio.Event | None = None) -> None:
    """Run the AgentExecutorWorker."""
    global runtime_failure_reason
    if shutdown_event is None:
        shutdown_event = asyncio.Event()
    runtime_failure_reason = None
    max_concurrent = int(
        os.environ.get("TRACECAT__AGENT_EXECUTOR_MAX_CONCURRENT_ACTIVITIES") or 1
    )
    threadpool_max_workers = int(
        os.environ.get("TEMPORAL__THREADPOOL_MAX_WORKERS") or 100
    )

    logger.info(
        "Starting AgentExecutorWorker",
        task_queue=config.TRACECAT__AGENT_EXECUTOR_QUEUE,
        max_concurrent_activities=max_concurrent,
    )

    try:
        client = await _start_runtime_services()
        with ThreadPoolExecutor(max_workers=threadpool_max_workers) as executor:
            async with Worker(
                client,
                task_queue=config.TRACECAT__AGENT_EXECUTOR_QUEUE,
                activities=get_activities(),
                workflow_runner=new_sandbox_runner(),
                max_concurrent_activities=max_concurrent,
                disable_eager_activity_execution=config.TEMPORAL__DISABLE_EAGER_ACTIVITY_EXECUTION,
                activity_executor=executor,
                # Activity cancellation is only delivered to a running activity
                # via heartbeat RPC responses, and the SDK throttles those to
                # 80% of the heartbeat timeout (48s at our 60s timeout) by
                # default. Cap the throttle so Temporal-driven cancellation
                # reaches long agent turns promptly; the Redis cancel signal
                # (tracecat/agent/cancellation.py) remains the primary path.
                max_heartbeat_throttle_interval=timedelta(seconds=5),
                default_heartbeat_throttle_interval=timedelta(seconds=5),
                graceful_shutdown_timeout=timedelta(
                    seconds=config.TRACECAT__AGENT_EXECUTOR_GRACEFUL_SHUTDOWN_TIMEOUT
                ),
            ):
                logger.info("AgentExecutorWorker started, ctrl+c to exit")
                await shutdown_event.wait()
                logger.info("AgentExecutorWorker shutdown requested")
            logger.info("Temporal Worker context exited")
    finally:
        await close_storage_client_cache()
        await _stop_runtime_services()
    if runtime_failure_reason is not None:
        raise RuntimeError(runtime_failure_reason)


if __name__ == "__main__":
    asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())
    run_worker_entrypoint(main)
