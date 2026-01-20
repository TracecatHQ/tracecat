"""Worker process for the warm worker pool.

This process is spawned by WorkerPool and stays alive to handle
multiple tasks. All heavy imports are done once at startup.

Architecture:
- Single event loop handles all connections concurrently
- asyncio.start_unix_server spawns a coroutine per connection
- When one task awaits (IO), others can run (AsyncConcurrent pattern)
- No ThreadPool needed - asyncio handles IO concurrency naturally

See scripts/benchmark_pool.py for performance comparison showing
AsyncConcurrent matches ThreadPool performance with simpler architecture.

Environment variables:
- TRACECAT_WORKER_ID: Worker ID (0, 1, 2, ...)
- TRACECAT_WORKER_SOCKET: Path to Unix socket
- TRACECAT__POOL_WORKER_TEST_MODE: If "true", return mock success without executing actions
"""

from __future__ import annotations

import asyncio
import os
import signal
import sys
import time
import traceback
from pathlib import Path
from typing import Any

import orjson
import uvloop
from pydantic_core import to_jsonable_python

# Set uvloop as the default event loop policy
asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())

# =============================================================================
# HEAVY IMPORTS - Done once at startup, this is the key optimization
# =============================================================================
from tracecat import config  # noqa: E402
from tracecat.auth.types import Role  # noqa: E402
from tracecat.contexts import ctx_role  # noqa: E402
from tracecat.executor.minimal_runner import run_action_minimal_async  # noqa: E402
from tracecat.executor.schemas import (  # noqa: E402
    ExecutorActionErrorInfo,
    ResolvedContext,
)
from tracecat.logger import logger  # noqa: E402

# Track tarball paths already added to sys.path to avoid duplicates
_added_tarball_paths: set[str] = set()


def _ensure_tarball_paths_in_sys_path() -> None:
    """Ensure all tarball extraction directories are in sys.path.

    Scans the registry cache directory for tarball-* subdirectories and adds
    them to sys.path if not already present. This allows the worker to import
    modules from both builtin and custom registries.
    """
    cache_dir = Path(config.TRACECAT__EXECUTOR_REGISTRY_CACHE_DIR)
    if not cache_dir.exists():
        return

    for path in cache_dir.iterdir():
        if path.is_dir() and path.name.startswith("tarball-"):
            path_str = str(path)
            if path_str not in _added_tarball_paths and path_str not in sys.path:
                sys.path.insert(0, path_str)
                _added_tarball_paths.add(path_str)
                logger.debug(
                    "Added tarball path to sys.path",
                    path=path_str,
                    worker_id=_worker_id,
                )


# Global counters for connection tracking
_connection_counter = 0
_active_connections = 0
_worker_id = int(os.environ.get("TRACECAT_WORKER_ID", "0"))
_test_mode = os.environ.get("TRACECAT__POOL_WORKER_TEST_MODE", "").lower() == "true"


async def handle_task(request: dict[str, Any]) -> dict[str, Any]:
    """Handle a single task request.

    Args:
        request: Dict with keys: input, role, resolved_context

    Returns:
        Dict matching ExecutorResult schema:
        - Success: {"type": "success", "result": ...}
        - Failure: {"type": "failure", "error": ...}
    """
    timing: dict[str, float] = {}
    start_total = time.monotonic()

    try:
        # Parse input
        # NOTE: We don't validate RunActionInput strictly because exec_context may be
        # MaterializedExecutionContext (raw values) after materialize_context() in the
        # activity. The pool worker only needs task metadata for logging - actual
        # execution uses resolved_context.evaluated_args which is already resolved.
        start = time.monotonic()
        input_dict = request["input"]
        task_dict = input_dict.get("task", {})
        action_name = task_dict.get("action", "unknown")
        role = Role.model_validate(request["role"])
        resolved_context = ResolvedContext.model_validate(request["resolved_context"])
        timing["parse_ms"] = (time.monotonic() - start) * 1000

        # Test mode: return mock success without executing action
        if _test_mode:
            timing["total_ms"] = (time.monotonic() - start_total) * 1000
            logger.info(
                "Task completed (test mode)",
                action=action_name,
                timing=timing,
            )
            return {
                "type": "success",
                "result": {
                    "test_mode": True,
                    "action": action_name,
                    "workspace_id": str(role.workspace_id)
                    if role.workspace_id
                    else None,
                    "timing": timing,
                },
            }

        # Ensure tarball paths are in sys.path for custom registry modules
        _ensure_tarball_paths_in_sys_path()

        # Set the role context for actions that need it (e.g., core.cases, core.table)
        ctx_role.set(role)

        # Execute action using minimal runner (no DB access, explicit context)
        start = time.monotonic()
        result = await run_action_minimal_async(
            action_impl=resolved_context.action_impl.model_dump(),
            args=resolved_context.evaluated_args,
            secrets=resolved_context.secrets,
            workspace_id=resolved_context.workspace_id,
            workflow_id=resolved_context.workflow_id,
            run_id=resolved_context.run_id,
            executor_token=resolved_context.executor_token,
        )
        timing["action_ms"] = (time.monotonic() - start) * 1000

        timing["total_ms"] = (time.monotonic() - start_total) * 1000

        logger.info(
            "Task completed",
            action=action_name,
            timing=timing,
        )

        return {
            "type": "success",
            "result": result,
        }

    except Exception as e:
        timing["total_ms"] = (time.monotonic() - start_total) * 1000

        # Log error summary at error level, full traceback only at debug
        # to avoid leaking sensitive information in production logs
        logger.error(
            "Task failed",
            error=str(e),
            type=type(e).__name__,
        )
        logger.debug("Task failed traceback", traceback=traceback.format_exc())

        # Try to create structured error
        try:
            action_name = (
                request.get("input", {}).get("task", {}).get("action", "unknown")
            )
            error_info = ExecutorActionErrorInfo.from_exc(e, action_name)
        except Exception:
            # Fallback: create minimal error info
            error_info = ExecutorActionErrorInfo(
                action_name="unknown",
                type=type(e).__name__,
                message=str(e),
                filename="unknown",
                function="unknown",
            )

        return {
            "type": "failure",
            "error": error_info.model_dump(mode="json"),
        }


async def handle_connection(
    reader: asyncio.StreamReader, writer: asyncio.StreamWriter
) -> None:
    """Handle a single client connection.

    Each connection runs as a coroutine on the main event loop.
    Multiple connections are handled concurrently - when one awaits IO,
    others can proceed (AsyncConcurrent pattern).
    """
    global _connection_counter, _active_connections

    # Assign connection ID and track active connections
    _connection_counter += 1
    conn_id = _connection_counter
    _active_connections += 1

    # Task timeout - prevents indefinite hangs on stuck DB queries, etc.
    task_timeout = float(os.environ.get("TRACECAT__POOL_WORKER_TASK_TIMEOUT", "300"))
    action_name = "unknown"
    task_ref = "unknown"

    logger.debug(
        "Connection received",
        worker_id=_worker_id,
        conn_id=conn_id,
        active_connections=_active_connections,
    )

    try:
        # Read request (length-prefixed)
        logger.debug(
            "Reading request length",
            worker_id=_worker_id,
            conn_id=conn_id,
        )
        length_bytes = await reader.readexactly(4)
        request_length = int.from_bytes(length_bytes, "big")

        logger.debug(
            "Reading request body",
            worker_id=_worker_id,
            conn_id=conn_id,
            request_length=request_length,
        )
        request_bytes = await reader.readexactly(request_length)
        request = orjson.loads(request_bytes)

        # Extract action info for logging
        action_name = request.get("input", {}).get("task", {}).get("action", "unknown")
        task_ref = request.get("input", {}).get("task", {}).get("ref", "unknown")

        logger.info(
            "Task received",
            worker_id=_worker_id,
            conn_id=conn_id,
            action=action_name,
            task_ref=task_ref,
            active_connections=_active_connections,
        )

        # Handle task with timeout to prevent indefinite hangs
        task_start = time.monotonic()
        try:
            result = await asyncio.wait_for(handle_task(request), timeout=task_timeout)
        except TimeoutError:
            elapsed = time.monotonic() - task_start
            logger.error(
                "Task timed out in pool worker",
                worker_id=_worker_id,
                conn_id=conn_id,
                action=action_name,
                task_ref=task_ref,
                timeout=task_timeout,
                elapsed_s=f"{elapsed:.1f}",
                active_connections=_active_connections,
            )
            error_info = ExecutorActionErrorInfo(
                action_name=action_name,
                type="TaskTimeout",
                message=f"Task timed out after {task_timeout}s in pool worker",
                filename="worker.py",
                function="handle_connection",
            )
            result = {
                "type": "failure",
                "error": error_info.model_dump(mode="json"),
            }

        task_elapsed = time.monotonic() - task_start
        logger.info(
            "Task processed",
            worker_id=_worker_id,
            conn_id=conn_id,
            action=action_name,
            task_ref=task_ref,
            success=result.get("type") == "success",
            elapsed_ms=f"{task_elapsed * 1000:.1f}",
            active_connections=_active_connections,
        )

        # Write response (length-prefixed)
        logger.debug(
            "Writing response",
            worker_id=_worker_id,
            conn_id=conn_id,
        )
        response_bytes = orjson.dumps(result, default=to_jsonable_python)
        length_prefix = len(response_bytes).to_bytes(4, "big")

        writer.write(length_prefix + response_bytes)
        await writer.drain()

        logger.debug(
            "Response sent",
            worker_id=_worker_id,
            conn_id=conn_id,
            response_length=len(response_bytes),
        )

    except asyncio.IncompleteReadError as e:
        logger.warning(
            "Client disconnected (incomplete read)",
            worker_id=_worker_id,
            conn_id=conn_id,
            action=action_name,
            task_ref=task_ref,
            active_connections=_active_connections,
            error=str(e),
        )
    except asyncio.CancelledError:
        logger.warning(
            "Connection cancelled",
            worker_id=_worker_id,
            conn_id=conn_id,
            action=action_name,
            task_ref=task_ref,
            active_connections=_active_connections,
        )
        raise
    except Exception as e:
        logger.error(
            "Connection handler error",
            worker_id=_worker_id,
            conn_id=conn_id,
            action=action_name,
            task_ref=task_ref,
            active_connections=_active_connections,
            error=str(e),
            error_type=type(e).__name__,
        )
        # Log traceback at debug level to avoid leaking sensitive data in production
        logger.debug(
            "Connection handler error traceback", traceback=traceback.format_exc()
        )
        # Try to send error response
        try:
            error_info = ExecutorActionErrorInfo(
                action_name=action_name,
                type="WorkerError",
                message=str(e),
                filename="worker.py",
                function="handle_connection",
            )
            error_response = orjson.dumps(
                {
                    "type": "failure",
                    "error": error_info.model_dump(mode="json"),
                },
                default=to_jsonable_python,
            )
            length_prefix = len(error_response).to_bytes(4, "big")
            writer.write(length_prefix + error_response)
            await writer.drain()
        except Exception:
            pass
    finally:
        _active_connections -= 1
        logger.debug(
            "Connection closed",
            worker_id=_worker_id,
            conn_id=conn_id,
            active_connections=_active_connections,
        )
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass


async def _heartbeat_loop(worker_id: int, interval: float = 30.0) -> None:
    """Emit periodic heartbeat to detect event loop blocks."""
    global _active_connections, _connection_counter
    last_beat = time.monotonic()
    tasks_at_last_beat = 0

    while True:
        await asyncio.sleep(interval)
        now = time.monotonic()
        elapsed = now - last_beat

        # Get pending tasks count (indicates event loop congestion)
        try:
            pending_tasks = len(asyncio.all_tasks())
        except Exception:
            pending_tasks = -1

        # Detect if heartbeat was delayed (event loop blocked)
        expected_elapsed = interval
        delay = elapsed - expected_elapsed

        logger.debug(
            "Pool worker heartbeat",
            worker_id=worker_id,
            active_connections=_active_connections,
            total_connections=_connection_counter,
            tasks_since_last_beat=_connection_counter - tasks_at_last_beat,
            pending_asyncio_tasks=pending_tasks,
            heartbeat_delay_ms=f"{delay * 1000:.1f}" if delay > 0.1 else "0",
            pid=os.getpid(),
        )

        # Warn if heartbeat was significantly delayed (event loop might be blocked)
        if delay > 1.0:
            logger.warning(
                "Pool worker heartbeat delayed - event loop may be blocked",
                worker_id=worker_id,
                expected_interval=interval,
                actual_elapsed=f"{elapsed:.1f}",
                delay_s=f"{delay:.1f}",
            )

        last_beat = now
        tasks_at_last_beat = _connection_counter


async def run_worker(socket_path: Path, worker_id: int) -> None:
    """Run the worker server loop."""
    # Remove stale socket
    socket_path.unlink(missing_ok=True)

    # Create Unix socket server
    # Each incoming connection spawns a handle_connection coroutine
    # Multiple connections are handled concurrently on the same event loop
    server = await asyncio.start_unix_server(
        handle_connection,
        path=str(socket_path),
    )

    # Set socket permissions (readable/writable by owner only)
    os.chmod(socket_path, 0o600)

    logger.info(
        "Worker ready",
        worker_id=worker_id,
        pid=os.getpid(),
        socket=str(socket_path),
    )

    # Handle shutdown signals
    shutdown_event = asyncio.Event()

    def handle_signal(sig: int) -> None:
        logger.info("Worker received signal", signal=sig, worker_id=worker_id)
        shutdown_event.set()

    loop = asyncio.get_event_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, lambda s=sig: handle_signal(s))

    # Start heartbeat task
    heartbeat_task = asyncio.create_task(_heartbeat_loop(worker_id))

    # Serve until shutdown
    async with server:
        await shutdown_event.wait()

    # Cancel heartbeat
    heartbeat_task.cancel()
    try:
        await heartbeat_task
    except asyncio.CancelledError:
        pass

    socket_path.unlink(missing_ok=True)
    logger.info("Worker shutdown complete", worker_id=worker_id)


def main() -> None:
    """Worker entrypoint."""
    worker_id = int(os.environ.get("TRACECAT_WORKER_ID", "0"))
    socket_path = Path(
        os.environ.get(
            "TRACECAT_WORKER_SOCKET", f"/tmp/tracecat-workers/worker-{worker_id}.sock"
        )
    )

    # Ensure parent directory exists
    socket_path.parent.mkdir(parents=True, exist_ok=True)

    logger.info(
        "Worker starting",
        worker_id=worker_id,
        pid=os.getpid(),
        socket=str(socket_path),
    )

    try:
        asyncio.run(run_worker(socket_path, worker_id))
    except KeyboardInterrupt:
        logger.info("Worker interrupted", worker_id=worker_id)
    except Exception as e:
        logger.error("Worker crashed", worker_id=worker_id, error=str(e))
        sys.exit(1)


if __name__ == "__main__":
    main()
