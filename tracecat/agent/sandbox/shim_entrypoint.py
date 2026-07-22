"""Standalone sandbox shim that starts Claude Code and proxies raw stdio.

This shim is intentionally self-contained and stdlib-only so broker-mode
startup does not depend on importing the wider Tracecat package tree or the
host virtualenv's Python dependencies.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import contextlib
import ctypes
import json
import logging
import os
import socket
import sys
from pathlib import Path
from typing import BinaryIO, NotRequired, TypedDict

LOGGER = logging.getLogger(__name__)

INIT_PAYLOAD_ENV_VAR = "TRACECAT__AGENT_INIT_PAYLOAD_PATH"
LLM_SOCKET_ENV_VAR = "TRACECAT__AGENT_LLM_SOCKET_PATH"
MCP_SOCKET_ENV_VAR = "TRACECAT__AGENT_MCP_SOCKET_PATH"
DEFAULT_AGENT_RUNTIME_DIR = Path("/run/tracecat")
DEFAULT_LLM_SOCKET_PATH = str(DEFAULT_AGENT_RUNTIME_DIR / "llm.sock")
DEFAULT_MCP_SOCKET_PATH = str(DEFAULT_AGENT_RUNTIME_DIR / "mcp.sock")
LLM_BRIDGE_HOST = "127.0.0.1"
TRUSTED_MCP_BRIDGE_PATH = "/mcp"
MAX_BODY_SIZE = 10 * 1024 * 1024
TOOL_LAUNCH_MODE = "--tracecat-tool-launch"
CAP_SETUID = 7
LINUX_CAPABILITY_VERSION_3 = 0x20080522
PR_CAP_AMBIENT = 47
PR_CAP_AMBIENT_CLEAR_ALL = 4
TOOL_ENVIRONMENT_BOUNDARY_KEYS = frozenset(
    {
        "PATH",
        "HOME",
        "XDG_CONFIG_HOME",
        "XDG_CACHE_HOME",
        "XDG_STATE_HOME",
        "TMPDIR",
        "TEMP",
        "TMP",
        "USER",
        "LOGNAME",
        "PWD",
    }
)


class _CapabilityHeader(ctypes.Structure):
    _fields_ = [("version", ctypes.c_uint32), ("pid", ctypes.c_int)]


class _CapabilityData(ctypes.Structure):
    _fields_ = [
        ("effective", ctypes.c_uint32),
        ("permitted", ctypes.c_uint32),
        ("inheritable", ctypes.c_uint32),
    ]


class ToolLaunchPayload(TypedDict):
    """Trusted wrapper payload for one demoted tool process."""

    argv: list[str]
    env: dict[str, str]


class ClaudeShimInitPayload(TypedDict):
    """Init payload consumed by the sandbox shim process."""

    command: list[str]
    env: dict[str, str]
    cwd: str
    mcp_bridge_port: int
    mcp_bridge_fd: NotRequired[int | None]


def _required_identity_env(name: str) -> int:
    value = os.environ.get(name)
    if value is None:
        raise RuntimeError(f"{name} is not set")
    try:
        parsed = int(value)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer") from exc
    if parsed < 0:
        raise RuntimeError(f"{name} must be non-negative")
    return parsed


def _process_status_fields() -> dict[str, str]:
    fields: dict[str, str] = {}
    for line in Path("/proc/self/status").read_text().splitlines():
        key, separator, value = line.partition(":")
        if separator:
            fields[key] = value.strip()
    return fields


def _capability_sets() -> dict[str, int]:
    status = _process_status_fields()
    return {
        field: int(status.get(field, "0"), 16)
        for field in ("CapInh", "CapPrm", "CapEff", "CapAmb")
    }


def _verify_claude_identity() -> None:
    """Verify nsjail retained only the capability needed by the trusted shim."""
    if os.environ.get("TRACECAT__DISABLE_NSJAIL") == "true":
        return

    claude_uid = _required_identity_env("TRACECAT__AGENT_CLAUDE_UID")
    shared_gid = _required_identity_env("TRACECAT__AGENT_SHARED_GID")
    if (os.getuid(), os.geteuid()) != (claude_uid, claude_uid):
        raise RuntimeError("Claude shim did not start as the configured Claude UID")
    if (os.getgid(), os.getegid()) != (shared_gid, shared_gid):
        raise RuntimeError("Claude shim did not start with the shared work GID")

    expected = 1 << CAP_SETUID
    capability_sets = _capability_sets()
    if any(value != expected for value in capability_sets.values()):
        raise RuntimeError(
            "Claude shim must start with only CAP_SETUID in every capability set"
        )
    if _process_status_fields().get("NoNewPrivs") != "1":
        raise RuntimeError("Claude shim must start with no_new_privs enabled")


def _clear_capability_sets() -> None:
    """Drop effective, permitted, inheritable, and ambient capabilities."""
    libc = ctypes.CDLL(None, use_errno=True)
    data = (_CapabilityData * 2)()
    header = _CapabilityHeader(version=LINUX_CAPABILITY_VERSION_3, pid=0)
    if libc.capset(ctypes.byref(header), ctypes.byref(data)) != 0:
        error_number = ctypes.get_errno()
        raise OSError(error_number, os.strerror(error_number))
    if libc.prctl(PR_CAP_AMBIENT, PR_CAP_AMBIENT_CLEAR_ALL, 0, 0, 0) != 0:
        error_number = ctypes.get_errno()
        raise OSError(error_number, os.strerror(error_number))


def _tool_environment(configured_env: dict[str, str]) -> dict[str, str]:
    tool_home = Path("/home/tools")
    env = {
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "HOME": str(tool_home),
        "XDG_CONFIG_HOME": str(tool_home / ".config"),
        "XDG_CACHE_HOME": str(tool_home / ".cache"),
        "XDG_STATE_HOME": str(tool_home / ".local/state"),
        "TMPDIR": str(tool_home / "tmp"),
        "TEMP": str(tool_home / "tmp"),
        "TMP": str(tool_home / "tmp"),
        "USER": "tools",
        "LOGNAME": "tools",
        "PWD": "/work",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONUNBUFFERED": "1",
    }
    for key, value in configured_env.items():
        if not key or "=" in key or "\x00" in key or "\x00" in value:
            raise RuntimeError("Tool environment contains an invalid entry")
        if key in TOOL_ENVIRONMENT_BOUNDARY_KEYS:
            raise RuntimeError(f"Tool environment cannot override {key}")
        env[key] = value
    return env


def _decode_tool_launch_payload(encoded_payload: str) -> ToolLaunchPayload:
    try:
        decoded = base64.urlsafe_b64decode(encoded_payload.encode("ascii"))
        raw_payload = json.loads(decoded)
    except (binascii.Error, ValueError, UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("Tool launch payload is malformed") from exc
    if not isinstance(raw_payload, dict):
        raise RuntimeError("Tool launch payload must be an object")
    argv = raw_payload.get("argv")
    env = raw_payload.get("env")
    if (
        not isinstance(argv, list)
        or not argv
        or not all(isinstance(value, str) and value for value in argv)
    ):
        raise RuntimeError("Tool launch argv must be a non-empty list[str]")
    if not isinstance(env, dict) or not all(
        isinstance(key, str) and isinstance(value, str) for key, value in env.items()
    ):
        raise RuntimeError("Tool launch env must be a dict[str, str]")
    return {"argv": argv, "env": env}


def _run_demoted_tool(encoded_payload: str) -> None:
    """Irreversibly demote and replace this process with a configured tool."""
    if os.environ.get("TRACECAT__DISABLE_NSJAIL") == "true":
        raise RuntimeError("UID-demoted tool launching is unavailable in direct mode")
    _verify_claude_identity()
    payload = _decode_tool_launch_payload(encoded_payload)
    tool_uid = _required_identity_env("TRACECAT__AGENT_TOOL_UID")
    shared_gid = _required_identity_env("TRACECAT__AGENT_SHARED_GID")

    os.setresuid(tool_uid, tool_uid, tool_uid)
    _clear_capability_sets()
    if (os.getuid(), os.geteuid(), os.getresuid()[2]) != (
        tool_uid,
        tool_uid,
        tool_uid,
    ):
        raise RuntimeError("Tool process did not irreversibly enter the tool UID")
    if (os.getgid(), os.getegid()) != (shared_gid, shared_gid):
        raise RuntimeError("Tool process lost the shared work GID")
    if any(_capability_sets().values()):
        raise RuntimeError("Tool process retained Linux capabilities")
    if _process_status_fields().get("NoNewPrivs") != "1":
        raise RuntimeError("Tool process must retain no_new_privs")

    tool_home = Path("/home/tools")
    for path in (
        tool_home / ".config",
        tool_home / ".cache",
        tool_home / ".local/state",
        tool_home / "tmp",
    ):
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chdir("/work")
    os.umask(0o002)
    os.execvpe(payload["argv"][0], payload["argv"], _tool_environment(payload["env"]))


def _prepare_claude_runtime_home() -> None:
    home = Path(os.environ["HOME"])
    for env_name in ("XDG_CONFIG_HOME", "XDG_CACHE_HOME", "XDG_STATE_HOME", "TMPDIR"):
        Path(os.environ.get(env_name, str(home))).mkdir(
            parents=True,
            exist_ok=True,
            mode=0o700,
        )
    os.umask(0o002)


class LLMBridge:
    """HTTP bridge that forwards localhost traffic to a Unix socket."""

    def __init__(
        self,
        *,
        socket_path: Path,
        port: int = 0,
        listener_fd: int | None = None,
    ) -> None:
        self.socket_path = socket_path
        self._requested_port = port
        self._listener_fd = listener_fd
        self._actual_port: int | None = None
        self._server: asyncio.Server | None = None
        self._serve_task: asyncio.Task[None] | None = None

    async def start(self) -> int:
        """Start the bridge and return the bound localhost port."""
        if self._listener_fd is None:
            self._server = await asyncio.start_server(
                self._handle_connection,
                host=LLM_BRIDGE_HOST,
                port=self._requested_port,
            )
        else:
            listener = socket.socket(fileno=self._listener_fd)
            listener.setblocking(False)
            self._server = await asyncio.start_server(
                self._handle_connection,
                sock=listener,
            )
        if not self._server.sockets:
            raise RuntimeError("LLM bridge did not expose a listening socket")
        actual_port = int(self._server.sockets[0].getsockname()[1])
        self._actual_port = actual_port
        self._serve_task = asyncio.create_task(self._server.serve_forever())
        self._serve_task.add_done_callback(self._on_serve_done)
        LOGGER.info(
            "LLM bridge started",
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
            LOGGER.info("LLM bridge stopped")

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
            LOGGER.error("LLM bridge server failed: %s", exc)

    async def _handle_connection(
        self,
        client_reader: asyncio.StreamReader,
        client_writer: asyncio.StreamWriter,
    ) -> None:
        """Forward one HTTP client connection to the Unix socket proxy."""
        try:
            request_data = await self._read_http_request(client_reader)
            if not request_data:
                return

            try:
                sock_reader, sock_writer = await asyncio.open_unix_connection(
                    str(self.socket_path)
                )
            except (OSError, ConnectionRefusedError) as exc:
                LOGGER.error("Failed to connect to LLM socket: %s", exc)
                await self._send_error_response(
                    client_writer,
                    status_code=502,
                    message="LLM proxy unavailable",
                )
                return

            try:
                sock_writer.write(request_data)
                await sock_writer.drain()
                await self._stream_response(sock_reader, client_writer)
            finally:
                sock_writer.close()
                await sock_writer.wait_closed()

        except asyncio.IncompleteReadError:
            LOGGER.debug("Client disconnected during request")
        except Exception as exc:
            LOGGER.exception("LLM bridge error: %s", exc)
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
            lower_line = line.lower()
            if lower_line.startswith(b"content-length:"):
                with contextlib.suppress(ValueError, IndexError):
                    content_length = int(line.split(b":", 1)[1].strip())
                break

        if content_length > MAX_BODY_SIZE:
            LOGGER.warning(
                "Request body too large",
                extra={
                    "content_length": content_length,
                    "max_size": MAX_BODY_SIZE,
                },
            )
            return None

        body = b""
        if content_length > 0:
            body = await reader.readexactly(content_length)
        return headers_data + body

    async def _stream_response(
        self,
        sock_reader: asyncio.StreamReader,
        client_writer: asyncio.StreamWriter,
    ) -> None:
        """Stream the Unix-socket response back to the HTTP client."""
        while chunk := await sock_reader.read(8192):
            client_writer.write(chunk)
            await client_writer.drain()

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


def _trusted_mcp_bridge_url(port: int) -> str:
    return f"http://{LLM_BRIDGE_HOST}:{port}{TRUSTED_MCP_BRIDGE_PATH}"


def _rewrite_mcp_bridge_command_port(
    command: list[str],
    *,
    requested_port: int,
    actual_port: int,
) -> list[str]:
    """Rewrite trusted MCP bridge URLs after dynamic port binding."""
    if requested_port == actual_port:
        return command

    requested_url = _trusted_mcp_bridge_url(requested_port)
    actual_url = _trusted_mcp_bridge_url(actual_port)
    return [arg.replace(requested_url, actual_url) for arg in command]


async def run_sandboxed_claude_shim() -> None:
    """Read shim config, start the LLM bridge, and proxy Claude stdio."""
    _verify_claude_identity()
    _prepare_claude_runtime_home()
    llm_bridge: LLMBridge | None = None
    mcp_bridge: LLMBridge | None = None
    process: asyncio.subprocess.Process | None = None
    stdout_task: asyncio.Task[None] | None = None
    stderr_task: asyncio.Task[None] | None = None

    try:
        init_payload = await _read_init_payload(_resolve_init_payload_path())
        llm_bridge = LLMBridge(socket_path=_resolve_llm_socket_path(), port=0)
        bridge_port = await llm_bridge.start()
        LOGGER.info("LLM bridge started for shim on port %s", bridge_port)
        mcp_bridge = LLMBridge(
            socket_path=_resolve_mcp_socket_path(),
            port=init_payload["mcp_bridge_port"],
            listener_fd=init_payload.get("mcp_bridge_fd"),
        )
        mcp_bridge_port = await mcp_bridge.start()
        LOGGER.info("MCP bridge started for shim on port %s", mcp_bridge_port)
        command = _rewrite_mcp_bridge_command_port(
            init_payload["command"],
            requested_port=init_payload["mcp_bridge_port"],
            actual_port=mcp_bridge_port,
        )

        child_env = {
            **os.environ,
            **init_payload["env"],
        }
        child_env["TRACECAT__LLM_BRIDGE_PORT"] = str(bridge_port)
        child_env["TRACECAT__MCP_BRIDGE_PORT"] = str(mcp_bridge_port)
        child_env["ANTHROPIC_BASE_URL"] = f"http://127.0.0.1:{bridge_port}"
        process = await asyncio.create_subprocess_exec(
            *command,
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

        return_code = await _wait_for_process_with_stdin(
            process,
            process.stdin,
        )
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
        if mcp_bridge is not None:
            await mcp_bridge.stop()


async def _read_init_payload(init_path: Path) -> ClaudeShimInitPayload:
    """Read the shim init payload from the mounted init file."""
    payload_bytes = await asyncio.to_thread(init_path.read_bytes)
    data = json.loads(payload_bytes)
    if not isinstance(data, dict):
        raise ValueError("Shim payload must be an object")

    command = data.get("command")
    env = data.get("env")
    cwd = data.get("cwd")
    mcp_bridge_port = data.get("mcp_bridge_port")
    mcp_bridge_fd = data.get("mcp_bridge_fd")

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
    if not isinstance(mcp_bridge_port, int) or mcp_bridge_port < 0:
        raise ValueError("Shim payload mcp_bridge_port must be a non-negative integer")
    if mcp_bridge_fd is not None and (
        not isinstance(mcp_bridge_fd, int) or mcp_bridge_fd < 0
    ):
        raise ValueError("Shim payload mcp_bridge_fd must be a non-negative integer")

    payload: ClaudeShimInitPayload = {
        "command": command,
        "env": env,
        "cwd": cwd,
        "mcp_bridge_port": mcp_bridge_port,
    }
    if mcp_bridge_fd is not None:
        payload["mcp_bridge_fd"] = mcp_bridge_fd
    return payload


def _resolve_init_payload_path() -> Path:
    """Resolve the init payload path from the spawn-provided env var."""
    if init_payload_path := os.environ.get(INIT_PAYLOAD_ENV_VAR):
        return Path(init_payload_path)
    raise RuntimeError(f"{INIT_PAYLOAD_ENV_VAR} is not set")


def _resolve_llm_socket_path() -> Path:
    """Resolve the mounted LLM socket path."""
    return Path(os.environ.get(LLM_SOCKET_ENV_VAR) or DEFAULT_LLM_SOCKET_PATH)


def _resolve_mcp_socket_path() -> Path:
    """Resolve the mounted trusted MCP socket path."""
    return Path(os.environ.get(MCP_SOCKET_ENV_VAR) or DEFAULT_MCP_SOCKET_PATH)


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


async def _pump_stdin_to_process(
    process_stdin: asyncio.StreamWriter,
) -> None:
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
    dst: BinaryIO,
) -> None:
    """Pump bytes from a subprocess pipe to a binary destination."""
    loop = asyncio.get_running_loop()
    while chunk := await reader.read(65536):
        await loop.run_in_executor(None, dst.write, chunk)
        await loop.run_in_executor(None, dst.flush)


def main() -> None:
    """CLI entry point for the sandbox shim."""
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    if len(sys.argv) == 3 and sys.argv[1] == TOOL_LAUNCH_MODE:
        _run_demoted_tool(sys.argv[2])
        return
    if len(sys.argv) != 1:
        raise RuntimeError("Unsupported sandbox shim invocation")
    asyncio.run(run_sandboxed_claude_shim())


if __name__ == "__main__":
    main()
