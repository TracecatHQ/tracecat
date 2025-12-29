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
from tracecat.auth.types import Role  # noqa: E402
from tracecat.dsl.schemas import RunActionInput  # noqa: E402
from tracecat.executor.schemas import ExecutorActionErrorInfo  # noqa: E402
from tracecat.executor.service import run_action_from_input  # noqa: E402
from tracecat.logger import logger  # noqa: E402

# Global counters for connection tracking
_connection_counter = 0
_active_connections = 0
_worker_id = int(os.environ.get("TRACECAT_WORKER_ID", "0"))
_test_mode = os.environ.get("TRACECAT__POOL_WORKER_TEST_MODE", "").lower() == "true"


async def handle_task(request: dict[str, Any]) -> dict[str, Any]:
    """Handle a single task request.

    Args:
        request: Dict with keys: input, role

    Returns:
        Dict with keys: success, result, error, timing
    """
    timing: dict[str, float] = {}
    start_total = time.monotonic()

    try:
        # Parse input
        start = time.monotonic()
        input_obj = RunActionInput.model_validate(request["input"])
        role = Role.model_validate(request["role"])
        timing["parse_ms"] = (time.monotonic() - start) * 1000

        # Test mode: return mock success without executing action
        if _test_mode:
            timing["total_ms"] = (time.monotonic() - start_total) * 1000
            logger.info(
                "Task completed (test mode)",
                action=input_obj.task.action,
                timing=timing,
            )
            return {
                "success": True,
                "result": {
                    "test_mode": True,
                    "action": input_obj.task.action,
                    "workspace_id": str(role.workspace_id)
                    if role.workspace_id
                    else None,
                },
                "error": None,
                "timing": timing,
            }

        # Execute action
        start = time.monotonic()
        result = await run_action_from_input(input=input_obj, role=role)
        timing["action_ms"] = (time.monotonic() - start) * 1000

        timing["total_ms"] = (time.monotonic() - start_total) * 1000

        logger.info(
            "Task completed",
            action=input_obj.task.action,
            timing=timing,
        )

        return {
            "success": True,
            "result": result,
            "error": None,
            "timing": timing,
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
            error_dict = error_info.model_dump(mode="json")
        except Exception:
            # Fallback: don't include traceback in response to avoid leaking sensitive data
            error_dict = {
                "type": type(e).__name__,
                "message": str(e),
            }

        return {
            "success": False,
            "result": None,
            "error": error_dict,
            "timing": timing,
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
            result = {
                "success": False,
                "result": None,
                "error": {
                    "type": "TaskTimeout",
                    "message": f"Task timed out after {task_timeout}s in pool worker",
                },
                "timing": {"total_ms": task_timeout * 1000},
            }

        task_elapsed = time.monotonic() - task_start
        logger.info(
            "Task processed",
            worker_id=_worker_id,
            conn_id=conn_id,
            action=action_name,
            task_ref=task_ref,
            success=result.get("success"),
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
            error_response = orjson.dumps(
                {
                    "success": False,
                    "result": None,
                    "error": {"type": "WorkerError", "message": str(e)},
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

        logger.info(
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
