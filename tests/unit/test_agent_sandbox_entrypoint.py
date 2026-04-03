from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import orjson
import pytest

from tracecat.agent.common.protocol import RuntimeInitPayload
from tracecat.agent.common.types import SandboxAgentConfig
from tracecat.agent.sandbox.entrypoint import (
    INIT_PAYLOAD_ENV_VAR,
    _read_init_payload,
    _resolve_init_payload_path,
)
from tracecat.agent.sandbox.shim_entrypoint import (
    _read_init_payload as _read_shim_init_payload,
)
from tracecat.agent.sandbox.shim_entrypoint import (
    _read_stdin_chunk,
)


@pytest.mark.anyio
async def test_read_init_payload_round_trip(tmp_path: Path) -> None:
    config = SandboxAgentConfig(
        model_name="claude-3-5-sonnet-20241022",
        model_provider="anthropic",
        instructions="Test init payload",
    )
    payload = RuntimeInitPayload(
        session_id=uuid4(),
        mcp_auth_token="mcp-token",
        config=config,
        user_prompt="hello",
        llm_gateway_auth_token="llm-token",
    )
    init_path = tmp_path / "init.json"
    init_path.write_bytes(orjson.dumps(payload.to_dict()))

    parsed = await _read_init_payload(init_path)

    assert parsed.to_dict() == payload.to_dict()


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
