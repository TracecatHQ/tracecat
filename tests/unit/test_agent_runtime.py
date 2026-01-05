"""Tests for ClaudeAgentRuntime.

Tests the runtime execution with mocked Claude SDK.
"""

from __future__ import annotations

import uuid
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from claude_agent_sdk.types import (
    HookContext,
    PreToolUseHookInput,
    PreToolUseHookSpecificOutput,
    SyncHookJSONOutput,
)

from tracecat.agent.mcp.types import MCPToolDefinition
from tracecat.agent.runtime import ClaudeAgentRuntime
from tracecat.agent.sandbox.protocol import RuntimeInitPayload
from tracecat.agent.sandbox.socket_io import SocketStreamWriter
from tracecat.agent.stream.types import StreamEventType, UnifiedStreamEvent
from tracecat.agent.types import AgentConfig


@pytest.fixture
def sample_agent_config() -> AgentConfig:
    """Create a sample agent config for testing."""
    return AgentConfig(
        model_name="claude-3-5-sonnet-20241022",
        model_provider="anthropic",
        instructions="You are a helpful assistant.",
        actions=["core.http_request"],
        tool_approvals={"core.http_request": False},
    )


@pytest.fixture
def sample_tool_definitions() -> dict[str, MCPToolDefinition]:
    """Create sample tool definitions for testing."""
    return {
        "core.http_request": MCPToolDefinition(
            name="core.http_request",
            description="Make an HTTP request",
            parameters_json_schema={
                "type": "object",
                "properties": {
                    "url": {"type": "string"},
                    "method": {"type": "string"},
                },
                "required": ["url", "method"],
            },
        ),
    }


@pytest.fixture
def sample_init_payload(
    sample_agent_config: AgentConfig,
    sample_tool_definitions: dict[str, MCPToolDefinition],
) -> RuntimeInitPayload:
    """Create a sample init payload for testing."""
    return RuntimeInitPayload(
        session_id=uuid.uuid4(),
        mcp_socket_path="/sockets/mcp.sock",
        jwt_token="test-jwt-token",
        config=sample_agent_config,
        user_prompt="Hello, how are you?",
        litellm_base_url="http://localhost:8080",
        litellm_auth_token="test-litellm-token",
        allowed_actions=sample_tool_definitions,
    )


@pytest.fixture
def mock_socket_writer() -> MagicMock:
    """Create a mock SocketStreamWriter."""
    writer = MagicMock(spec=SocketStreamWriter)
    writer.send_event = AsyncMock()
    writer.send_session_update = AsyncMock()
    writer.send_error = AsyncMock()
    writer.send_done = AsyncMock()
    writer.close = AsyncMock()
    return writer


@pytest.fixture
def mock_claude_sdk_client() -> MagicMock:
    """Create a mock ClaudeSDKClient."""
    mock_client = MagicMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.query = AsyncMock()
    mock_client.interrupt = AsyncMock()

    # Default: return empty response
    async def empty_response() -> Any:
        return
        yield  # Make it an async generator  # noqa: B901

    mock_client.receive_response = empty_response
    return mock_client


def make_pre_tool_use_input(
    tool_name: str,
    tool_input: dict[str, Any],
) -> PreToolUseHookInput:
    """Create a PreToolUseHookInput for testing."""
    return PreToolUseHookInput(
        session_id="test-session-id",
        transcript_path="/tmp/test-transcript.jsonl",
        cwd="/tmp",
        hook_event_name="PreToolUse",
        tool_name=tool_name,
        tool_input=tool_input,
    )


def make_hook_context() -> HookContext:
    """Create a HookContext for testing."""
    return HookContext(signal=None)


def get_hook_output(result: SyncHookJSONOutput) -> PreToolUseHookSpecificOutput:
    """Extract and cast hookSpecificOutput from result."""
    return cast(PreToolUseHookSpecificOutput, result.get("hookSpecificOutput", {}))


class TestClaudeAgentRuntimeRun:
    """Tests for ClaudeAgentRuntime.run()."""

    @pytest.mark.anyio
    async def test_streams_user_message(
        self,
        mock_socket_writer: MagicMock,
        mock_claude_sdk_client: MagicMock,
        sample_init_payload: RuntimeInitPayload,
    ) -> None:
        """Test that runtime streams user message event."""
        with (
            patch(
                "tracecat.agent.runtime.ClaudeSDKClient",
                return_value=mock_claude_sdk_client,
            ),
            patch(
                "tracecat.agent.runtime.create_proxy_mcp_server",
                AsyncMock(return_value={}),
            ),
        ):
            runtime = ClaudeAgentRuntime(mock_socket_writer)
            await runtime.run(sample_init_payload)

        # Should have streamed user message
        calls = mock_socket_writer.send_event.call_args_list
        assert len(calls) >= 1
        first_event: UnifiedStreamEvent = calls[0][0][0]
        assert first_event.type == StreamEventType.USER_MESSAGE
        assert first_event.text == sample_init_payload.user_prompt

    @pytest.mark.anyio
    async def test_sends_done_on_completion(
        self,
        mock_socket_writer: MagicMock,
        mock_claude_sdk_client: MagicMock,
        sample_init_payload: RuntimeInitPayload,
    ) -> None:
        """Test that runtime sends done signal on completion."""
        with (
            patch(
                "tracecat.agent.runtime.ClaudeSDKClient",
                return_value=mock_claude_sdk_client,
            ),
            patch(
                "tracecat.agent.runtime.create_proxy_mcp_server",
                AsyncMock(return_value={}),
            ),
        ):
            runtime = ClaudeAgentRuntime(mock_socket_writer)
            await runtime.run(sample_init_payload)

        mock_socket_writer.send_done.assert_awaited_once()

    @pytest.mark.anyio
    async def test_handles_stream_events(
        self,
        mock_socket_writer: MagicMock,
        mock_claude_sdk_client: MagicMock,
        sample_init_payload: RuntimeInitPayload,
    ) -> None:
        """Test that runtime handles stream events from SDK."""
        # Create mock StreamEvent objects
        mock_stream_event = MagicMock()
        mock_stream_event.event = {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "text_delta", "text": "Hello!"},
        }

        async def mock_receive() -> Any:
            yield mock_stream_event

        mock_claude_sdk_client.receive_response = mock_receive

        # Mock the adapter to return UnifiedStreamEvents
        mock_adapter = MagicMock()
        mock_adapter.to_unified_event.return_value = UnifiedStreamEvent(
            type=StreamEventType.TEXT_DELTA,
            text="Hello!",
            part_id=0,
        )

        with (
            patch(
                "tracecat.agent.runtime.ClaudeSDKClient",
                return_value=mock_claude_sdk_client,
            ),
            patch(
                "tracecat.agent.runtime.create_proxy_mcp_server",
                AsyncMock(return_value={}),
            ),
            patch("tracecat.agent.runtime.ClaudeSDKAdapter", return_value=mock_adapter),
            patch("tracecat.agent.runtime.StreamEvent", MagicMock),
        ):
            runtime = ClaudeAgentRuntime(mock_socket_writer)
            await runtime.run(sample_init_payload)

        # Should have streamed user message + content event
        assert mock_socket_writer.send_event.call_count >= 2
        mock_adapter.to_unified_event.assert_called()

    @pytest.mark.anyio
    async def test_sends_error_on_exception(
        self,
        mock_socket_writer: MagicMock,
        mock_claude_sdk_client: MagicMock,
        sample_init_payload: RuntimeInitPayload,
    ) -> None:
        """Test that runtime sends error on exception."""
        mock_claude_sdk_client.query = AsyncMock(side_effect=ValueError("Test error"))

        with (
            patch(
                "tracecat.agent.runtime.ClaudeSDKClient",
                return_value=mock_claude_sdk_client,
            ),
            patch(
                "tracecat.agent.runtime.create_proxy_mcp_server",
                AsyncMock(return_value={}),
            ),
            pytest.raises(ValueError, match="Test error"),
        ):
            runtime = ClaudeAgentRuntime(mock_socket_writer)
            await runtime.run(sample_init_payload)

        mock_socket_writer.send_error.assert_awaited_once()
        mock_socket_writer.send_done.assert_awaited_once()


class TestClaudeAgentRuntimePreToolUseHook:
    """Tests for ClaudeAgentRuntime._pre_tool_use_hook()."""

    @pytest.mark.anyio
    async def test_auto_approve_list_tools(
        self,
        mock_socket_writer: MagicMock,
        sample_init_payload: RuntimeInitPayload,
    ) -> None:
        """Test that list_tools discovery tool is auto-approved."""
        runtime = ClaudeAgentRuntime(mock_socket_writer)
        runtime._allowed_actions = sample_init_payload.allowed_actions
        runtime._tool_approvals = sample_init_payload.config.tool_approvals

        result = await runtime._pre_tool_use_hook(
            input_data=make_pre_tool_use_input(
                tool_name="mcp__tracecat-registry__list_tools",
                tool_input={},
            ),
            tool_use_id="call-1",
            context=make_hook_context(),
        )

        hook_output = get_hook_output(result)
        assert hook_output.get("permissionDecision") == "allow"

    @pytest.mark.anyio
    async def test_auto_approve_get_tool_schema(
        self,
        mock_socket_writer: MagicMock,
        sample_init_payload: RuntimeInitPayload,
    ) -> None:
        """Test that get_tool_schema discovery tool is auto-approved."""
        runtime = ClaudeAgentRuntime(mock_socket_writer)
        runtime._allowed_actions = sample_init_payload.allowed_actions
        runtime._tool_approvals = sample_init_payload.config.tool_approvals

        result = await runtime._pre_tool_use_hook(
            input_data=make_pre_tool_use_input(
                tool_name="mcp__tracecat-registry__get_tool_schema",
                tool_input={"tool_name": "core.http_request"},
            ),
            tool_use_id="call-2",
            context=make_hook_context(),
        )

        hook_output = get_hook_output(result)
        assert hook_output.get("permissionDecision") == "allow"

    @pytest.mark.anyio
    async def test_auto_approve_user_mcp_tools(
        self,
        mock_socket_writer: MagicMock,
        sample_init_payload: RuntimeInitPayload,
    ) -> None:
        """Test that user MCP tools are auto-approved."""
        runtime = ClaudeAgentRuntime(mock_socket_writer)
        runtime._allowed_actions = sample_init_payload.allowed_actions
        runtime._tool_approvals = sample_init_payload.config.tool_approvals

        result = await runtime._pre_tool_use_hook(
            input_data=make_pre_tool_use_input(
                tool_name="mcp__user-mcp-0__some_tool",
                tool_input={"arg": "value"},
            ),
            tool_use_id="call-3",
            context=make_hook_context(),
        )

        hook_output = get_hook_output(result)
        assert hook_output.get("permissionDecision") == "allow"

    @pytest.mark.anyio
    async def test_execute_tool_requires_approval(
        self,
        mock_socket_writer: MagicMock,
        sample_agent_config: AgentConfig,
    ) -> None:
        """Test that tools marked for approval trigger approval request."""
        # Set tool to require approval
        sample_agent_config.tool_approvals = {"core.http_request": True}

        runtime = ClaudeAgentRuntime(mock_socket_writer)
        runtime._allowed_actions = {
            "core.http_request": MCPToolDefinition(
                name="core.http_request",
                description="Make HTTP request",
                parameters_json_schema={},
            )
        }
        runtime._tool_approvals = {"core.http_request": True}
        runtime._client = MagicMock()
        runtime._client.interrupt = AsyncMock()

        result = await runtime._pre_tool_use_hook(
            input_data=make_pre_tool_use_input(
                tool_name="mcp__tracecat-registry__execute_tool",
                tool_input={
                    "tool_name": "core.http_request",
                    "args": {"url": "https://example.com"},
                },
            ),
            tool_use_id="call-4",
            context=make_hook_context(),
        )

        hook_output = get_hook_output(result)
        assert hook_output.get("permissionDecision") == "deny"
        assert "requires approval" in (
            hook_output.get("permissionDecisionReason") or ""
        )

        # Should have sent approval request and interrupted
        mock_socket_writer.send_event.assert_awaited()
        runtime._client.interrupt.assert_awaited()

    @pytest.mark.anyio
    async def test_execute_tool_auto_approve_when_not_requiring_approval(
        self,
        mock_socket_writer: MagicMock,
        sample_init_payload: RuntimeInitPayload,
    ) -> None:
        """Test that execute_tool is auto-approved when tool doesn't require approval."""
        runtime = ClaudeAgentRuntime(mock_socket_writer)
        runtime._allowed_actions = sample_init_payload.allowed_actions
        runtime._tool_approvals = {"core.http_request": False}  # No approval needed

        result = await runtime._pre_tool_use_hook(
            input_data=make_pre_tool_use_input(
                tool_name="mcp__tracecat-registry__execute_tool",
                tool_input={
                    "tool_name": "core.http_request",
                    "args": {"url": "https://example.com"},
                },
            ),
            tool_use_id="call-5",
            context=make_hook_context(),
        )

        hook_output = get_hook_output(result)
        assert hook_output.get("permissionDecision") == "allow"

    @pytest.mark.anyio
    async def test_deny_unknown_tool(
        self,
        mock_socket_writer: MagicMock,
        sample_init_payload: RuntimeInitPayload,
    ) -> None:
        """Test that unknown tools are denied."""
        runtime = ClaudeAgentRuntime(mock_socket_writer)
        runtime._allowed_actions = sample_init_payload.allowed_actions
        runtime._tool_approvals = sample_init_payload.config.tool_approvals

        result = await runtime._pre_tool_use_hook(
            input_data=make_pre_tool_use_input(
                tool_name="some_random_tool",
                tool_input={},
            ),
            tool_use_id="call-6",
            context=make_hook_context(),
        )

        hook_output = get_hook_output(result)
        assert hook_output.get("permissionDecision") == "deny"
        assert "not allowed" in (hook_output.get("permissionDecisionReason") or "")
