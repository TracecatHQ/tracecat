"""Tests for ClaudeAgentRuntime.

Tests the runtime execution with mocked Claude SDK.
"""

from __future__ import annotations

import os
import tempfile
import uuid
from dataclasses import replace
from pathlib import Path
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, call, patch

import orjson
import pytest
from claude_agent_sdk.types import (
    HookContext,
    PreToolUseHookInput,
    SyncHookJSONOutput,
)

from tracecat.agent.common.protocol import RuntimeInitPayload
from tracecat.agent.common.socket_io import SocketStreamWriter
from tracecat.agent.common.stream_types import StreamEventType, UnifiedStreamEvent
from tracecat.agent.common.types import (
    MCPToolDefinition,
    SandboxAgentConfig,
)
from tracecat.agent.common.types import (
    MCPToolDefinition as SharedMCPToolDefinition,
)
from tracecat.agent.mcp.proxy_server import (
    PROXY_TOOL_CALL_ID_KEY,
    PROXY_TOOL_METADATA_KEY,
)
from tracecat.agent.runtime.claude_code.runtime import ClaudeAgentRuntime
from tracecat.agent.types import AgentConfig


@pytest.fixture
def sample_agent_config() -> AgentConfig:
    """Create a sample agent config for testing."""
    return cast(Any, AgentConfig)(
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
                "additionalProperties": False,
            },
        ),
    }


@pytest.fixture
def sample_sandbox_config(sample_agent_config: AgentConfig) -> SandboxAgentConfig:
    """Create a sample sandbox config for testing."""
    return SandboxAgentConfig.from_agent_config(sample_agent_config)


@pytest.fixture
def sample_shared_tool_definitions(
    sample_tool_definitions: dict[str, MCPToolDefinition],
) -> dict[str, SharedMCPToolDefinition]:
    """Convert Pydantic tool definitions to shared dataclass format."""
    return {
        name: SharedMCPToolDefinition(
            name=tool.name,
            description=tool.description,
            parameters_json_schema=tool.parameters_json_schema,
        )
        for name, tool in sample_tool_definitions.items()
    }


@pytest.fixture
def sample_init_payload(
    sample_sandbox_config: SandboxAgentConfig,
    sample_shared_tool_definitions: dict[str, SharedMCPToolDefinition],
) -> RuntimeInitPayload:
    """Create a sample init payload for testing."""
    return RuntimeInitPayload(
        session_id=uuid.uuid4(),
        mcp_auth_token="test-jwt-token",
        config=sample_sandbox_config,
        user_prompt="Hello, how are you?",
        llm_gateway_auth_token="test-llm-token",
        allowed_actions=sample_shared_tool_definitions,
    )


@pytest.fixture
def mock_socket_writer() -> MagicMock:
    """Create a mock SocketStreamWriter."""
    writer = MagicMock(spec=SocketStreamWriter)
    writer.send_stream_event = AsyncMock()
    writer.send_message = AsyncMock()
    writer.send_session_line = AsyncMock()
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


def make_hook_input(
    tool_name: str,
    tool_input: dict[str, Any],
    tool_use_id: str,
) -> PreToolUseHookInput:
    """Create a PreToolUse hook input for testing."""
    return {
        "session_id": "test-session-id",
        "transcript_path": "/tmp/test-transcript.jsonl",
        "cwd": "/tmp",
        "hook_event_name": "PreToolUse",
        "tool_name": tool_name,
        "tool_input": tool_input,
        "tool_use_id": tool_use_id,
    }


def make_hook_context() -> HookContext:
    """Create a HookContext for testing."""
    return HookContext(signal=None)


def get_hook_output(result: SyncHookJSONOutput) -> dict[str, Any]:
    """Extract hookSpecificOutput from result."""
    return cast(dict[str, Any], result.get("hookSpecificOutput", {}))


class TestClaudeAgentRuntimeRun:
    """Tests for ClaudeAgentRuntime.run()."""

    @pytest.fixture(autouse=True)
    def _mock_llm_bridge_port(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Set the LLM bridge port env var so get_llm_proxy_url() succeeds."""
        monkeypatch.setenv("TRACECAT__LLM_BRIDGE_PORT", "12345")

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
                "tracecat.agent.runtime.claude_code.runtime.ClaudeSDKClient",
                return_value=mock_claude_sdk_client,
            ),
            patch(
                "tracecat.agent.runtime.claude_code.runtime.create_proxy_mcp_server",
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
        mock_stream_event.session_id = "test-sdk-session"
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
                "tracecat.agent.runtime.claude_code.runtime.ClaudeSDKClient",
                return_value=mock_claude_sdk_client,
            ),
            patch(
                "tracecat.agent.runtime.claude_code.runtime.create_proxy_mcp_server",
                AsyncMock(return_value={}),
            ),
            patch(
                "tracecat.agent.runtime.claude_code.runtime.ClaudeSDKAdapter",
                return_value=mock_adapter,
            ),
            patch("tracecat.agent.runtime.claude_code.runtime.StreamEvent", MagicMock),
        ):
            runtime = ClaudeAgentRuntime(mock_socket_writer)
            await runtime.run(sample_init_payload)

        # Should have called send_stream_event
        mock_socket_writer.send_stream_event.assert_awaited()

    @pytest.mark.anyio
    async def test_sets_skip_version_check_before_sdk_connect(
        self,
        monkeypatch: pytest.MonkeyPatch,
        mock_socket_writer: MagicMock,
        sample_init_payload: RuntimeInitPayload,
    ) -> None:
        """Test that the runtime primes the SDK skip-version-check env var."""
        monkeypatch.delenv("CLAUDE_AGENT_SDK_SKIP_VERSION_CHECK", raising=False)
        mock_client = MagicMock()

        async def enter_client() -> MagicMock:
            assert os.environ["CLAUDE_AGENT_SDK_SKIP_VERSION_CHECK"] == "1"
            return mock_client

        mock_client.__aenter__ = AsyncMock(side_effect=enter_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.query = AsyncMock()
        mock_client.interrupt = AsyncMock()

        async def empty_response() -> Any:
            return
            yield  # pragma: no cover  # noqa: B901

        mock_client.receive_response = empty_response

        with (
            patch(
                "tracecat.agent.runtime.claude_code.runtime.ClaudeSDKClient",
                return_value=mock_client,
            ),
            patch(
                "tracecat.agent.runtime.claude_code.runtime.create_proxy_mcp_server",
                AsyncMock(return_value={}),
            ),
        ):
            runtime = ClaudeAgentRuntime(mock_socket_writer)
            await runtime.run(sample_init_payload)

        assert os.environ["CLAUDE_AGENT_SDK_SKIP_VERSION_CHECK"] == "1"

    @pytest.mark.anyio
    async def test_preserves_existing_skip_version_check_value(
        self,
        monkeypatch: pytest.MonkeyPatch,
        mock_socket_writer: MagicMock,
        sample_init_payload: RuntimeInitPayload,
    ) -> None:
        """Test that runtime does not overwrite an existing SDK env value."""
        monkeypatch.setenv("CLAUDE_AGENT_SDK_SKIP_VERSION_CHECK", "existing")
        mock_client = MagicMock()

        async def enter_client() -> MagicMock:
            assert os.environ["CLAUDE_AGENT_SDK_SKIP_VERSION_CHECK"] == "existing"
            return mock_client

        mock_client.__aenter__ = AsyncMock(side_effect=enter_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.query = AsyncMock()
        mock_client.interrupt = AsyncMock()

        async def empty_response() -> Any:
            return
            yield  # pragma: no cover  # noqa: B901

        mock_client.receive_response = empty_response

        with (
            patch(
                "tracecat.agent.runtime.claude_code.runtime.ClaudeSDKClient",
                return_value=mock_client,
            ),
            patch(
                "tracecat.agent.runtime.claude_code.runtime.create_proxy_mcp_server",
                AsyncMock(return_value={}),
            ),
        ):
            runtime = ClaudeAgentRuntime(mock_socket_writer)
            await runtime.run(sample_init_payload)

        assert os.environ["CLAUDE_AGENT_SDK_SKIP_VERSION_CHECK"] == "existing"

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
                "tracecat.agent.runtime.claude_code.runtime.ClaudeSDKClient",
                return_value=mock_claude_sdk_client,
            ),
            patch(
                "tracecat.agent.runtime.claude_code.runtime.create_proxy_mcp_server",
                AsyncMock(return_value={}),
            ),
            pytest.raises(ValueError, match="Test error"),
        ):
            runtime = ClaudeAgentRuntime(mock_socket_writer)
            await runtime.run(sample_init_payload)

        mock_socket_writer.send_error.assert_awaited_once()
        mock_socket_writer.send_done.assert_awaited_once()

    @pytest.mark.anyio
    async def test_canonicalizes_registry_mcp_alias_on_resume(
        self,
        mock_socket_writer: MagicMock,
        mock_claude_sdk_client: MagicMock,
        sample_init_payload: RuntimeInitPayload,
    ) -> None:
        """Test that resumed sessions only mount the canonical registry MCP name."""
        captured_options: list[Any] = []

        def _mock_client_ctor(*_args: Any, **kwargs: Any) -> MagicMock:
            captured_options.append(kwargs["options"])
            return mock_claude_sdk_client

        resumed_payload = replace(
            sample_init_payload,
            sdk_session_id="eed8297f-26fb-4e00-905f-a10f0cf20704",
            sdk_session_data='{"type":"user","message":{"content":"test"}}\n',
        )

        with (
            patch(
                "tracecat.agent.runtime.claude_code.runtime.ClaudeSDKClient",
                side_effect=_mock_client_ctor,
            ),
            patch(
                "tracecat.agent.runtime.claude_code.runtime.create_proxy_mcp_server",
                AsyncMock(return_value={}),
            ),
        ):
            runtime = ClaudeAgentRuntime(mock_socket_writer)
            await runtime.run(resumed_payload)

        assert captured_options
        mcp_servers = captured_options[0].mcp_servers
        assert isinstance(mcp_servers, dict)
        assert "tracecat-registry" in mcp_servers
        assert "tracecat_registry" not in mcp_servers

    @pytest.mark.anyio
    async def test_write_session_file_canonicalizes_registry_mcp_aliases(
        self,
        mock_socket_writer: MagicMock,
    ) -> None:
        """Test that resume JSONL is rewritten to the canonical registry MCP name."""
        runtime = ClaudeAgentRuntime(mock_socket_writer)
        sdk_session_id = "eed8297f-26fb-4e00-905f-a10f0cf20704"
        runtime._cwd = (
            Path(tempfile.gettempdir())
            / "tracecat-agent-test-canonicalize-registry-mcp-alias"
        )
        sdk_session_data = (
            '{"type":"assistant","message":{"content":[{"type":"tool_use",'
            '"name":"mcp__tracecat_registry__execute_tool","input":{"tool_name":'
            '"mcp__tracecat_registry__core__http_request"}}]}}\n'
        )

        session_file = await runtime._write_session_file(
            sdk_session_id,
            sdk_session_data,
        )

        session_text = session_file.read_text()
        assert "mcp__tracecat-registry__execute_tool" in session_text
        assert "mcp__tracecat-registry__core__http_request" in session_text
        assert "mcp__tracecat_registry__" not in session_text


class TestClaudeAgentRuntimePreToolUseHook:
    """Tests for ClaudeAgentRuntime._pre_tool_use_hook().

    The hook uses these rules:
    1. Auto-approve if it's a user MCP tool (starts with mcp__ but not mcp__tracecat-registry__)
    2. Auto-approve if action is in registry_tools AND doesn't require approval
    3. Deny with reason if requires_approval is True
    4. Deny without reason otherwise (tool not allowed)
    """

    @pytest.mark.anyio
    async def test_auto_approve_user_mcp_tools(
        self,
        mock_socket_writer: MagicMock,
        sample_init_payload: RuntimeInitPayload,
    ) -> None:
        """Test that user MCP tools are auto-approved."""
        runtime = ClaudeAgentRuntime(mock_socket_writer)
        runtime.registry_tools = sample_init_payload.allowed_actions
        runtime.tool_approvals = sample_init_payload.config.tool_approvals

        # User MCP tool format: mcp__{server_name}__{tool_name}
        result = await runtime._pre_tool_use_hook(
            input_data=make_hook_input(
                tool_name="mcp__tracecat-registry__mcp__some_tool",
                tool_input={"arg": "value"},
                tool_use_id="call-1",
            ),
            tool_use_id="call-1",
            context=make_hook_context(),
        )

        hook_output = get_hook_output(result)
        assert hook_output == {
            "hookEventName": "PreToolUse",
            "permissionDecision": "allow",
        }

    @pytest.mark.anyio
    async def test_auto_approve_registry_tool_without_approval(
        self,
        mock_socket_writer: MagicMock,
        sample_init_payload: RuntimeInitPayload,
    ) -> None:
        """Test that registry tools are auto-approved when not requiring approval."""
        runtime = ClaudeAgentRuntime(mock_socket_writer)
        runtime.registry_tools = sample_init_payload.allowed_actions
        runtime.tool_approvals = {"core.http_request": False}  # No approval needed

        result = await runtime._pre_tool_use_hook(
            input_data=make_hook_input(
                # MCP tool name format: mcp__tracecat-registry__core__http_request
                tool_name="mcp__tracecat-registry__core__http_request",
                tool_input={"url": "https://example.com", "method": "GET"},
                tool_use_id="call-2",
            ),
            tool_use_id="call-2",
            context=make_hook_context(),
        )

        hook_output = get_hook_output(result)
        assert hook_output.get("permissionDecision") == "allow"
        assert hook_output["updatedInput"] == {
            "url": "https://example.com",
            "method": "GET",
            PROXY_TOOL_METADATA_KEY: {
                PROXY_TOOL_CALL_ID_KEY: "call-2",
            },
        }

    @pytest.mark.anyio
    async def test_auto_approve_internal_tool_does_not_inject_metadata(
        self,
        mock_socket_writer: MagicMock,
    ) -> None:
        runtime = ClaudeAgentRuntime(mock_socket_writer)

        result = await runtime._pre_tool_use_hook(
            input_data=make_hook_input(
                tool_name="mcp__tracecat-registry__internal__builder__list_sessions",
                tool_input={"query": "abc"},
                tool_use_id="call-internal",
            ),
            tool_use_id="call-internal",
            context=make_hook_context(),
        )

        hook_output = get_hook_output(result)
        assert hook_output == {
            "hookEventName": "PreToolUse",
            "permissionDecision": "allow",
        }

    @pytest.mark.anyio
    async def test_auto_approve_non_registry_mcp_tool_does_not_inject_metadata(
        self,
        mock_socket_writer: MagicMock,
    ) -> None:
        runtime = ClaudeAgentRuntime(mock_socket_writer)

        result = await runtime._pre_tool_use_hook(
            input_data=make_hook_input(
                tool_name="mcp__jira__search",
                tool_input={"jql": "project = TRACE"},
                tool_use_id="call-jira",
            ),
            tool_use_id="call-jira",
            context=make_hook_context(),
        )

        hook_output = get_hook_output(result)
        assert hook_output == {
            "hookEventName": "PreToolUse",
            "permissionDecision": "allow",
        }

    @pytest.mark.anyio
    async def test_tool_requires_approval(
        self,
        mock_socket_writer: MagicMock,
    ) -> None:
        """Test that tools marked for approval trigger approval request."""
        runtime = ClaudeAgentRuntime(mock_socket_writer)
        runtime.registry_tools = {
            "core.http_request": SharedMCPToolDefinition(
                name="core.http_request",
                description="Make HTTP request",
                parameters_json_schema={},
            )
        }
        runtime.tool_approvals = {"core.http_request": True}
        runtime.client = MagicMock()
        runtime.client.interrupt = AsyncMock()

        result = await runtime._pre_tool_use_hook(
            input_data=make_hook_input(
                tool_name="mcp__tracecat-registry__core__http_request",
                tool_input={"url": "https://example.com"},
                tool_use_id="call-3",
            ),
            tool_use_id="call-3",
            context=make_hook_context(),
        )

        hook_output = get_hook_output(result)
        assert hook_output.get("permissionDecision") == "deny"
        assert "requires approval" in (
            hook_output.get("permissionDecisionReason") or ""
        )

        # Should have sent approval request and interrupted
        mock_socket_writer.send_stream_event.assert_awaited()
        runtime.client.interrupt.assert_awaited()


def _jsonl_line(data: dict[str, Any]) -> str:
    """Encode a dict as a single JSONL line (no trailing newline)."""
    return orjson.dumps(data).decode("utf-8")


def _make_session_file(
    runtime: ClaudeAgentRuntime,
    sdk_session_id: str,
    lines: list[str],
) -> Path:
    """Write lines to the runtime's session file path and return the path."""
    path = runtime._get_session_file_path(sdk_session_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n" if lines else "")
    return path


class TestEmitNewSessionLines:
    """Tests for ClaudeAgentRuntime._emit_new_session_lines().

    Validates the byte-offset incremental reader: correct lines are emitted,
    the offset advances properly, and edge cases (empty file, incomplete
    writes, continuation flag) are handled.
    """

    SDK_SESSION_ID = "test-emit-session-001"

    @pytest.fixture
    def runtime(self, mock_socket_writer: MagicMock) -> ClaudeAgentRuntime:
        rt = ClaudeAgentRuntime(mock_socket_writer)
        rt._sdk_session_id = self.SDK_SESSION_ID
        rt._cwd = Path(tempfile.mkdtemp(prefix="tracecat-emit-test-"))
        return rt

    # -- basic emission --

    @pytest.mark.anyio
    async def test_emits_all_lines(
        self, runtime: ClaudeAgentRuntime, mock_socket_writer: MagicMock
    ) -> None:
        """All complete JSONL lines are emitted on the first call."""
        lines = [
            _jsonl_line({"type": "user", "message": {"content": "hi"}}),
            _jsonl_line(
                {
                    "type": "assistant",
                    "message": {"content": [{"type": "text", "text": "hello"}]},
                }
            ),
        ]
        _make_session_file(runtime, self.SDK_SESSION_ID, lines)

        await runtime._emit_new_session_lines()

        assert mock_socket_writer.send_session_line.await_count == 2
        # Verify lines sent in order
        sent_lines = [
            c.args[1] for c in mock_socket_writer.send_session_line.call_args_list
        ]
        assert sent_lines == lines

    # -- incremental reads --

    @pytest.mark.anyio
    async def test_incremental_offset_only_emits_new_lines(
        self, runtime: ClaudeAgentRuntime, mock_socket_writer: MagicMock
    ) -> None:
        """After first call, appending new lines only emits the new ones."""
        line1 = _jsonl_line({"type": "user", "message": {"content": "first"}})
        path = _make_session_file(runtime, self.SDK_SESSION_ID, [line1])

        await runtime._emit_new_session_lines()
        assert mock_socket_writer.send_session_line.await_count == 1
        mock_socket_writer.send_session_line.reset_mock()

        # Append a second line
        line2 = _jsonl_line(
            {
                "type": "assistant",
                "message": {"content": [{"type": "text", "text": "second"}]},
            }
        )
        with open(path, "a") as f:
            f.write(line2 + "\n")

        await runtime._emit_new_session_lines()
        assert mock_socket_writer.send_session_line.await_count == 1
        sent = mock_socket_writer.send_session_line.call_args_list[0].args[1]
        assert sent == line2

    # -- no-op cases --

    @pytest.mark.anyio
    async def test_noop_when_no_sdk_session_id(
        self, runtime: ClaudeAgentRuntime, mock_socket_writer: MagicMock
    ) -> None:
        runtime._sdk_session_id = None
        await runtime._emit_new_session_lines()
        mock_socket_writer.send_session_line.assert_not_awaited()

    @pytest.mark.anyio
    async def test_noop_when_file_missing(
        self, runtime: ClaudeAgentRuntime, mock_socket_writer: MagicMock
    ) -> None:
        # Don't create a file
        await runtime._emit_new_session_lines()
        mock_socket_writer.send_session_line.assert_not_awaited()

    @pytest.mark.anyio
    async def test_noop_when_no_new_data(
        self, runtime: ClaudeAgentRuntime, mock_socket_writer: MagicMock
    ) -> None:
        line = _jsonl_line({"type": "user", "message": {"content": "hi"}})
        _make_session_file(runtime, self.SDK_SESSION_ID, [line])

        await runtime._emit_new_session_lines()
        mock_socket_writer.send_session_line.reset_mock()

        # Second call with no file change
        await runtime._emit_new_session_lines()
        mock_socket_writer.send_session_line.assert_not_awaited()

    # -- empty / whitespace lines --

    @pytest.mark.anyio
    async def test_skips_empty_lines(
        self, runtime: ClaudeAgentRuntime, mock_socket_writer: MagicMock
    ) -> None:
        line = _jsonl_line({"type": "user", "message": {"content": "hi"}})
        path = runtime._get_session_file_path(self.SDK_SESSION_ID)
        path.parent.mkdir(parents=True, exist_ok=True)
        # Write with blank lines interspersed
        path.write_text(f"\n\n{line}\n\n")

        await runtime._emit_new_session_lines()
        assert mock_socket_writer.send_session_line.await_count == 1

    # -- incomplete writes --

    @pytest.mark.anyio
    async def test_stops_at_incomplete_line_without_newline(
        self, runtime: ClaudeAgentRuntime, mock_socket_writer: MagicMock
    ) -> None:
        """A line not terminated by newline is treated as incomplete and retried."""
        complete = _jsonl_line({"type": "user", "message": {"content": "done"}})
        incomplete = '{"type":"assistant","message":'  # no closing brace, no newline
        path = runtime._get_session_file_path(self.SDK_SESSION_ID)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"{complete}\n{incomplete}")

        await runtime._emit_new_session_lines()
        # Only the complete line should be emitted
        assert mock_socket_writer.send_session_line.await_count == 1
        mock_socket_writer.send_session_line.reset_mock()

        # Now "complete" the write by finishing the line
        finished = _jsonl_line(
            {
                "type": "assistant",
                "message": {"content": [{"type": "text", "text": "ok"}]},
            }
        )
        path.write_text(f"{complete}\n{finished}\n")

        await runtime._emit_new_session_lines()
        assert mock_socket_writer.send_session_line.await_count == 1
        sent = mock_socket_writer.send_session_line.call_args_list[0].args[1]
        assert sent == finished

    @pytest.mark.anyio
    async def test_stops_at_invalid_json(
        self, runtime: ClaudeAgentRuntime, mock_socket_writer: MagicMock
    ) -> None:
        """Malformed JSON on a newline-terminated line stops processing."""
        good = _jsonl_line({"type": "user", "message": {"content": "hi"}})
        bad = "{not valid json"
        after = _jsonl_line({"type": "user", "message": {"content": "bye"}})
        path = runtime._get_session_file_path(self.SDK_SESSION_ID)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"{good}\n{bad}\n{after}\n")

        await runtime._emit_new_session_lines()
        # Only the line before the bad one is emitted
        assert mock_socket_writer.send_session_line.await_count == 1

    # -- internal classification --

    @pytest.mark.anyio
    async def test_marks_internal_lines(
        self, runtime: ClaudeAgentRuntime, mock_socket_writer: MagicMock
    ) -> None:
        """System messages and queue-operation lines are marked internal."""
        system_line = _jsonl_line({"type": "system", "message": {"content": "init"}})
        user_line = _jsonl_line({"type": "user", "message": {"content": "hi"}})
        _make_session_file(runtime, self.SDK_SESSION_ID, [system_line, user_line])

        await runtime._emit_new_session_lines()

        calls = mock_socket_writer.send_session_line.call_args_list
        assert len(calls) == 2
        # System line → internal=True
        assert calls[0] == call(self.SDK_SESSION_ID, system_line, internal=True)
        # User line → internal=False
        assert calls[1] == call(self.SDK_SESSION_ID, user_line, internal=False)

    # -- continuation flag --

    @pytest.mark.anyio
    async def test_continuation_marks_first_user_message_internal(
        self, runtime: ClaudeAgentRuntime, mock_socket_writer: MagicMock
    ) -> None:
        """When _is_continuation is set, the first user message is internal."""
        runtime._is_continuation = True
        user1 = _jsonl_line({"type": "user", "message": {"content": "continuation"}})
        user2 = _jsonl_line({"type": "user", "message": {"content": "real"}})
        _make_session_file(runtime, self.SDK_SESSION_ID, [user1, user2])

        await runtime._emit_new_session_lines()

        calls = mock_socket_writer.send_session_line.call_args_list
        assert len(calls) == 2
        # First user message marked internal
        assert calls[0] == call(self.SDK_SESSION_ID, user1, internal=True)
        # Second user message is visible
        assert calls[1] == call(self.SDK_SESSION_ID, user2, internal=False)
        # Flag consumed
        assert runtime._is_continuation is False

    # -- byte offset correctness --

    @pytest.mark.anyio
    async def test_byte_offset_tracks_exact_bytes(
        self, runtime: ClaudeAgentRuntime, mock_socket_writer: MagicMock
    ) -> None:
        """Byte offset matches the exact bytes consumed (including newlines)."""
        line = _jsonl_line({"type": "user", "message": {"content": "test"}})
        _make_session_file(runtime, self.SDK_SESSION_ID, [line])

        await runtime._emit_new_session_lines()

        expected_offset = len(line.encode("utf-8")) + 1  # +1 for \n
        assert runtime._last_seen_byte_offset == expected_offset

    @pytest.mark.anyio
    async def test_byte_offset_with_multibyte_utf8(
        self, runtime: ClaudeAgentRuntime, mock_socket_writer: MagicMock
    ) -> None:
        """Byte offset is correct for content with multibyte UTF-8 characters."""
        line = _jsonl_line({"type": "user", "message": {"content": "hello 🔥 world"}})
        _make_session_file(runtime, self.SDK_SESSION_ID, [line])

        await runtime._emit_new_session_lines()

        expected_offset = len(line.encode("utf-8")) + 1
        assert runtime._last_seen_byte_offset == expected_offset
        mock_socket_writer.send_session_line.assert_awaited_once()

    @pytest.mark.anyio
    async def test_preseeded_offset_skips_existing_lines(
        self, runtime: ClaudeAgentRuntime, mock_socket_writer: MagicMock
    ) -> None:
        """Pre-seeding _last_seen_byte_offset skips already-persisted data."""
        old_line = _jsonl_line({"type": "user", "message": {"content": "old"}})
        new_line = _jsonl_line(
            {
                "type": "assistant",
                "message": {"content": [{"type": "text", "text": "new"}]},
            }
        )
        path = runtime._get_session_file_path(self.SDK_SESSION_ID)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"{old_line}\n{new_line}\n")

        # Pre-seed offset past the old line
        runtime._last_seen_byte_offset = len(old_line.encode("utf-8")) + 1

        await runtime._emit_new_session_lines()

        assert mock_socket_writer.send_session_line.await_count == 1
        sent = mock_socket_writer.send_session_line.call_args_list[0].args[1]
        assert sent == new_line
