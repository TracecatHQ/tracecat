from __future__ import annotations

import uuid
from typing import Literal

import pytest

from tests.smoke.agent.smoke_client import (
    AgentSmokeClient,
    assert_has_approval_decision,
    assert_has_messages,
    assert_workflow_result_contains,
    assert_workflow_tool_called,
    assert_workflow_tool_result_contains,
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


@pytest.mark.parametrize("provider_name", PROVIDER_NAMES)
async def test_ai_agent_workflow_continuity_and_stream_resume(
    smoke_client: AgentSmokeClient,
    provider_name: str,
) -> None:
    provider = await smoke_client.ensure_provider(provider_name)
    sentinel = new_sentinel()
    session_id = str(uuid.uuid4())

    _, first_exec_id, _ = await smoke_client.create_workflow_agent_run(
        provider,
        session_id=session_id,
        prompt=(
            f"Remember this exact sentinel for the next workflow turn: {sentinel}. "
            "Reply exactly: READY"
        ),
    )
    await smoke_client.wait_for_workflow(first_exec_id)

    _, second_exec_id, _ = await smoke_client.create_workflow_agent_run(
        provider,
        session_id=session_id,
        prompt=(
            "Return only the exact sentinel I asked you to remember in the "
            "previous workflow turn. Do not add prose."
        ),
    )
    compact = await smoke_client.wait_for_workflow(second_exec_id)
    reloaded = await smoke_client.read_session(session_id)
    assert_workflow_result_contains(compact, sentinel)
    assert_has_messages(reloaded, minimum=4)
    await smoke_client.assert_basic_stream_resume(session_id)

    smoke_client.record(
        "workflow_ai_agent_continuity",
        provider=provider.name,
        workflow_exec_ids=[first_exec_id, second_exec_id],
    )


@pytest.mark.parametrize("provider_name", PROVIDER_NAMES)
async def test_ai_agent_workflow_platform_tool(
    smoke_client: AgentSmokeClient,
    provider_name: str,
) -> None:
    provider = await smoke_client.ensure_provider(provider_name)
    action = "core.transform.flatten_json"
    sentinel = new_sentinel()

    _, exec_id, _ = await smoke_client.create_workflow_agent_run(
        provider,
        prompt=(
            f"Use the {action} tool exactly once with JSON "
            f'{{"outer": {{"sentinel": "{sentinel}"}}}}. '
            "After the tool returns, reply only with the flattened "
            "`outer.sentinel` value from the tool result. Do not add prose."
        ),
        actions=[action],
    )
    compact = await smoke_client.wait_for_workflow(exec_id)
    assert_workflow_result_contains(compact, sentinel)
    assert_workflow_tool_called(compact)

    smoke_client.record("workflow_ai_agent_platform_tool", provider=provider.name)


@pytest.mark.parametrize("provider_name", PROVIDER_NAMES)
@pytest.mark.parametrize("mcp_kind", ["http", "stdio"])
async def test_ai_agent_workflow_mcp_tool(
    smoke_client: AgentSmokeClient,
    provider_name: str,
    mcp_kind: McpKind,
) -> None:
    provider = await smoke_client.ensure_provider(provider_name)
    mcp_tool = await smoke_client.ensure_mcp_tool(mcp_kind)
    sentinel = new_sentinel()
    expected = f"{mcp_tool.expected_prefix}{sentinel}"

    _, exec_id, _ = await smoke_client.create_workflow_agent_run(
        provider,
        prompt=(
            f"Use the {mcp_tool.tool_name} tool with marker {sentinel}. "
            "Reply only with the raw tool result. Do not add prose."
        ),
        mcp_integrations=[mcp_tool.integration_id],
    )
    compact = await smoke_client.wait_for_workflow(exec_id)
    assert_workflow_tool_called(compact)
    assert_workflow_result_contains(compact, sentinel)
    assert_workflow_tool_result_contains(compact, expected)

    smoke_client.record(
        "workflow_ai_agent_mcp_tool",
        mcp_kind=mcp_kind,
        provider=provider.name,
    )


@pytest.mark.parametrize("provider_name", PROVIDER_NAMES)
async def test_ai_agent_workflow_approval(
    smoke_client: AgentSmokeClient,
    provider_name: str,
) -> None:
    provider = await smoke_client.ensure_provider(provider_name)
    action = "core.transform.flatten_json"
    sentinel = new_sentinel()
    session_id = str(uuid.uuid4())

    _, exec_id, _ = await smoke_client.create_workflow_agent_run(
        provider,
        session_id=session_id,
        prompt=(
            f"You must call the {action} tool now with exactly this JSON "
            f'argument: {{"approval": {{"sentinel": "{sentinel}"}}}}. '
            "Do not answer in text before calling the tool. The tool requires "
            "approval, so requesting approval is the expected next step. "
            f"After approval and tool completion, reply exactly with: {sentinel}"
        ),
        actions=[action],
        tool_approvals={action: True},
    )
    approval = await smoke_client.wait_for_approval_request(session_id)
    await smoke_client.continue_with_approval(
        session_id,
        tool_call_id=str(approval["tool_call_id"]),
    )
    compact = await smoke_client.wait_for_workflow(exec_id)
    reloaded = await smoke_client.read_session(session_id)
    assert_workflow_result_contains(compact, sentinel)
    assert_has_approval_decision(reloaded)

    smoke_client.record("workflow_ai_agent_approval", provider=provider.name)


@pytest.mark.parametrize("provider_name", PROVIDER_NAMES)
async def test_ai_agent_workflow_custom_registry_tool(
    smoke_client: AgentSmokeClient,
    provider_name: str,
) -> None:
    action = await smoke_client.ensure_custom_registry_action()
    provider = await smoke_client.ensure_provider(provider_name)
    sentinel = new_sentinel()

    _, exec_id, _ = await smoke_client.create_workflow_agent_run(
        provider,
        prompt=(
            f"Use the {action} custom registry tool with marker {sentinel}. "
            "Reply only with the marker returned by the tool. Do not add prose."
        ),
        actions=[action],
    )
    compact = await smoke_client.wait_for_workflow(exec_id)
    assert_workflow_result_contains(compact, sentinel)
    assert_workflow_tool_called(compact)

    smoke_client.record(
        "workflow_ai_agent_custom_registry_tool", provider=provider.name
    )


@pytest.mark.parametrize("provider_name", PROVIDER_NAMES)
async def test_preset_agent_workflow_uses_saved_preset(
    smoke_client: AgentSmokeClient,
    provider_name: str,
) -> None:
    provider = await smoke_client.ensure_provider(provider_name)
    sentinel = new_sentinel()
    preset = await smoke_client.create_agent_preset(provider)

    _, exec_id, _ = await smoke_client.create_workflow_preset_agent_run(
        provider,
        preset,
        prompt=f"Reply exactly with this preset workflow sentinel: {sentinel}",
    )
    compact = await smoke_client.wait_for_workflow(exec_id)
    assert_workflow_result_contains(compact, sentinel)

    smoke_client.record("workflow_preset_agent", provider=provider.name)
