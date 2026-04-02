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
from tracecat.agent.types import AgentConfig


@pytest.mark.anyio
async def test_read_init_payload_round_trip(tmp_path: Path) -> None:
    config = AgentConfig(
        model_name="claude-3-5-sonnet-20241022",
        model_provider="anthropic",
        instructions="Test init payload",
    )
    payload = RuntimeInitPayload(
        session_id=uuid4(),
        mcp_auth_token="mcp-token",
        config=SandboxAgentConfig.from_agent_config(config),
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
