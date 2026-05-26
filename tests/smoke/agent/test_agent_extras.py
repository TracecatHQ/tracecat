from __future__ import annotations

import pytest

from tests.smoke.agent.smoke_client import (
    AgentSmokeClient,
    ProviderSpec,
    assert_agent_response_contains,
    new_sentinel,
)

pytestmark = [
    pytest.mark.anyio,
    pytest.mark.agent_smoke,
    pytest.mark.requires_api,
    pytest.mark.llm,
    pytest.mark.live_secret,
]


async def test_case_chat_uses_primary_provider(
    smoke_client: AgentSmokeClient,
    primary_provider: ProviderSpec,
) -> None:
    sentinel = new_sentinel()
    case = await smoke_client.create_case(
        summary=f"Agent smoke case {sentinel}",
        description=f"Case chat should preserve sentinel {sentinel}",
    )
    session = await smoke_client.create_session(
        title="Agent smoke case chat",
        entity_type="case",
        entity_id=str(case["id"]),
    )

    result = await smoke_client.send_chat_turn(
        str(session["id"]),
        primary_provider,
        f"Reply exactly with this case smoke sentinel: {sentinel}",
    )
    reloaded = await smoke_client.read_session(str(session["id"]))
    assert_agent_response_contains(result, reloaded, sentinel)

    smoke_client.record("case_chat", provider=primary_provider.name)


async def test_builder_assistant_uses_primary_provider(
    smoke_client: AgentSmokeClient,
    primary_provider: ProviderSpec,
) -> None:
    sentinel = new_sentinel()
    preset = await smoke_client.create_agent_preset(primary_provider)
    session = await smoke_client.create_session(
        title="Agent smoke builder assistant",
        entity_type="agent_preset_builder",
        entity_id=str(preset["id"]),
    )

    result = await smoke_client.send_chat_turn(
        str(session["id"]),
        primary_provider,
        f"Reply exactly with this builder smoke sentinel: {sentinel}",
    )
    reloaded = await smoke_client.read_session(str(session["id"]))
    assert_agent_response_contains(result, reloaded, sentinel)

    smoke_client.record("builder_assistant", provider=primary_provider.name)
