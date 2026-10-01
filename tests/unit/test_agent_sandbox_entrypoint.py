from __future__ import annotations

import asyncio
import contextlib
import signal
import socket
import tempfile
import weakref
from collections.abc import Iterator
from pathlib import Path
from typing import Any, cast

import httpx
import orjson
import pytest

from tracecat.agent.sandbox.shim_entrypoint import (
    BRIDGE_HOST,
    DEFAULT_LLM_SOCKET_PATH,
    DEFAULT_MCP_SOCKET_PATH,
    HTTP_HEADER_LIMIT,
    INIT_PAYLOAD_ENV_VAR,
    LLM_MAX_BODY_SIZE,
    LLM_SOCKET_ENV_VAR,
    MCP_SOCKET_ENV_VAR,
    HTTPRequestError,
    SandboxSocketBridge,
    _forward_exit_code,
    _pump_stdin_to_process,
    _read_response_headers,
    _read_stdin_chunk,
    _resolve_init_payload_path,
    _resolve_mcp_socket_path,
    _rewrite_mcp_bridge_command_port,
    _wait_for_process_with_stdin,
    read_http_request,
    run_sandboxed_claude_shim,
)
from tracecat.agent.sandbox.shim_entrypoint import (
    _read_init_payload as _read_shim_init_payload,
)


def test_resolve_init_payload_path_direct_mode_uses_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    init_path = "/tmp/tracecat-agent/init.json"
    monkeypatch.setenv(INIT_PAYLOAD_ENV_VAR, init_path)

    assert _resolve_init_payload_path() == Path(init_path)


def test_resolve_init_payload_path_raises_without_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(INIT_PAYLOAD_ENV_VAR, raising=False)

    with pytest.raises(RuntimeError, match=f"{INIT_PAYLOAD_ENV_VAR} is not set"):
        _resolve_init_payload_path()


def test_read_stdin_chunk_uses_os_read(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, int] = {}

    def fake_fileno() -> int:
        return 42

    def fake_os_read(fd: int, chunk_size: int) -> bytes:
        captured["fd"] = fd
        captured["chunk_size"] = chunk_size
        return b'{"type":"control_request"}\n'

    monkeypatch.setattr("sys.stdin.fileno", fake_fileno)
    monkeypatch.setattr("os.read", fake_os_read)

    chunk = _read_stdin_chunk(65536)

    assert chunk == b'{"type":"control_request"}\n'
    assert captured == {"fd": 42, "chunk_size": 65536}


def test_llm_socket_path_falls_back_on_empty_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The shim resolves the LLM socket path with an env-var-then-default lookup."""
    import os

    monkeypatch.setenv(LLM_SOCKET_ENV_VAR, "")

    resolved = Path(os.environ.get(LLM_SOCKET_ENV_VAR) or DEFAULT_LLM_SOCKET_PATH)
    assert resolved == Path(DEFAULT_LLM_SOCKET_PATH)


def test_resolve_mcp_socket_path_falls_back_on_empty_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(MCP_SOCKET_ENV_VAR, "")

    assert _resolve_mcp_socket_path() == Path(DEFAULT_MCP_SOCKET_PATH)


@pytest.mark.anyio
async def test_sandbox_socket_bridge_ignores_expected_server_closed_error(
    caplog: pytest.LogCaptureFixture,
    tmp_path: Path,
) -> None:
    bridge = SandboxSocketBridge(
        socket_path=tmp_path / "llm.sock",
        max_body_size=LLM_MAX_BODY_SIZE,
        on_uds_failure="error",
        log_label="LLM bridge",
    )

    async def raise_server_closed() -> None:
        raise RuntimeError("server is closed")

    task = asyncio.create_task(raise_server_closed())
    await asyncio.sleep(0)

    with caplog.at_level("ERROR"):
        bridge._on_serve_done(task)

    assert "LLM bridge server failed" not in caplog.text


@pytest.mark.anyio
async def test_read_shim_init_payload_validates_shape(tmp_path: Path) -> None:
    init_path = tmp_path / "shim-init.json"
    init_path.write_bytes(
        orjson.dumps(
            {
                "command": ["claude", "--print"],
                "env": {"HOME": "/home/agent"},
                "cwd": "/work",
                "mcp_bridge_port": 4101,
            }
        )
    )

    payload = await _read_shim_init_payload(init_path)

    assert payload == {
        "command": ["claude", "--print"],
        "env": {"HOME": "/home/agent"},
        "cwd": "/work",
        "mcp_bridge_port": 4101,
    }


@pytest.mark.anyio
async def test_read_shim_init_payload_allows_port_zero(tmp_path: Path) -> None:
    init_path = tmp_path / "shim-init.json"
    init_path.write_bytes(
        orjson.dumps(
            {
                "command": ["claude", "--print"],
                "env": {"HOME": "/home/agent"},
                "cwd": "/work",
                "mcp_bridge_port": 0,
            }
        )
    )

    payload = await _read_shim_init_payload(init_path)

    assert payload["mcp_bridge_port"] == 0


@pytest.mark.anyio
async def test_read_shim_init_payload_accepts_inherited_mcp_bridge_fd(
    tmp_path: Path,
) -> None:
    init_path = tmp_path / "shim-init.json"
    init_path.write_bytes(
        orjson.dumps(
            {
                "command": ["claude", "--print"],
                "env": {"HOME": "/home/agent"},
                "cwd": "/work",
                "mcp_bridge_port": 54321,
                "mcp_bridge_fd": 42,
            }
        )
    )

    payload = await _read_shim_init_payload(init_path)

    assert payload["mcp_bridge_port"] == 54321
    assert payload.get("mcp_bridge_fd") == 42


@pytest.mark.anyio
async def test_llm_bridge_adopts_inherited_listener_fd(tmp_path: Path) -> None:
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    expected_port = int(listener.getsockname()[1])
    listener_fd = listener.detach()
    bridge = SandboxSocketBridge(
        socket_path=tmp_path / "mcp.sock",
        max_body_size=LLM_MAX_BODY_SIZE,
        on_uds_failure="error",
        log_label="MCP bridge",
        listener_fd=listener_fd,
    )

    actual_port = await bridge.start()
    try:
        assert actual_port == expected_port
    finally:
        await bridge.stop()


def test_rewrite_mcp_bridge_command_port_replaces_dynamic_urls() -> None:
    command = [
        "claude",
        "--mcp-config",
        '{"url":"http://127.0.0.1:0/mcp","other":"http://127.0.0.1:4101/mcp"}',
    ]

    rewritten = _rewrite_mcp_bridge_command_port(
        command,
        requested_port=0,
        actual_port=54321,
    )

    assert rewritten == [
        "claude",
        "--mcp-config",
        '{"url":"http://127.0.0.1:54321/mcp","other":"http://127.0.0.1:4101/mcp"}',
    ]


class _FakeStreamWriter:
    def __init__(self, *, fail_after_write: bool = False) -> None:
        self.fail_after_write = fail_after_write
        self.writes: list[bytes] = []
        self.closed = False

    def write(self, data: bytes) -> None:
        self.writes.append(data)

    async def drain(self) -> None:
        if self.fail_after_write:
            raise BrokenPipeError

    def close(self) -> None:
        self.closed = True

    async def wait_closed(self) -> None:
        return None


@pytest.mark.anyio
async def test_pump_stdin_to_process_suppresses_broken_pipe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    chunks = iter([b"hello", b""])

    async def fake_wait_for_stdin_chunk(chunk_size: int) -> bytes:
        assert chunk_size == 65536
        return next(chunks)

    monkeypatch.setattr(
        "tracecat.agent.sandbox.shim_entrypoint._wait_for_stdin_chunk",
        fake_wait_for_stdin_chunk,
    )
    writer = _FakeStreamWriter(fail_after_write=True)

    await _pump_stdin_to_process(cast(Any, writer))

    assert writer.writes == [b"hello"]
    assert writer.closed is True


@pytest.mark.anyio
async def test_pump_stdin_forwards_streamed_initialize_agent_mcp_urls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    chunks = iter(
        [
            b'{"type":"control_request","agents":{"analyst":{"mcpServers":[{"tracecat-registry-analyst":{"type":"http","url":"http://127.0.0.1:4101',
            b'/mcp","headers":{"Authorization":"Bearer token"}}}]}}}\n',
            b"",
        ]
    )

    async def fake_wait_for_stdin_chunk(chunk_size: int) -> bytes:
        assert chunk_size == 65536
        return next(chunks)

    monkeypatch.setattr(
        "tracecat.agent.sandbox.shim_entrypoint._wait_for_stdin_chunk",
        fake_wait_for_stdin_chunk,
    )
    writer = _FakeStreamWriter()

    await _pump_stdin_to_process(cast(Any, writer))

    forwarded = b"".join(writer.writes)
    assert b"http://127.0.0.1:4101/mcp" in forwarded
    assert writer.closed is True


class _FakeProcess:
    async def wait(self) -> int:
        await asyncio.sleep(0)
        return 7


@pytest.mark.anyio
async def test_wait_for_process_with_stdin_does_not_wait_for_stdin_eof(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stdin_started = asyncio.Event()
    stdin_cancelled = asyncio.Event()

    async def fake_pump_stdin_to_process(
        _writer: asyncio.StreamWriter,
        **_kwargs: Any,
    ) -> None:
        stdin_started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            stdin_cancelled.set()
            raise

    monkeypatch.setattr(
        "tracecat.agent.sandbox.shim_entrypoint._pump_stdin_to_process",
        fake_pump_stdin_to_process,
    )

    return_code = await _wait_for_process_with_stdin(
        cast(Any, _FakeProcess()),
        cast(Any, _FakeStreamWriter()),
    )

    assert return_code == 7
    assert stdin_started.is_set()
    assert stdin_cancelled.is_set()


@pytest.fixture
def short_socket_dir() -> Iterator[Path]:
    """Provide a short directory (under /tmp) for Unix socket binding.

    macOS limits AF_UNIX paths to ~104 chars; pytest's tmp_path is too deep.
    """
    with tempfile.TemporaryDirectory(prefix="tc-bridge-", dir="/tmp") as path:
        yield Path(path)


@pytest.mark.anyio
async def test_sandbox_socket_bridge_preserves_request_body(
    short_socket_dir: Path,
) -> None:
    """Connection normalization preserves the payload and its framing."""
    socket_path = short_socket_dir / "upstream.sock"
    received: list[bytes] = []

    async def handle_uds(
        reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        data = await reader.read(4096)
        received.append(data)
        writer.write(
            b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nok"
        )
        await writer.drain()
        writer.close()

    uds_server = await asyncio.start_unix_server(handle_uds, path=str(socket_path))
    try:
        bridge = SandboxSocketBridge(
            socket_path=socket_path,
            port=0,
            max_body_size=LLM_MAX_BODY_SIZE,
            on_uds_failure="error",
            log_label="LLM bridge",
        )
        port = await bridge.start()
        try:
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            writer.write(
                b"POST /v1/messages HTTP/1.1\r\n"
                b"Host: bridge\r\n"
                b"Content-Length: 4\r\n"
                b"\r\n"
                b"body"
            )
            await writer.drain()
            response = await reader.read(4096)
            writer.close()
            await writer.wait_closed()
        finally:
            await bridge.stop()
    finally:
        uds_server.close()
        await uds_server.wait_closed()

    assert response.startswith(b"HTTP/1.1 200 OK")
    assert received and received[0].endswith(b"\r\n\r\nbody")
    assert b"Content-Length: 4" in received[0]


@pytest.mark.anyio
async def test_bridge_stop_closes_connection_before_handler_starts(
    short_socket_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bridge = SandboxSocketBridge(
        socket_path=short_socket_dir / "upstream.sock",
        max_body_size=1024,
        on_uds_failure="error",
        log_label="MCP bridge",
    )
    accepted = asyncio.Event()
    shutdown: asyncio.Task[None] | None = None
    accepted_writer_ref: weakref.ReferenceType[asyncio.StreamWriter] | None = None
    accept_connection = bridge._accept_connection

    def accept(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        nonlocal shutdown, accepted_writer_ref
        accepted_writer_ref = weakref.ref(writer)
        # Queue shutdown before the new handler gets its first event-loop turn.
        shutdown = asyncio.create_task(bridge.stop())
        accept_connection(reader, writer)
        accepted.set()

    monkeypatch.setattr(bridge, "_accept_connection", accept)
    port = await bridge.start()
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    try:
        await asyncio.wait_for(accepted.wait(), 2)
        assert shutdown is not None
        await asyncio.wait_for(asyncio.shield(shutdown), 2)
        assert await asyncio.wait_for(reader.read(), 2) == b""
        assert not bridge._connections
    finally:
        # Release the socket even when the regression leaves shutdown blocked.
        if accepted_writer_ref is not None:
            if accepted_writer := accepted_writer_ref():
                accepted_writer.close()
        writer.close()
        await writer.wait_closed()
        if shutdown is not None:
            await asyncio.wait_for(shutdown, 2)
        await bridge.stop()


@pytest.mark.anyio
async def test_bridge_stop_closes_an_open_event_stream(short_socket_dir: Path) -> None:
    socket_path = short_socket_dir / "events.sock"
    upstream_closed = asyncio.Event()

    async def upstream(
        reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        try:
            await read_http_request(reader, max_body_size=1024)
            writer.write(
                b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\n"
                b"Transfer-Encoding: chunked\r\n\r\n"
            )
            await writer.drain()
            await reader.read()
        finally:
            writer.close()
            await writer.wait_closed()
            upstream_closed.set()

    server = await asyncio.start_unix_server(upstream, path=str(socket_path))
    bridge = SandboxSocketBridge(
        socket_path=socket_path,
        max_body_size=1024,
        on_uds_failure="error",
        log_label="MCP bridge",
    )
    port = await bridge.start()
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    try:
        writer.write(b"GET /mcp HTTP/1.1\r\nHost: bridge\r\n\r\n")
        await writer.drain()
        await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 2)
        await asyncio.wait_for(bridge.stop(), 2)
        assert await asyncio.wait_for(reader.read(), 2) == b""
        await asyncio.wait_for(upstream_closed.wait(), 2)
    finally:
        writer.close()
        await writer.wait_closed()
        await bridge.stop()
        server.close()
        await server.wait_closed()


@pytest.mark.anyio
@pytest.mark.parametrize(
    "response",
    [
        b"HTTP/1.1 200 OK\r\n",  # Truncated headers.
        b"not HTTP\r\n\r\n",
        b"HTTP/1.1 101 Switching Protocols\r\n\r\n",
        b"HTTP/1.1 000 Invalid\r\n\r\n",
        b"HTTP/1.1 200 OK\r\nBad header\r\n\r\n",
        b"HTTP/1.1 200 OK\r\nX-Large: " + b"a" * HTTP_HEADER_LIMIT + b"\r\n\r\n",
    ],
)
async def test_bridge_rejects_invalid_upstream_headers(response: bytes) -> None:
    reader = asyncio.StreamReader()
    reader.feed_data(response)
    reader.feed_eof()

    with pytest.raises(HTTPRequestError) as error:
        await _read_response_headers(reader)
    assert error.value.status_code == 502


@pytest.mark.anyio
@pytest.mark.parametrize("chunked", [False, True])
@pytest.mark.parametrize("response_connection", [b"", b"Connection: keep-alive\r\n"])
async def test_bridge_reconnects_between_complete_streamed_responses(
    short_socket_dir: Path, chunked: bool, response_connection: bytes
) -> None:
    """A pooling client receives each complete stream and reconnects promptly."""
    socket_path = short_socket_dir / "persistent.sock"
    release_body = asyncio.Event()
    requests: list[bytes] = []
    handlers: set[asyncio.Task[None]] = set()
    first, last = b"data: first\n\n", b"data: last\n\n"

    async def upstream(
        reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        task = asyncio.current_task()
        assert task is not None
        handlers.add(task)
        try:
            while request := await read_http_request(reader, max_body_size=1024):
                headers, body = request
                requests.append(body)
                framing = (
                    b"Transfer-Encoding: chunked\r\n"
                    if chunked
                    else f"Content-Length: {len(first) + len(last)}\r\n".encode()
                )
                # Exercise interim headers as well as the final response.
                writer.write(b"HTTP/1.1 103 Early Hints\r\n\r\n")
                writer.write(
                    b"HTTP/1.1 200 OK\r\n"
                    + framing
                    + response_connection
                    + b"Content-Type: text/event-stream\r\n\r\n"
                )
                writer.write(
                    f"{len(first):x}\r\n".encode() + first + b"\r\n"
                    if chunked
                    else first
                )
                await writer.drain()
                await release_body.wait()
                writer.write(
                    f"{len(last):x}\r\n".encode() + last + b"\r\n0\r\n\r\n"
                    if chunked
                    else last
                )
                await writer.drain()
                if b"connection: close\r\n" in headers.lower():
                    break
        finally:
            writer.close()
            with contextlib.suppress(ConnectionError):
                await writer.wait_closed()
            handlers.discard(task)

    server = await asyncio.start_unix_server(upstream, path=str(socket_path))
    bridge = SandboxSocketBridge(
        socket_path=socket_path,
        max_body_size=1024,
        on_uds_failure="error",
        log_label="MCP bridge",
    )
    port = await bridge.start()
    try:
        async with httpx.AsyncClient(timeout=2) as client:
            for index in range(3):
                release_body.clear()
                async with client.stream(
                    "POST",
                    f"http://127.0.0.1:{port}/mcp",
                    content=str(index).encode(),
                    headers={"Connection": "keep-alive", "Keep-Alive": "timeout=60"},
                ) as response:
                    assert response.status_code == 200
                    assert response.headers["connection"] == "close"
                    assert "keep-alive" not in response.headers
                    chunks = response.aiter_bytes()
                    # The first chunk must arrive before the upstream completes.
                    assert await anext(chunks) == first
                    release_body.set()
                    assert b"".join([chunk async for chunk in chunks]) == last
        assert requests == [b"0", b"1", b"2"]
    finally:
        release_body.set()
        server.close()
        pending = list(handlers)
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        await server.wait_closed()
        await bridge.stop()


@pytest.mark.anyio
async def test_sandbox_socket_bridge_returns_502_on_uds_failure_in_error_mode(
    short_socket_dir: Path,
) -> None:
    """on_uds_failure='error' surfaces 502 to the client when the UDS is missing."""
    bridge = SandboxSocketBridge(
        socket_path=short_socket_dir / "missing.sock",
        port=0,
        max_body_size=LLM_MAX_BODY_SIZE,
        on_uds_failure="error",
        log_label="LLM bridge",
    )
    port = await bridge.start()
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        writer.write(
            b"POST /v1/messages HTTP/1.1\r\nHost: bridge\r\nContent-Length: 0\r\n\r\n"
        )
        await writer.drain()
        response = await reader.read(4096)
        writer.close()
        await writer.wait_closed()
    finally:
        await bridge.stop()

    assert response.startswith(b"HTTP/1.1 502")


@pytest.mark.anyio
async def test_sandbox_socket_bridge_drops_silently_on_uds_failure_in_drop_mode(
    short_socket_dir: Path,
) -> None:
    """on_uds_failure='drop' closes the connection without an error response."""
    bridge = SandboxSocketBridge(
        socket_path=short_socket_dir / "missing.sock",
        port=0,
        max_body_size=LLM_MAX_BODY_SIZE,
        on_uds_failure="drop",
        log_label="OTel bridge",
    )
    port = await bridge.start()
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        writer.write(
            b"POST /v1/traces HTTP/1.1\r\nHost: bridge\r\nContent-Length: 0\r\n\r\n"
        )
        await writer.drain()
        response = await reader.read(4096)
        writer.close()
        await writer.wait_closed()
    finally:
        await bridge.stop()

    # drop mode: no HTTP response written, connection just closes.
    assert response == b""


@pytest.mark.parametrize(
    "otel_port",
    [
        pytest.param(None, id="start-failure"),
        pytest.param(4318, id="start-success"),
    ],
)
@pytest.mark.anyio
async def test_shim_sets_otel_env_only_after_drop_mode_bridge_starts(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    otel_port: int | None,
) -> None:
    """The child gets local OTel routing only after the drop-mode bridge starts."""
    init_path = tmp_path / "shim-init.json"
    init_path.write_bytes(
        orjson.dumps(
            {
                "command": ["claude", "--print"],
                "env": {"CLAUDE_CODE_ENABLE_TELEMETRY": "1"},
                "cwd": str(tmp_path),
                "mcp_bridge_port": 4101,
            }
        )
    )
    monkeypatch.setenv(INIT_PAYLOAD_ENV_VAR, str(init_path))
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    captured_env: dict[str, str] = {}
    bridges: list[SandboxSocketBridge] = []

    class FakeProcess:
        def __init__(self) -> None:
            self.stdin = cast(asyncio.StreamWriter, object())
            self.stdout = cast(asyncio.StreamReader, object())
            self.stderr = cast(asyncio.StreamReader, object())
            self.returncode: int | None = 0

        def terminate(self) -> None:
            raise AssertionError("completed process should not be terminated")

        async def wait(self) -> int:
            return 0

    async def fake_bridge_start(bridge: SandboxSocketBridge) -> int:
        bridges.append(bridge)
        if bridge._log_label == "LLM bridge":
            return 4100
        if bridge._log_label == "MCP bridge":
            return 4101
        assert bridge._log_label == "OTel bridge"
        if otel_port is None:
            raise OSError("synthetic bind failure")
        return otel_port

    async def fake_bridge_stop(_bridge: SandboxSocketBridge) -> None:
        return None

    async def fake_create_subprocess_exec(
        *_command: str,
        stdin: int | None = None,
        stdout: int | None = None,
        stderr: int | None = None,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
    ) -> asyncio.subprocess.Process:
        del stdin, stdout, stderr, cwd
        assert env is not None
        captured_env.update(env)
        return cast(asyncio.subprocess.Process, FakeProcess())

    async def fake_pump_stream(
        _reader: asyncio.StreamReader,
        _dst: Any,
    ) -> None:
        return None

    async def fake_wait_for_process_with_stdin(
        _process: asyncio.subprocess.Process,
        _process_stdin: asyncio.StreamWriter,
    ) -> int:
        return 0

    monkeypatch.setattr(SandboxSocketBridge, "start", fake_bridge_start)
    monkeypatch.setattr(SandboxSocketBridge, "stop", fake_bridge_stop)
    monkeypatch.setattr(
        "tracecat.agent.sandbox.shim_entrypoint.asyncio.create_subprocess_exec",
        fake_create_subprocess_exec,
    )
    monkeypatch.setattr(
        "tracecat.agent.sandbox.shim_entrypoint._pump_stream",
        fake_pump_stream,
    )
    monkeypatch.setattr(
        "tracecat.agent.sandbox.shim_entrypoint._wait_for_process_with_stdin",
        fake_wait_for_process_with_stdin,
    )

    await run_sandboxed_claude_shim()

    otel_bridges = [bridge for bridge in bridges if bridge._log_label == "OTel bridge"]
    assert len(otel_bridges) == 1
    assert otel_bridges[0]._on_uds_failure == "drop"
    if otel_port is None:
        assert "CLAUDE_CODE_ENABLE_TELEMETRY" not in captured_env
        assert "OTEL_EXPORTER_OTLP_ENDPOINT" not in captured_env
    else:
        assert captured_env["OTEL_EXPORTER_OTLP_ENDPOINT"] == (
            f"http://{BRIDGE_HOST}:{otel_port}"
        )


@pytest.mark.parametrize(
    ("return_code", "expected"),
    [
        pytest.param(0, 0, id="clean-exit"),
        pytest.param(1, 1, id="nonzero-exit-passes-through"),
        pytest.param(-signal.SIGABRT, 134, id="sigabrt-forwards-as-134"),
        pytest.param(-signal.SIGKILL, 137, id="sigkill-forwards-as-137"),
    ],
)
def test_forward_exit_code_maps_signal_death_to_nsjail_contract(
    return_code: int,
    expected: int,
) -> None:
    """Invariant: the shim forwards the Claude child's death as ``128 + signal``.

    Without this the shim exits 1 for every failure and the host can never
    observe which signal killed the jailed runtime.
    """
    assert _forward_exit_code(return_code) == expected


@pytest.mark.anyio
async def test_mcp_bridge_forwards_catalog_sized_auth_header(
    short_socket_dir: Path,
) -> None:
    socket_path = short_socket_dir / "catalog.sock"
    request = (
        b"POST /mcp HTTP/1.1\r\nAuthorization: Bearer "
        + b"x" * 200_000
        + b"\r\nContent-Length: 0\r\n\r\n"
    )
    received: list[bytes] = []

    async def upstream(
        reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        received.append(await reader.readexactly(len(request)))
        writer.write(
            b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nok"
        )
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    server = await asyncio.start_unix_server(upstream, path=str(socket_path))
    bridge = SandboxSocketBridge(
        socket_path=socket_path,
        max_body_size=LLM_MAX_BODY_SIZE,
        max_header_size=1024 * 1024,
        on_uds_failure="error",
        log_label="MCP bridge",
    )
    try:
        port = await bridge.start()
        async with asyncio.timeout(5):
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            writer.write(request)
            await writer.drain()
            assert (await reader.read()).endswith(b"ok")
            writer.close()
            await writer.wait_closed()
        assert received == [request]
    finally:
        await bridge.stop()
        server.close()
        await server.wait_closed()
