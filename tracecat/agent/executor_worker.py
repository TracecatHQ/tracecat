"""AgentExecutorWorker - Temporal worker for `run_agent_activity` execution."""

from __future__ import annotations

import asyncio
import os
import signal
from concurrent.futures import ThreadPoolExecutor

import uvloop
from temporalio.worker import Worker

from tracecat import config
from tracecat.agent.executor.activity import run_agent_activity
from tracecat.agent.runtime_services import (
    start_litellm_proxy,
    start_mcp_server,
    stop_litellm_proxy,
    stop_mcp_server,
)
from tracecat.agent.worker import new_sandbox_runner
from tracecat.dsl.client import get_temporal_client
from tracecat.logger import logger

interrupt_event = asyncio.Event()


def get_activities() -> list:
    """Load runtime activities registered by the agent-executor worker."""
    return [run_agent_activity]


async def main() -> None:
    """Run the AgentExecutorWorker."""
    max_concurrent = int(
        os.environ.get("TRACECAT__AGENT_EXECUTOR_MAX_CONCURRENT_ACTIVITIES", 1)
    )
    threadpool_max_workers = int(
        os.environ.get("TEMPORAL__THREADPOOL_MAX_WORKERS", 100)
    )

    logger.info(
        "Starting AgentExecutorWorker",
        task_queue=config.TRACECAT__AGENT_EXECUTOR_QUEUE,
        max_concurrent_activities=max_concurrent,
    )

    await start_litellm_proxy()
    await start_mcp_server()

    try:
        client = await get_temporal_client()
        with ThreadPoolExecutor(max_workers=threadpool_max_workers) as executor:
            async with Worker(
                client,
                task_queue=config.TRACECAT__AGENT_EXECUTOR_QUEUE,
                activities=get_activities(),
                workflow_runner=new_sandbox_runner(),
                max_concurrent_activities=max_concurrent,
                disable_eager_activity_execution=config.TEMPORAL__DISABLE_EAGER_ACTIVITY_EXECUTION,
                activity_executor=executor,
            ):
                logger.info("AgentExecutorWorker started, ctrl+c to exit")
                await interrupt_event.wait()
                logger.info("Shutting down AgentExecutorWorker")
    finally:
        logger.info("Shutting down runtime services")
        await stop_mcp_server()
        await stop_litellm_proxy()


def _signal_handler(sig: int, _frame: object) -> None:
    """Handle shutdown signals gracefully."""
    logger.info("Received shutdown signal", signal=sig)
    interrupt_event.set()


if __name__ == "__main__":
    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())
    loop = asyncio.new_event_loop()
    loop.set_task_factory(asyncio.eager_task_factory)
    try:
        loop.run_until_complete(main())
    except KeyboardInterrupt:
        interrupt_event.set()
    finally:
        loop.run_until_complete(loop.shutdown_asyncgens())
