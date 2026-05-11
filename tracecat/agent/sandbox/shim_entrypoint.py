"""Standalone sandbox shim that starts Claude Code and proxies raw stdio.

This shim is intentionally self-contained and stdlib-only so broker-mode
startup does not depend on importing the wider Tracecat package tree or the
host virtualenv's Python dependencies.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import sys
from pathlib import Path
from typing import BinaryIO, Literal, TypedDict

LOGGER = logging.getLogger(__name__)

INIT_PAYLOAD_ENV_VAR = "TRACECAT__AGENT_INIT_PAYLOAD_PATH"
LLM_SOCKET_ENV_VAR = "TRACECAT__AGENT_LLM_SOCKET_PATH"
DEFAULT_LLM_SOCKET_PATH = "/var/run/tracecat/llm.sock"
OTEL_SOCKET_ENV_VAR = "TRACECAT__AGENT_OTEL_SOCKET_PATH"
DEFAULT_OTEL_SOCKET_PATH = "/var/run/tracecat/otel.sock"
BRIDGE_HOST = "127.0.0.1"
LLM_MAX_BODY_SIZE = 10 * 1024 * 1024
OTEL_MAX_BODY_SIZE = 16 * 1024 * 1024


class ClaudeShimInitPayload(TypedDict):
    """Init payload consumed by the sandbox shim process."""

    command: list[str]
    env: dict[str, str]
    cwd: str


class SandboxSocketBridge:
    """Pure byte-pipe: 127.0.0.1:<port> -> UDS at ``socket_path``.

    Holds no credentials and applies no policy beyond the body cap and
    UDS-failure mode passed at construction.
    """

    def __init__(
        self,
        *,
        socket_path: Path,
        port: int = 0,
        max_body_size: int,
        on_uds_failure: Literal["error", "drop"],
        log_label: str,
    ) -> None:
        self.socket_path = socket_path
        self._requested_port = port
        self._max_body_size = max_body_size
        self._on_uds_failure = on_uds_failure
        self._log_label = log_label
        self._actual_port: int | None = None
        self._server: asyncio.Server | None = None
        self._serve_task: asyncio.Task[None] | None = None

    async def start(self) -> int:
        """Start the bridge and return the bound localhost port."""
        self._server = await asyncio.start_server(
            self._handle_connection,
            host=BRIDGE_HOST,
            port=self._requested_port,
        )
        if not self._server.sockets:
            raise RuntimeError(f"{self._log_label} did not expose a listening socket")
        actual_port = int(self._server.sockets[0].getsockname()[1])
        self._actual_port = actual_port
        self._serve_task = asyncio.create_task(self._server.serve_forever())
        self._serve_task.add_done_callback(self._on_serve_done)
        LOGGER.info(
            "%s started",
            self._log_label,
            extra={
                "requested_port": self._requested_port,
                "actual_port": actual_port,
                "socket_path": str(self.socket_path),
            },
        )
        return actual_port

    async def stop(self) -> None:
        """Stop the bridge server."""
        if self._serve_task is not None:
            if not self._serve_task.done():
                self._serve_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await self._serve_task
            self._serve_task = None
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
            LOGGER.info("%s stopped", self._log_label)

    def _on_serve_done(self, task: asyncio.Task[None]) -> None:
        """Log unexpected bridge task failures."""
        if task.cancelled():
            return
        try:
            exc = task.exception()
        except asyncio.CancelledError:
            return
        if isinstance(exc, RuntimeError) and str(exc) == "server is closed":
            return
        if exc:
            LOGGER.error("%s server failed: %s", self._log_label, exc)

    async def _handle_connection(
        self,
        client_reader: asyncio.StreamReader,
        client_writer: asyncio.StreamWriter,
    ) -> None:
        """Forward one HTTP client connection to the Unix socket."""
        try:
            request_data = await self._read_http_request(client_reader)
            if not request_data:
                return

            try:
                sock_reader, sock_writer = await asyncio.open_unix_connection(
                    str(self.socket_path)
                )
            except (OSError, ConnectionRefusedError) as exc:
                await self._handle_uds_failure(client_writer, exc)
                return

            try:
                sock_writer.write(request_data)
                await sock_writer.drain()
                while chunk := await sock_reader.read(8192):
                    client_writer.write(chunk)
                    await client_writer.drain()
            finally:
                sock_writer.close()
                with contextlib.suppress(Exception):
                    await sock_writer.wait_closed()

        except asyncio.IncompleteReadError:
            LOGGER.debug("%s client disconnected during request", self._log_label)
        except Exception as exc:
            LOGGER.warning("%s error: %s", self._log_label, exc)
            if self._on_uds_failure == "error":
                with contextlib.suppress(Exception):
                    await self._send_error_response(
                        client_writer,
                        status_code=500,
                        message="Internal bridge error",
                    )
        finally:
            with contextlib.suppress(Exception):
                client_writer.close()
                await client_writer.wait_closed()

    async def _handle_uds_failure(
        self,
        client_writer: asyncio.StreamWriter,
        exc: Exception,
    ) -> None:
        if self._on_uds_failure == "error":
            LOGGER.error("%s failed to connect to UDS: %s", self._log_label, exc)
            await self._send_error_response(
                client_writer,
                status_code=502,
                message="Upstream socket unavailable",
            )
        else:
            LOGGER.warning("%s failed to connect to UDS: %s", self._log_label, exc)

    async def _read_http_request(self, reader: asyncio.StreamReader) -> bytes | None:
        """Read a full HTTP request including headers and optional body."""
        headers_data = b""
        while True:
            line = await reader.readline()
            if not line:
                return None
            headers_data += line
            if line == b"\r\n":
                break

        content_length = 0
        for line in headers_data.split(b"\r\n"):
            if line.lower().startswith(b"content-length:"):
                with contextlib.suppress(ValueError, IndexError):
                    content_length = int(line.split(b":", 1)[1].strip())
                break

        if content_length > self._max_body_size:
            LOGGER.warning(
                "%s request body too large",
                self._log_label,
                extra={
                    "content_length": content_length,
                    "max_size": self._max_body_size,
                },
            )
            return None

        body = b""
        if content_length > 0:
            body = await reader.readexactly(content_length)
        return headers_data + body

    async def _send_error_response(
        self,
        writer: asyncio.StreamWriter,
        *,
        status_code: int,
        message: str,
    ) -> None:
        """Send a minimal JSON HTTP error response."""
        status_messages = {
            500: "Internal Server Error",
            502: "Bad Gateway",
            503: "Service Unavailable",
        }
        status_text = status_messages.get(status_code, "Error")
        body = json.dumps({"error": message})
        response = (
            f"HTTP/1.1 {status_code} {status_text}\r\n"
            "Content-Type: application/json\r\n"
            f"Content-Length: {len(body)}\r\n"
            "Connection: close\r\n"
            "\r\n"
            f"{body}"
        )
        writer.write(response.encode())
        await writer.drain()


async def run_sandboxed_claude_shim() -> None:
    """Read shim config, start the LLM bridge, and proxy Claude stdio."""
    llm_bridge: SandboxSocketBridge | None = None
    otel_bridge: SandboxSocketBridge | None = None
    process: asyncio.subprocess.Process | None = None
    stdout_task: asyncio.Task[None] | None = None
    stderr_task: asyncio.Task[None] | None = None

    try:
        init_payload = await _read_init_payload(_resolve_init_payload_path())
        llm_socket = Path(os.environ.get(LLM_SOCKET_ENV_VAR) or DEFAULT_LLM_SOCKET_PATH)
        llm_bridge = SandboxSocketBridge(
            socket_path=llm_socket,
            port=0,
            max_body_size=LLM_MAX_BODY_SIZE,
            on_uds_failure="error",
            log_label="LLM bridge",
        )
        bridge_port = await llm_bridge.start()

        child_env = {**os.environ, **init_payload["env"]}
        child_env["TRACECAT__LLM_BRIDGE_PORT"] = str(bridge_port)
        child_env["ANTHROPIC_BASE_URL"] = f"http://{BRIDGE_HOST}:{bridge_port}"

        # Telemetry path: start the OTel bridge before spawning Claude so the
        # SDK's exporters connect to the bridge instead of leaving the sandbox.
        # The host-side relay holds tenant headers and the real collector URL.
        if child_env.get("CLAUDE_CODE_ENABLE_TELEMETRY") == "1":
            otel_socket = Path(
                os.environ.get(OTEL_SOCKET_ENV_VAR) or DEFAULT_OTEL_SOCKET_PATH
            )
            otel_bridge = SandboxSocketBridge(
                socket_path=otel_socket,
                port=0,
                max_body_size=OTEL_MAX_BODY_SIZE,
                on_uds_failure="drop",
                log_label="OTel bridge",
            )
            otel_port = await otel_bridge.start()
            child_env["OTEL_EXPORTER_OTLP_ENDPOINT"] = (
                f"http://{BRIDGE_HOST}:{otel_port}"
            )

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

        return_code = await _wait_for_process_with_stdin(process, process.stdin)
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
        if otel_bridge is not None:
            await otel_bridge.stop()


async def _read_init_payload(init_path: Path) -> ClaudeShimInitPayload:
    """Read the shim init payload from the mounted init file."""

    def _read_bytes() -> bytes:
        return init_path.read_bytes()

    payload_bytes = await asyncio.to_thread(_read_bytes)
    data = json.loads(payload_bytes)
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

    return {
        "command": command,
        "env": env,
        "cwd": cwd,
    }


def _resolve_init_payload_path() -> Path:
    """Resolve the init payload path from the spawn-provided env var."""
    if init_payload_path := os.environ.get(INIT_PAYLOAD_ENV_VAR):
        return Path(init_payload_path)
    raise RuntimeError(f"{INIT_PAYLOAD_ENV_VAR} is not set")


async def _wait_for_process_with_stdin(
    process: asyncio.subprocess.Process,
    process_stdin: asyncio.StreamWriter,
) -> int:
    """Proxy stdin while waiting for the child process to exit."""
    stdin_task = asyncio.create_task(_pump_stdin_to_process(process_stdin))
    try:
        return await process.wait()
    finally:
        if not stdin_task.done():
            stdin_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await stdin_task


async def _pump_stdin_to_process(process_stdin: asyncio.StreamWriter) -> None:
    """Proxy shim stdin into the Claude subprocess stdin."""
    try:
        while chunk := await _wait_for_stdin_chunk(65536):
            try:
                process_stdin.write(chunk)
                await process_stdin.drain()
            except (BrokenPipeError, ConnectionResetError):
                break
    finally:
        process_stdin.close()
        with contextlib.suppress(Exception):
            await process_stdin.wait_closed()


async def _wait_for_stdin_chunk(chunk_size: int) -> bytes:
    """Wait until shim stdin is readable and return one chunk."""
    loop = asyncio.get_running_loop()
    fd = sys.stdin.fileno()
    ready: asyncio.Future[None] = loop.create_future()

    def mark_ready() -> None:
        if not ready.done():
            ready.set_result(None)

    loop.add_reader(fd, mark_ready)
    try:
        await ready
    finally:
        loop.remove_reader(fd)
    return _read_stdin_chunk(chunk_size)


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
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    asyncio.run(run_sandboxed_claude_shim())


if __name__ == "__main__":
    main()
