"""AgentExecutorWorker - Temporal worker for `run_agent_activity` execution."""

from __future__ import annotations

import asyncio
import os
import signal
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING

import uvloop
from temporalio.worker import Worker

from tracecat import config
from tracecat.agent.executor.activity import run_agent_activity
from tracecat.agent.runtime_services import (
    start_configured_llm_proxy,
    start_mcp_server,
    stop_configured_llm_proxy,
    stop_mcp_server,
)
from tracecat.agent.worker import new_sandbox_runner
from tracecat.dsl.client import get_temporal_client
from tracecat.logger import logger

if TYPE_CHECKING:
    from temporalio.client import Client

interrupt_event = asyncio.Event()
runtime_failure_reason: str | None = None


def get_activities() -> list:
    """Load runtime activities registered by the agent-executor worker."""
    return [run_agent_activity]


async def _start_runtime_services() -> Client:
    """Start shared runtime services needed by the agent executor worker."""
    logger.info("Starting runtime services")

    _, _, client = await asyncio.gather(
        start_configured_llm_proxy(),
        start_mcp_server(),
        get_temporal_client(),
    )
    return client


async def _stop_runtime_services() -> None:
    """Stop runtime services without letting one failure skip the others."""
    logger.info("Shutting down runtime services")
    results = await asyncio.gather(
        stop_mcp_server(),
        stop_configured_llm_proxy(),
        return_exceptions=True,
    )
    for service_name, result in zip(
        ("MCP server", "LLM gateway proxy"), results, strict=True
    ):
        if isinstance(result, Exception):
            logger.warning(
                "Runtime service shutdown failed",
                service=service_name,
                error=str(result),
            )


async def main() -> None:
    """Run the AgentExecutorWorker."""
    global runtime_failure_reason
    interrupt_event.clear()
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
                disable_eager_activity_execution=(
                    config.TEMPORAL__DISABLE_EAGER_ACTIVITY_EXECUTION
                ),
                activity_executor=executor,
            ):
                logger.info("AgentExecutorWorker started, ctrl+c to exit")
                await interrupt_event.wait()
                logger.info("Shutting down AgentExecutorWorker")
    finally:
        await _stop_runtime_services()
    if runtime_failure_reason is not None:
        raise RuntimeError(runtime_failure_reason)


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
