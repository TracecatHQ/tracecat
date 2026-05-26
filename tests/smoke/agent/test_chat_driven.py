from __future__ import annotations

from typing import Literal

import pytest

from tests.smoke.agent.smoke_client import (
    AgentSmokeClient,
    ProviderSpec,
    assert_agent_response_contains,
    assert_agent_tool_result_contains,
    assert_has_approval_decision,
    assert_has_messages,
    new_sentinel,
    smoke_provider_names,
)

type McpKind = Literal["http", "stdio"]

PROVIDER_NAMES = smoke_provider_names()

pytestmark = [
    pytest.mark.anyio,
    pytest.mark.agent_smoke,
    pytest.mark.requires_api,
    pytest.mark.llm,
    pytest.mark.live_secret,
]


async def _create_agent_chat_session(
    smoke_client: AgentSmokeClient,
    provider: ProviderSpec,
    *,
    title: str,
    actions: list[str] | None = None,
    tool_approvals: dict[str, bool] | None = None,
    mcp_integrations: list[str] | None = None,
) -> str:
    preset = await smoke_client.create_agent_preset(
        provider,
        actions=actions,
        tool_approvals=tool_approvals,
        mcp_integrations=mcp_integrations,
    )
    session = await smoke_client.create_session(
        title=title,
        entity_type="agent_preset",
        entity_id=str(preset["id"]),
    )
    return str(session["id"])


@pytest.mark.parametrize("provider_name", PROVIDER_NAMES)
async def test_agent_chat_continuity_and_stream_resume(
    smoke_client: AgentSmokeClient,
    provider_name: str,
) -> None:
    provider = await smoke_client.ensure_provider(provider_name)
    sentinel = new_sentinel()
    session_id = await _create_agent_chat_session(
        smoke_client,
        provider,
        title="Agent smoke chat continuity",
    )

    first = await smoke_client.send_chat_turn(
        session_id,
        provider,
        (
            f"Remember this exact sentinel for the next turn: {sentinel}. "
            "Reply exactly: READY"
        ),
    )
    assert first.payload_count > 0
    assert first.saw_text_delta

    reloaded = await smoke_client.read_session(session_id)
    assert_has_messages(reloaded)

    second = await smoke_client.send_chat_turn(
        session_id,
        provider,
        "Return only the exact sentinel I asked you to remember. Do not add prose.",
    )
    reloaded_after_second = await smoke_client.read_session(session_id)
    assert_agent_response_contains(second, reloaded_after_second, sentinel)
    assert_has_messages(reloaded_after_second, minimum=4)
    await smoke_client.assert_basic_stream_resume(session_id)

    smoke_client.record(
        "chat_continuity",
        provider=provider.name,
        payloads=first.payload_count + second.payload_count,
    )


@pytest.mark.parametrize("provider_name", PROVIDER_NAMES)
async def test_agent_chat_platform_tool(
    smoke_client: AgentSmokeClient,
    provider_name: str,
) -> None:
    provider = await smoke_client.ensure_provider(provider_name)
    action = "core.transform.flatten_json"
    sentinel = new_sentinel()
    session_id = await _create_agent_chat_session(
        smoke_client,
        provider,
        title="Agent smoke chat platform tool",
        actions=[action],
    )

    result = await smoke_client.send_chat_turn(
        session_id,
        provider,
        (
            f"Use the {action} tool exactly once with JSON "
            f'{{"outer": {{"sentinel": "{sentinel}"}}}}. '
            "After the tool returns, reply only with the flattened "
            "`outer.sentinel` value from the tool result. Do not add prose."
        ),
    )
    reloaded = await smoke_client.read_session(session_id)
    assert_agent_response_contains(result, reloaded, sentinel)
    assert result.saw_tool_activity

    smoke_client.record("chat_platform_tool", provider=provider.name)


@pytest.mark.parametrize("provider_name", PROVIDER_NAMES)
@pytest.mark.parametrize("mcp_kind", ["http", "stdio"])
async def test_agent_chat_mcp_tool(
    smoke_client: AgentSmokeClient,
    provider_name: str,
    mcp_kind: McpKind,
) -> None:
    provider = await smoke_client.ensure_provider(provider_name)
    mcp_tool = await smoke_client.ensure_mcp_tool(mcp_kind)
    sentinel = new_sentinel()
    expected = f"{mcp_tool.expected_prefix}{sentinel}"
    session_id = await _create_agent_chat_session(
        smoke_client,
        provider,
        title=f"Agent smoke chat {mcp_kind} MCP tool",
        mcp_integrations=[mcp_tool.integration_id],
    )

    result = await smoke_client.send_chat_turn(
        session_id,
        provider,
        (
            f"Use the {mcp_tool.tool_name} tool with marker {sentinel}. "
            "Reply only with the raw tool result. Do not add prose."
        ),
    )
    reloaded = await smoke_client.read_session(session_id)
    assert result.saw_tool_activity
    assert_agent_response_contains(result, reloaded, sentinel)
    assert_agent_tool_result_contains(result, reloaded, expected)

    smoke_client.record(
        "chat_mcp_tool",
        mcp_kind=mcp_kind,
        provider=provider.name,
    )


@pytest.mark.parametrize("provider_name", PROVIDER_NAMES)
async def test_agent_chat_approval(
    smoke_client: AgentSmokeClient,
    provider_name: str,
) -> None:
    provider = await smoke_client.ensure_provider(provider_name)
    action = "core.transform.flatten_json"
    sentinel = new_sentinel()
    session_id = await _create_agent_chat_session(
        smoke_client,
        provider,
        title="Agent smoke chat approval",
        actions=[action],
        tool_approvals={action: True},
    )

    await smoke_client.send_chat_turn(
        session_id,
        provider,
        (
            f"Use the {action} tool exactly once with JSON "
            f'{{"approval": {{"sentinel": "{sentinel}"}}}}. '
            "The tool requires approval. After approval, reply exactly with "
            f"this sentinel: {sentinel}"
        ),
    )
    approval = await smoke_client.wait_for_approval_request(session_id)
    continued = await smoke_client.continue_with_approval(
        session_id,
        tool_call_id=str(approval["tool_call_id"]),
    )
    reloaded = await smoke_client.read_session(session_id)
    assert_agent_response_contains(continued, reloaded, sentinel)
    assert_has_approval_decision(reloaded)

    smoke_client.record("chat_approval", provider=provider.name)


@pytest.mark.parametrize("provider_name", PROVIDER_NAMES)
async def test_agent_chat_custom_registry_tool(
    smoke_client: AgentSmokeClient,
    provider_name: str,
) -> None:
    action = await smoke_client.ensure_custom_registry_action()
    provider = await smoke_client.ensure_provider(provider_name)
    sentinel = new_sentinel()
    session_id = await _create_agent_chat_session(
        smoke_client,
        provider,
        title="Agent smoke chat custom registry tool",
        actions=[action],
    )

    result = await smoke_client.send_chat_turn(
        session_id,
        provider,
        (
            f"Use the {action} custom registry tool with marker {sentinel}. "
            "Reply only with the marker returned by the tool. Do not add prose."
        ),
    )
    reloaded = await smoke_client.read_session(session_id)
    assert_agent_response_contains(result, reloaded, sentinel)
    assert result.saw_tool_activity

    smoke_client.record("chat_custom_registry_tool", provider=provider.name)
