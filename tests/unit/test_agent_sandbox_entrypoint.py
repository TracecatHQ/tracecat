from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, cast

import orjson
import pytest

from tracecat.agent.sandbox.shim_entrypoint import (
    DEFAULT_LLM_SOCKET_PATH,
    INIT_PAYLOAD_ENV_VAR,
    LLMBridge,
    _pump_stdin_to_process,
    _read_stdin_chunk,
    _resolve_init_payload_path,
    _resolve_llm_socket_path,
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


class _FakeLoop:
    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = iter(chunks)

    async def run_in_executor(
        self,
        _executor: object,
        fn: object,
        chunk_size: int,
    ) -> bytes:
        assert fn is _read_stdin_chunk
        assert chunk_size == 65536
        return next(self._chunks)


@pytest.mark.anyio
async def test_pump_stdin_to_process_suppresses_broken_pipe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    loop = _FakeLoop([b"hello", b""])
    monkeypatch.setattr(
        "tracecat.agent.sandbox.shim_entrypoint.asyncio.get_running_loop",
        lambda: loop,
    )
    writer = _FakeStreamWriter(fail_after_write=True)

    await _pump_stdin_to_process(cast(Any, writer))

    assert writer.writes == [b"hello"]
    assert writer.closed is True
