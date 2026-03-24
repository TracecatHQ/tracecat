"""Runtime sidecars for agent execution workers."""

from __future__ import annotations

import asyncio
import inspect
import os
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import httpx

from tracecat.agent.litellm_observability import get_load_tracker
from tracecat.config import (
    TRACECAT__LITELLM_HEALTHCHECK_FAILURE_THRESHOLD,
    TRACECAT__LITELLM_HEALTHCHECK_INTERVAL_SECONDS,
    TRACECAT__LITELLM_HEALTHCHECK_TIMEOUT_SECONDS,
    TRACECAT__LITELLM_NUM_WORKERS,
    TRACECAT__LITELLM_STATUS_LOG_INTERVAL_SECONDS,
)
from tracecat.logger import logger


@dataclass(frozen=True, slots=True)
class LiteLLMProxyStatus:
    """Current process-local LiteLLM sidecar status."""

    state: Literal["starting", "ready", "unhealthy", "stopped"] = "stopped"
    pid: int | None = None
    last_ready_at: float | None = None
    consecutive_probe_failures: int = 0
    reason: str | None = None
    exit_code: int | None = None


_LITELLM_READINESS_URL = "http://127.0.0.1:4000/health/readiness"
type LiteLLMUnhealthyCallback = Callable[[LiteLLMProxyStatus], object | Awaitable[None]]

_litellm_process: asyncio.subprocess.Process | None = None
_litellm_stderr_task: asyncio.Task[None] | None = None
_litellm_exit_task: asyncio.Task[None] | None = None
_litellm_health_task: asyncio.Task[None] | None = None
_litellm_status_log_task: asyncio.Task[None] | None = None
_litellm_on_unhealthy: LiteLLMUnhealthyCallback | None = None
_litellm_unhealthy_notified = False
_litellm_status = LiteLLMProxyStatus()
_mcp_server_task: asyncio.Task[None] | None = None
_proxy_load_tracker = get_load_tracker("llm_socket_proxy")


class _StatusUnset:
    pass


_STATUS_UNSET = _StatusUnset()


def _proxy_load_fields() -> dict[str, int]:
    snapshot = _proxy_load_tracker.snapshot()
    return {
        "active_proxy_connections": snapshot.active_connections,
        "active_proxy_requests": snapshot.active_requests,
        "proxy_total_requests": snapshot.total_requests,
        "proxy_peak_active_connections": snapshot.peak_active_connections,
        "proxy_peak_active_requests": snapshot.peak_active_requests,
    }


def _build_litellm_command(runtime_config_path: Path) -> list[str]:
    cmd = [
        "litellm",
        "--port",
        "4000",
        "--config",
        str(runtime_config_path),
        "--num_workers",
        str(TRACECAT__LITELLM_NUM_WORKERS),
    ]
    if TRACECAT__LITELLM_NUM_WORKERS > 1:
        cmd.append("--run_gunicorn")
    return cmd


def get_litellm_proxy_status() -> LiteLLMProxyStatus:
    """Return the current LiteLLM sidecar status."""
    return _litellm_status


def _set_litellm_status(
    *,
    state: Literal["starting", "ready", "unhealthy", "stopped"]
    | _StatusUnset = _STATUS_UNSET,
    pid: int | None | _StatusUnset = _STATUS_UNSET,
    last_ready_at: float | None | _StatusUnset = _STATUS_UNSET,
    consecutive_probe_failures: int | _StatusUnset = _STATUS_UNSET,
    reason: str | None | _StatusUnset = _STATUS_UNSET,
    exit_code: int | None | _StatusUnset = _STATUS_UNSET,
) -> LiteLLMProxyStatus:
    global _litellm_status
    current = _litellm_status
    _litellm_status = LiteLLMProxyStatus(
        state=current.state if isinstance(state, _StatusUnset) else state,
        pid=current.pid if isinstance(pid, _StatusUnset) else pid,
        last_ready_at=(
            current.last_ready_at
            if isinstance(last_ready_at, _StatusUnset)
            else last_ready_at
        ),
        consecutive_probe_failures=(
            current.consecutive_probe_failures
            if isinstance(consecutive_probe_failures, _StatusUnset)
            else consecutive_probe_failures
        ),
        reason=current.reason if isinstance(reason, _StatusUnset) else reason,
        exit_code=current.exit_code
        if isinstance(exit_code, _StatusUnset)
        else exit_code,
    )
    return _litellm_status


async def _invoke_litellm_on_unhealthy(status: LiteLLMProxyStatus) -> None:
    callback = _litellm_on_unhealthy
    if callback is None:
        return
    result = callback(status)
    if inspect.isawaitable(result):
        await result


async def _mark_litellm_unhealthy(
    *,
    reason: str,
    exit_code: int | None = None,
) -> None:
    global _litellm_unhealthy_notified

    current_status = get_litellm_proxy_status()
    if current_status.state == "stopped":
        return
    if current_status.state == "unhealthy":
        return

    status = _set_litellm_status(
        state="unhealthy",
        reason=reason,
        exit_code=exit_code,
    )
    logger.error(
        "LiteLLM sidecar became unhealthy",
        reason=reason,
        exit_code=exit_code,
        pid=status.pid,
        consecutive_probe_failures=status.consecutive_probe_failures,
        **_proxy_load_fields(),
    )
    if _litellm_unhealthy_notified:
        return
    _litellm_unhealthy_notified = True
    await _invoke_litellm_on_unhealthy(status)


async def _stream_litellm_stderr(process: asyncio.subprocess.Process) -> None:
    """Stream LiteLLM stderr to logger."""
    if process.stderr is None:
        return
    try:
        async for line in process.stderr:
            decoded = line.decode("utf-8", errors="replace").rstrip()
            if decoded:
                logger.info("LiteLLM stderr", line=decoded)
    except asyncio.CancelledError:
        raise
    except Exception as e:
        logger.warning("LiteLLM stderr stream ended", error=str(e))


async def _watch_litellm_process(process: asyncio.subprocess.Process) -> None:
    """Watch the LiteLLM subprocess for unexpected exit."""
    try:
        return_code = await process.wait()
    except asyncio.CancelledError:
        raise
    if _litellm_process is not process:
        return
    await _mark_litellm_unhealthy(
        reason=f"LiteLLM process exited unexpectedly with code {return_code}",
        exit_code=return_code,
    )


async def _monitor_litellm_health(
    url: str = _LITELLM_READINESS_URL,
) -> None:
    """Continuously poll LiteLLM readiness and fail fast on repeated probe failures."""
    timeout = httpx.Timeout(
        connect=TRACECAT__LITELLM_HEALTHCHECK_TIMEOUT_SECONDS,
        read=TRACECAT__LITELLM_HEALTHCHECK_TIMEOUT_SECONDS,
        write=TRACECAT__LITELLM_HEALTHCHECK_TIMEOUT_SECONDS,
        pool=TRACECAT__LITELLM_HEALTHCHECK_TIMEOUT_SECONDS,
    )
    async with httpx.AsyncClient(timeout=timeout) as client:
        while True:
            await asyncio.sleep(TRACECAT__LITELLM_HEALTHCHECK_INTERVAL_SECONDS)

            process = _litellm_process
            if process is None or process.returncode is not None:
                return

            failure_reason: str | None = None
            try:
                response = await client.get(url)
                if response.status_code == 200:
                    previous_failures = (
                        get_litellm_proxy_status().consecutive_probe_failures
                    )
                    status = _set_litellm_status(
                        state="ready",
                        last_ready_at=time.time(),
                        consecutive_probe_failures=0,
                        reason=None,
                        exit_code=None,
                    )
                    if previous_failures > 0:
                        logger.info(
                            "LiteLLM readiness recovered",
                            pid=status.pid,
                            **_proxy_load_fields(),
                        )
                    continue
                failure_reason = (
                    f"LiteLLM readiness probe returned HTTP {response.status_code}"
                )
            except httpx.TimeoutException as e:
                failure_reason = (
                    f"LiteLLM readiness probe timed out ({type(e).__name__})"
                )
            except httpx.HTTPError as e:
                failure_reason = (
                    f"LiteLLM readiness probe failed ({type(e).__name__}: {e})"
                )

            next_failures = get_litellm_proxy_status().consecutive_probe_failures + 1
            current_status = get_litellm_proxy_status()
            _set_litellm_status(
                state=current_status.state,
                consecutive_probe_failures=next_failures,
                reason=failure_reason,
            )
            logger.warning(
                "LiteLLM readiness probe failed",
                failure_reason=failure_reason,
                consecutive_probe_failures=next_failures,
                failure_threshold=TRACECAT__LITELLM_HEALTHCHECK_FAILURE_THRESHOLD,
                **_proxy_load_fields(),
            )
            if next_failures >= TRACECAT__LITELLM_HEALTHCHECK_FAILURE_THRESHOLD:
                await _mark_litellm_unhealthy(reason=failure_reason or "unknown")
                return


async def _log_litellm_status_loop() -> None:
    """Emit periodic LiteLLM sidecar status heartbeats."""
    while True:
        await asyncio.sleep(TRACECAT__LITELLM_STATUS_LOG_INTERVAL_SECONDS)
        status = get_litellm_proxy_status()
        if status.state == "stopped":
            return
        logger.info(
            "LiteLLM sidecar status",
            state=status.state,
            pid=status.pid,
            last_ready_at=status.last_ready_at,
            consecutive_probe_failures=status.consecutive_probe_failures,
            reason=status.reason,
            exit_code=status.exit_code,
            **_proxy_load_fields(),
        )


async def _wait_for_socket(
    socket_path: Path,
    *,
    attempts: int = 50,
    interval: float = 0.1,
) -> bool:
    """Wait for a Unix socket path to appear."""
    for _ in range(attempts):
        if socket_path.exists():
            return True
        await asyncio.sleep(interval)
    return False


async def start_litellm_proxy(
    *,
    on_unhealthy: LiteLLMUnhealthyCallback | None = None,
) -> None:
    """Start the LiteLLM proxy subprocess and health supervision."""
    global _litellm_process
    global _litellm_stderr_task
    global _litellm_exit_task
    global _litellm_health_task
    global _litellm_status_log_task
    global _litellm_on_unhealthy
    global _litellm_unhealthy_notified

    if _litellm_process is not None and _litellm_process.returncode is None:
        logger.info("LiteLLM proxy already running", pid=_litellm_process.pid)
        return

    source_config = Path(__file__).parent / "litellm_config.yaml"
    if not source_config.exists():
        logger.error("LiteLLM config not found", config_path=str(source_config))
        return

    runtime_config = Path("/app/litellm_config.yaml")
    temp_symlink = runtime_config.with_suffix(f".yaml.{os.getpid()}.tmp")
    try:
        temp_symlink.symlink_to(source_config)
        temp_symlink.replace(runtime_config)
    except FileExistsError:
        pass
    finally:
        if temp_symlink.exists() or temp_symlink.is_symlink():
            temp_symlink.unlink()

    _litellm_on_unhealthy = on_unhealthy
    _litellm_unhealthy_notified = False
    _set_litellm_status(
        state="starting",
        pid=None,
        last_ready_at=None,
        consecutive_probe_failures=0,
        reason=None,
        exit_code=None,
    )

    cmd = _build_litellm_command(runtime_config)
    logger.info(
        "Starting LiteLLM proxy",
        num_workers=TRACECAT__LITELLM_NUM_WORKERS,
    )

    env = os.environ.copy()
    pythonpath = env.get("PYTHONPATH", "")
    app_paths = "/app:/app/packages/tracecat-registry:/app/packages/tracecat-ee"
    env["PYTHONPATH"] = f"{app_paths}:{pythonpath}" if pythonpath else app_paths

    try:
        _litellm_process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        _set_litellm_status(state="starting", pid=_litellm_process.pid)
        _litellm_stderr_task = asyncio.create_task(
            _stream_litellm_stderr(_litellm_process)
        )

        await _wait_for_litellm_ready()
        _set_litellm_status(
            state="ready",
            pid=_litellm_process.pid,
            last_ready_at=time.time(),
            consecutive_probe_failures=0,
            reason=None,
            exit_code=None,
        )
        _litellm_exit_task = asyncio.create_task(
            _watch_litellm_process(_litellm_process)
        )
        _litellm_health_task = asyncio.create_task(_monitor_litellm_health())
        _litellm_status_log_task = asyncio.create_task(_log_litellm_status_loop())
        logger.info(
            "LiteLLM proxy started",
            pid=_litellm_process.pid,
            **_proxy_load_fields(),
        )
    except Exception:
        await stop_litellm_proxy()
        raise


async def _wait_for_litellm_ready(
    url: str = _LITELLM_READINESS_URL,
    max_attempts: int = 60,
    interval: float = 0.5,
) -> None:
    """Poll LiteLLM health endpoint until it responds 200."""
    timeout = httpx.Timeout(
        connect=TRACECAT__LITELLM_HEALTHCHECK_TIMEOUT_SECONDS,
        read=TRACECAT__LITELLM_HEALTHCHECK_TIMEOUT_SECONDS,
        write=TRACECAT__LITELLM_HEALTHCHECK_TIMEOUT_SECONDS,
        pool=TRACECAT__LITELLM_HEALTHCHECK_TIMEOUT_SECONDS,
    )
    async with httpx.AsyncClient(timeout=timeout) as client:
        for attempt in range(1, max_attempts + 1):
            if _litellm_process is not None and _litellm_process.returncode is not None:
                raise RuntimeError(
                    "LiteLLM process exited with code "
                    f"{_litellm_process.returncode} during startup"
                )
            try:
                response = await client.get(url)
                if response.status_code == 200:
                    logger.info(
                        "LiteLLM readiness confirmed",
                        attempts=attempt,
                    )
                    return
                logger.debug(
                    "LiteLLM readiness returned non-200 during startup",
                    attempt=attempt,
                    status_code=response.status_code,
                )
            except httpx.ConnectError:
                pass
            except httpx.TimeoutException as e:
                logger.debug(
                    "LiteLLM readiness probe timed out during startup",
                    attempt=attempt,
                    error_type=type(e).__name__,
                )
            if attempt % 10 == 0:
                logger.info(
                    "Waiting for LiteLLM to become ready",
                    attempt=attempt,
                    max_attempts=max_attempts,
                )
            await asyncio.sleep(interval)
    raise RuntimeError(f"LiteLLM did not become ready after {max_attempts} attempts")


async def stop_litellm_proxy() -> None:
    """Stop the LiteLLM proxy subprocess and supervision tasks."""
    global _litellm_process
    global _litellm_stderr_task
    global _litellm_exit_task
    global _litellm_health_task
    global _litellm_status_log_task
    global _litellm_on_unhealthy
    global _litellm_unhealthy_notified
    for task in (
        _litellm_status_log_task,
        _litellm_health_task,
        _litellm_exit_task,
        _litellm_stderr_task,
    ):
        if task is None:
            continue
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    _litellm_status_log_task = None
    _litellm_health_task = None
    _litellm_exit_task = None
    _litellm_stderr_task = None

    if _litellm_process and _litellm_process.returncode is None:
        logger.info("Stopping LiteLLM proxy", pid=_litellm_process.pid)
        _litellm_process.terminate()
        try:
            await asyncio.wait_for(_litellm_process.wait(), timeout=5.0)
        except TimeoutError:
            _litellm_process.kill()
            await _litellm_process.wait()

    _litellm_process = None
    _litellm_on_unhealthy = None
    _litellm_unhealthy_notified = False
    _set_litellm_status(
        state="stopped",
        pid=None,
        last_ready_at=None,
        consecutive_probe_failures=0,
        reason=None,
        exit_code=None,
    )


async def start_mcp_server() -> None:
    """Start the trusted MCP HTTP server on Unix socket."""
    global _mcp_server_task

    import uvicorn

    from tracecat.agent.common.config import TRUSTED_MCP_SOCKET_PATH
    from tracecat.agent.mcp.trusted_server import app

    socket_path = TRUSTED_MCP_SOCKET_PATH
    socket_path.parent.mkdir(parents=True, exist_ok=True)

    if socket_path.exists():
        socket_path.unlink()

    logger.info("Starting MCP server", socket_path=str(socket_path))
    uvicorn_config = uvicorn.Config(
        app,
        uds=str(socket_path),
        log_level="warning",
    )
    server = uvicorn.Server(uvicorn_config)
    _mcp_server_task = asyncio.create_task(server.serve())

    if not await _wait_for_socket(socket_path):
        await stop_mcp_server()
        raise RuntimeError(f"MCP server socket was not created at {socket_path}")

    os.chmod(str(socket_path), 0o600)
    logger.info("MCP server started", socket_path=str(socket_path))


async def stop_mcp_server() -> None:
    """Stop the MCP server."""
    global _mcp_server_task

    if _mcp_server_task:
        logger.info("Stopping MCP server")
        _mcp_server_task.cancel()
        try:
            await _mcp_server_task
        except asyncio.CancelledError:
            pass
        _mcp_server_task = None
