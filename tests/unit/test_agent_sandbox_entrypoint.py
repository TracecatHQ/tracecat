from __future__ import annotations

import asyncio
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any, cast

import orjson
import pytest

from tracecat.agent.sandbox.shim_entrypoint import (
    DEFAULT_LLM_SOCKET_PATH,
    INIT_PAYLOAD_ENV_VAR,
    LLM_MAX_BODY_SIZE,
    LLM_SOCKET_ENV_VAR,
    SandboxSocketBridge,
    _pump_stdin_to_process,
    _read_stdin_chunk,
    _resolve_init_payload_path,
    _wait_for_process_with_stdin,
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
                "env": {"HOME": "/work/claude-home"},
                "cwd": "/work/claude-project",
            }
        )
    )

    payload = await _read_shim_init_payload(init_path)

    assert payload == {
        "command": ["claude", "--print"],
        "env": {"HOME": "/work/claude-home"},
        "cwd": "/work/claude-project",
    }


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

    async def fake_pump_stdin_to_process(_writer: asyncio.StreamWriter) -> None:
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
async def test_sandbox_socket_bridge_forwards_unchanged_without_hook(
    short_socket_dir: Path,
) -> None:
    """No before_forward hook means request bytes pass through unchanged."""
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
