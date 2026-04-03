"""Thin sandbox shim that starts Claude Code and proxies raw stdio."""

from __future__ import annotations

import asyncio
import contextlib
import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING, BinaryIO

import orjson

from tracecat.agent.common.config import TRACECAT__AGENT_LLM_SOCKET_PATH
from tracecat.agent.sandbox.entrypoint import INIT_PAYLOAD_ENV_VAR
from tracecat.agent.sandbox.llm_bridge import LLMBridge
from tracecat.logger import logger

if TYPE_CHECKING:
    from tracecat.agent.runtime.claude_code.transport import ClaudeShimInitPayload


async def run_sandboxed_claude_shim() -> None:
    """Read shim config, start the LLM bridge, and proxy Claude stdio."""
    llm_bridge: LLMBridge | None = None
    process: asyncio.subprocess.Process | None = None
    stdout_task: asyncio.Task[None] | None = None
    stderr_task: asyncio.Task[None] | None = None

    try:
        init_payload = await _read_init_payload(_resolve_init_payload_path())
        llm_bridge = LLMBridge(
            socket_path=TRACECAT__AGENT_LLM_SOCKET_PATH,
            port=0,
        )
        bridge_port = await llm_bridge.start()
        logger.info("LLM bridge started for shim", port=bridge_port)

        child_env = {**os.environ, **init_payload["env"]}
        child_env["TRACECAT__LLM_BRIDGE_PORT"] = str(bridge_port)
        child_env["ANTHROPIC_BASE_URL"] = f"http://127.0.0.1:{bridge_port}"
        process = await asyncio.create_subprocess_exec(
            *init_payload["command"],
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=init_payload["cwd"],
            env=child_env,
        )
        if process.stdin is None or process.stdout is None or process.stderr is None:
            raise RuntimeError("Claude subprocess stdio pipes were not created")

        stdout_task = asyncio.create_task(
            _pump_stream(process.stdout, sys.stdout.buffer)
        )
        stderr_task = asyncio.create_task(
            _pump_stream(process.stderr, sys.stderr.buffer)
        )

        await _pump_stdin_to_process(process.stdin)
        return_code = await process.wait()
        await stdout_task
        await stderr_task
        if return_code != 0:
            raise RuntimeError(f"Claude subprocess exited with code {return_code}")

    finally:
        if stdout_task is not None and not stdout_task.done():
            stdout_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await stdout_task
        if stderr_task is not None and not stderr_task.done():
            stderr_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await stderr_task
        if process is not None and process.returncode is None:
            process.terminate()
            with contextlib.suppress(Exception):
                await process.wait()
        if llm_bridge is not None:
            await llm_bridge.stop()


async def _read_init_payload(init_path: Path) -> ClaudeShimInitPayload:
    """Read the shim init payload from the mounted init file."""

    def _read_bytes() -> bytes:
        return init_path.read_bytes()

    payload_bytes = await asyncio.to_thread(_read_bytes)
    data = orjson.loads(payload_bytes)
    if not isinstance(data, dict):
        raise ValueError("Shim payload must be an object")

    command = data.get("command")
    env = data.get("env")
    cwd = data.get("cwd")

    if not isinstance(command, list) or not all(
        isinstance(item, str) for item in command
    ):
        raise ValueError("Shim payload command must be a list[str]")
    if not isinstance(env, dict) or not all(
        isinstance(key, str) and isinstance(value, str) for key, value in env.items()
    ):
        raise ValueError("Shim payload env must be a dict[str, str]")
    if not isinstance(cwd, str):
        raise ValueError("Shim payload cwd must be a string")

    return {"command": command, "env": env, "cwd": cwd}


def _resolve_init_payload_path() -> Path:
    """Resolve the init payload path from the spawn-provided env var."""
    if init_payload_path := os.environ.get(INIT_PAYLOAD_ENV_VAR):
        return Path(init_payload_path)
    raise RuntimeError(f"{INIT_PAYLOAD_ENV_VAR} is not set")


async def _pump_stdin_to_process(process_stdin: asyncio.StreamWriter) -> None:
    """Proxy shim stdin into the Claude subprocess stdin."""
    loop = asyncio.get_running_loop()
    while chunk := await loop.run_in_executor(None, _read_stdin_chunk, 65536):
        process_stdin.write(chunk)
        await process_stdin.drain()
    process_stdin.close()
    with contextlib.suppress(Exception):
        await process_stdin.wait_closed()


def _read_stdin_chunk(chunk_size: int) -> bytes:
    """Read one available chunk from shim stdin without waiting for EOF.

    Args:
        chunk_size: Maximum number of bytes to read from stdin.

    Returns:
        The next available stdin bytes, or `b""` on EOF.
    """
    return os.read(sys.stdin.fileno(), chunk_size)


async def _pump_stream(
    reader: asyncio.StreamReader,
    destination: BinaryIO,
) -> None:
    """Pump bytes from a subprocess pipe to a binary destination."""
    loop = asyncio.get_running_loop()
    writer = destination.write
    flush = destination.flush
    while chunk := await reader.read(65536):
        await loop.run_in_executor(None, writer, chunk)
        await loop.run_in_executor(None, flush)


def main() -> None:
    """CLI entry point for the sandbox shim."""
    asyncio.run(run_sandboxed_claude_shim())


if __name__ == "__main__":
    main()
