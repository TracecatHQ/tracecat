from __future__ import annotations

import uuid
from pathlib import Path
from unittest.mock import MagicMock

from tracecat.agent.common.protocol import RuntimeEventEnvelope, RuntimeInitPayload
from tracecat.agent.common.stream_types import StreamEventType, UnifiedStreamEvent
from tracecat.agent.common.types import RuntimeResolution, SandboxAgentConfig
from tracecat.agent.runtime.claude_code.runtime import ClaudeAgentRuntime


def test_runtime_resolution_is_metadata_only() -> None:
    resolution = RuntimeResolution(
        runtime="claude_code",
        model_provider="anthropic",
        model_name="claude-3-5-sonnet",
        model_route="anthropic/claude-3-5-sonnet",
        instructions_present=True,
        instructions_length=120,
        system_prompt_length=500,
        actions_count=4,
        allowed_tools_count=6,
        mcp_server_count=2,
    )

    metadata = resolution.to_metadata()

    assert metadata["runtime"] == "claude_code"
    assert metadata["instructions_length"] == 120
    assert "system_prompt" not in metadata
    assert "tools" not in metadata
    assert "headers" not in metadata


def test_runtime_resolution_round_trips_through_result_envelope() -> None:
    resolution = RuntimeResolution(
        runtime="claude_code",
        model_provider="anthropic",
        model_name="claude-3-5-sonnet",
        model_route="anthropic/claude-3-5-sonnet",
        user_prompt_length=9,
        allowed_tools_count=1,
    )
    envelope = RuntimeEventEnvelope.from_result(
        usage={"input_tokens": 10, "output_tokens": 5},
        num_turns=1,
        output="done",
        runtime_resolution=resolution,
    )

    serialized = envelope.to_dict()
    restored = RuntimeEventEnvelope.from_dict(serialized)

    assert restored.runtime_resolution == resolution
    assert restored.result_output == "done"


def test_runtime_resolution_stream_event_is_metadata_event() -> None:
    resolution = RuntimeResolution(
        runtime="pydantic_ai",
        model_provider="openai",
        model_name="gpt-4.1-mini",
    )

    event = UnifiedStreamEvent.runtime_resolution_event(resolution.to_metadata())

    assert event.type is StreamEventType.RUNTIME_RESOLUTION
    assert event.metadata == {
        "runtime": "pydantic_ai",
        "model_provider": "openai",
        "model_name": "gpt-4.1-mini",
        "passthrough": False,
        "base_url_configured": False,
        "instructions_present": False,
        "instructions_length": 0,
        "system_prompt_fragment_count": 0,
        "user_prompt_length": 0,
        "output_type_kind": "none",
        "approval_policy_count": 0,
        "approvals_enabled": False,
        "mcp_server_count": 0,
        "stdio_mcp_server_count": 0,
        "subagent_count": 0,
        "skills_count": 0,
        "resumed": False,
        "forked": False,
        "approval_continuation": False,
    }


def test_claude_runtime_resolution_counts_resolved_runtime_shape() -> None:
    payload = RuntimeInitPayload(
        session_id=uuid.uuid4(),
        mcp_auth_token="mcp-token",
        config=SandboxAgentConfig(
            model_name="claude-3-5-sonnet",
            model_provider="anthropic",
            instructions="Investigate alerts.",
            tool_approvals={"core.http_request": True},
            mcp_servers=[
                {
                    "type": "stdio",
                    "name": "local-tools",
                    "command": "npx",
                }
            ],
            enable_thinking=True,
            enable_internet_access=False,
        ),
        user_prompt="Analyze this alert",
        llm_gateway_auth_token="llm-token",
        allowed_actions={},
        sdk_session_id="previous-session",
        is_fork=True,
        is_approval_continuation=True,
    )
    runtime = ClaudeAgentRuntime(
        MagicMock(),
        transport_factory=lambda _: MagicMock(),
        cwd=Path("/tmp/tracecat-agent-test"),
    )
    runtime._configure_runtime_state(payload)
    prepared = runtime._runtime_resolution(
        payload=payload,
        options=MagicMock(
            model="anthropic/claude-3-5-sonnet",
            system_prompt="system prompt",
            allowed_tools=["mcp__tracecat-registry__core__http_request"],
            disallowed_tools=["WebSearch", "WebFetch"],
        ),
        resume_session_id="previous-session",
        fork_session=True,
        mcp_servers={"tracecat-registry": {"type": "http", "url": "http://mcp"}},
        stdio_mcp_servers={"local-tools": {"type": "stdio", "command": "npx"}},
        agent_definitions=None,
    )

    assert prepared.runtime == "claude_code"
    assert prepared.model_route == "anthropic/claude-3-5-sonnet"
    assert prepared.instructions_length == len("Investigate alerts.")
    assert prepared.allowed_tools_count == 1
    assert prepared.disallowed_tools_count == 2
    assert prepared.mcp_server_count == 1
    assert prepared.stdio_mcp_server_count == 1
    assert prepared.approval_policy_count == 1
    assert prepared.resumed is True
    assert prepared.forked is True
    assert prepared.approval_continuation is True
