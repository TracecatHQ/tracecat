"""Runtime sidecars for agent execution workers."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import httpx

from tracecat.logger import logger

_litellm_process: asyncio.subprocess.Process | None = None
_litellm_stderr_task: asyncio.Task[None] | None = None
_mcp_server_task: asyncio.Task[None] | None = None


async def _stream_litellm_stderr(process: asyncio.subprocess.Process) -> None:
    """Stream LiteLLM stderr to logger."""
    if process.stderr is None:
        return
    try:
        async for line in process.stderr:
            decoded = line.decode("utf-8", errors="replace").rstrip()
            if decoded:
                logger.info("LiteLLM stderr", line=decoded)
    except Exception as e:
        logger.warning("LiteLLM stderr stream ended", error=str(e))


async def start_litellm_proxy() -> None:
    """Start the LiteLLM proxy subprocess."""
    global _litellm_process, _litellm_stderr_task

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

    logger.info("Starting LiteLLM proxy")
    cmd = [
        "litellm",
        "--port",
        "4000",
        "--config",
        str(runtime_config),
    ]

    env = os.environ.copy()
    pythonpath = env.get("PYTHONPATH", "")
    app_paths = "/app:/app/packages/tracecat-registry:/app/packages/tracecat-ee"
    env["PYTHONPATH"] = f"{app_paths}:{pythonpath}" if pythonpath else app_paths

    _litellm_process = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )
    _litellm_stderr_task = asyncio.create_task(_stream_litellm_stderr(_litellm_process))

    # Wait for LiteLLM to become ready before returning
    await _wait_for_litellm_ready()
    logger.info("LiteLLM proxy started")


async def _wait_for_litellm_ready(
    url: str = "http://127.0.0.1:4000/health/readiness",
    max_attempts: int = 60,
    interval: float = 0.5,
) -> None:
    """Poll LiteLLM health endpoint until it responds 200."""
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(connect=2.0, read=2.0, write=2.0, pool=2.0)
    ) as client:
        for attempt in range(1, max_attempts + 1):
            # Check if the process died while we're waiting
            if _litellm_process is not None and _litellm_process.returncode is not None:
                raise RuntimeError(
                    f"LiteLLM process exited with code {_litellm_process.returncode} during startup"
                )
            try:
                resp = await client.get(url)
                if resp.status_code == 200:
                    logger.info(
                        "LiteLLM readiness confirmed",
                        attempts=attempt,
                    )
                    return
            except httpx.ConnectError:
                pass
            except httpx.TimeoutException:
                pass
            if attempt % 10 == 0:
                logger.info(
                    "Waiting for LiteLLM to become ready",
                    attempt=attempt,
                    max_attempts=max_attempts,
                )
            await asyncio.sleep(interval)
    raise RuntimeError(f"LiteLLM did not become ready after {max_attempts} attempts")


async def stop_litellm_proxy() -> None:
    """Stop the LiteLLM proxy subprocess."""
    global _litellm_process, _litellm_stderr_task

    if _litellm_stderr_task:
        _litellm_stderr_task.cancel()
        try:
            await _litellm_stderr_task
        except asyncio.CancelledError:
            pass
        _litellm_stderr_task = None

    if _litellm_process and _litellm_process.returncode is None:
        logger.info("Stopping LiteLLM proxy")
        _litellm_process.terminate()
        try:
            await asyncio.wait_for(_litellm_process.wait(), timeout=5.0)
        except TimeoutError:
            _litellm_process.kill()
            await _litellm_process.wait()
        _litellm_process = None


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

    for _ in range(50):
        if socket_path.exists():
            break
        await asyncio.sleep(0.1)

    if socket_path.exists():
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
