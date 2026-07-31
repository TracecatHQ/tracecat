"""AgentExecutorWorker - Temporal worker for `run_agent_activity` execution."""

from __future__ import annotations

import asyncio
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

import uvloop
from temporalio.worker import Worker

from tracecat import config
from tracecat.agent.executor.activity import (
    probe_stdio_mcp_connection_activity,
    run_agent_activity,
)
from tracecat.agent.runtime_services import (
    start_claude_runtime_broker,
    start_mcp_server,
    stop_claude_runtime_broker,
    stop_mcp_server,
)
from tracecat.agent.sandbox.cgroup import (
    clamp_agent_executor_concurrency,
    prepare_agent_sandbox_cgroup,
)
from tracecat.agent.worker import new_sandbox_runner
from tracecat.dsl.client import get_temporal_client
from tracecat.logger import logger
from tracecat.storage.blob import close_storage_client_cache
from tracecat.temporal.worker_lifecycle import run_worker_entrypoint

if TYPE_CHECKING:
    from temporalio.client import Client

runtime_failure_reason: str | None = None


def _write_readiness_file(path: Path, started_at: datetime) -> bool:
    """Write the best-effort agent executor readiness sentinel."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"{started_at.isoformat()}\n")
    except OSError as exc:
        logger.warning(
            "Unable to write AgentExecutorWorker readiness sentinel; continuing",
            path=str(path),
            errno=exc.errno,
            error=str(exc),
        )
        return False
    return True


def _remove_readiness_file(path: Path) -> None:
    """Remove the best-effort agent executor readiness sentinel."""
    try:
        path.unlink(missing_ok=True)
    except OSError as exc:
        logger.warning(
            "Unable to remove AgentExecutorWorker readiness sentinel; continuing",
            path=str(path),
            errno=exc.errno,
            error=str(exc),
        )


def get_activities() -> list:
    """Load runtime activities registered by the agent-executor worker."""
    return [
        run_agent_activity,
        probe_stdio_mcp_connection_activity,
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
    readiness_file = Path(config.TRACECAT__AGENT_EXECUTOR_READY_FILE)
    # A SIGKILLed predecessor cannot run its cleanup, and the sentinel may live
    # on a filesystem that survives container restarts; clear it before any
    # startup work — including validation that can raise — so a readiness
    # probe never sees a stale file.
    _remove_readiness_file(readiness_file)
    max_concurrent = int(
        os.environ.get("TRACECAT__AGENT_EXECUTOR_MAX_CONCURRENT_ACTIVITIES") or 1
    )
    if max_concurrent < 1:
        raise ValueError(
            "TRACECAT__AGENT_EXECUTOR_MAX_CONCURRENT_ACTIVITIES must be at "
            f"least 1 (got {max_concurrent})"
        )
    threadpool_max_workers = int(
        os.environ.get("TEMPORAL__THREADPOOL_MAX_WORKERS") or 100
    )
    cgroup_required = (
        config.TRACECAT__AGENT_SANDBOX_CGROUP_ENABLED
        and not config.TRACECAT__DISABLE_NSJAIL
    )
    prepared_cgroup = prepare_agent_sandbox_cgroup(enabled=cgroup_required)
    if cgroup_required:
        cgroup_mount = prepared_cgroup.require_sandbox_mount()
        logger.info(
            "Agent sandbox cgroup memory limits ready",
            cgroup_mount=str(cgroup_mount),
        )
    max_concurrent = clamp_agent_executor_concurrency(
        max_concurrent,
        prepared_cgroup,
        reserve_mb=config.TRACECAT__AGENT_EXECUTOR_MEMORY_RESERVE_MB,
        sandbox_memory_mb=config.TRACECAT__AGENT_SANDBOX_MEMORY_MB,
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
                _write_readiness_file(readiness_file, datetime.now(UTC))
                await shutdown_event.wait()
                logger.info("AgentExecutorWorker shutdown requested")
            logger.info("Temporal Worker context exited")
    finally:
        _remove_readiness_file(readiness_file)
        await close_storage_client_cache()
        await _stop_runtime_services()
    if runtime_failure_reason is not None:
        raise RuntimeError(runtime_failure_reason)


if __name__ == "__main__":
    asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())
    run_worker_entrypoint(main)
