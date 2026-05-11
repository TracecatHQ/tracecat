from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, cast

import orjson
import pytest

from tracecat.agent.sandbox.shim_entrypoint import (
    DEFAULT_LLM_SOCKET_PATH,
    DEFAULT_MCP_SOCKET_PATH,
    INIT_PAYLOAD_ENV_VAR,
    MCP_SOCKET_ENV_VAR,
    LLMBridge,
    _pump_stdin_to_process,
    _read_stdin_chunk,
    _resolve_init_payload_path,
    _resolve_llm_socket_path,
    _resolve_mcp_socket_path,
    _rewrite_mcp_bridge_command_port,
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


def test_resolve_llm_socket_path_falls_back_on_empty_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TRACECAT__AGENT_LLM_SOCKET_PATH", "")

    assert _resolve_llm_socket_path() == Path(DEFAULT_LLM_SOCKET_PATH)


def test_resolve_mcp_socket_path_falls_back_on_empty_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(MCP_SOCKET_ENV_VAR, "")

    assert _resolve_mcp_socket_path() == Path(DEFAULT_MCP_SOCKET_PATH)


@pytest.mark.anyio
async def test_llm_bridge_ignores_expected_server_closed_error(
    caplog: pytest.LogCaptureFixture,
    tmp_path: Path,
) -> None:
    bridge = LLMBridge(socket_path=tmp_path / "llm.sock")

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
                "mcp_bridge_port": 4101,
            }
        )
    )

    payload = await _read_shim_init_payload(init_path)

    assert payload == {
        "command": ["claude", "--print"],
        "env": {"HOME": "/work/claude-home"},
        "cwd": "/work/claude-project",
        "mcp_bridge_port": 4101,
    }


@pytest.mark.anyio
async def test_read_shim_init_payload_allows_port_zero(tmp_path: Path) -> None:
    init_path = tmp_path / "shim-init.json"
    init_path.write_bytes(
        orjson.dumps(
            {
                "command": ["claude", "--print"],
                "env": {"HOME": "/work/claude-home"},
                "cwd": "/work/claude-project",
                "mcp_bridge_port": 0,
            }
        )
    )

    payload = await _read_shim_init_payload(init_path)

    assert payload["mcp_bridge_port"] == 0


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
