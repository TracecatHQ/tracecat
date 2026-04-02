from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import orjson
import pytest

from tracecat.agent.common.protocol import RuntimeInitPayload
from tracecat.agent.common.types import SandboxAgentConfig
from tracecat.agent.sandbox.entrypoint import _read_init_payload
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
