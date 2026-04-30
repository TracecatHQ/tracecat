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
from unittest.mock import AsyncMock, MagicMock, patch

import orjson
import pytest
from claude_agent_sdk.types import (
    HookContext,
    PreToolUseHookInput,
    ResultMessage,
    StopHookInput,
    StreamEvent,
    SyncHookJSONOutput,
)

import tracecat.agent.runtime.claude_code.runtime as runtime_module
from tracecat.agent.common.protocol import RuntimeInitPayload, RuntimeToolResult
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
from tracecat.agent.runtime.claude_code.runtime import (
    CLAUDE_SDK_MAX_BUFFER_SIZE_BYTES,
    ClaudeAgentRuntime,
    get_litellm_route_model,
)
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


@pytest.mark.parametrize(
    ("provider", "model_name", "passthrough", "expected"),
    [
        (
            "anthropic",
            "claude-sonnet-4-5-20250929",
            False,
            "anthropic/claude-sonnet-4-5-20250929",
        ),
        ("bedrock", "bedrock", False, "bedrock/bedrock"),
        (
            "bedrock",
            "us.anthropic.claude-sonnet-4-20250514-v1:0",
            False,
            "bedrock/us.anthropic.claude-sonnet-4-20250514-v1:0",
        ),
        ("azure_openai", "my-deployment", False, "azure/my-deployment"),
        ("custom-model-provider", "custom", False, "custom"),
        ("custom-model-provider", "customer-alias", True, "customer-alias"),
        ("openai", "openai/gpt-5", False, "openai/gpt-5"),
    ],
)
def test_get_litellm_route_model_prefixes_provider_route(
    provider: str,
    model_name: str,
    passthrough: bool,
    expected: str,
) -> None:
    assert (
        get_litellm_route_model(
            model_provider=provider,
            model_name=model_name,
            passthrough=passthrough,
        )
        == expected
    )


class TestClaudeAgentRuntimeRun:
    """Tests for ClaudeAgentRuntime.run()."""

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
            runtime = ClaudeAgentRuntime(
                mock_socket_writer, transport_factory=lambda _: MagicMock()
            )
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
            runtime = ClaudeAgentRuntime(
                mock_socket_writer, transport_factory=lambda _: MagicMock()
            )
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
            runtime = ClaudeAgentRuntime(
                mock_socket_writer, transport_factory=lambda _: MagicMock()
            )
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
            runtime = ClaudeAgentRuntime(
                mock_socket_writer, transport_factory=lambda _: MagicMock()
            )
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
            runtime = ClaudeAgentRuntime(
                mock_socket_writer, transport_factory=lambda _: MagicMock()
            )
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
            runtime = ClaudeAgentRuntime(
                mock_socket_writer, transport_factory=lambda _: MagicMock()
            )
            await runtime.run(resumed_payload)

        assert captured_options
        mcp_servers = captured_options[0].mcp_servers
        assert isinstance(mcp_servers, dict)
        assert "tracecat-registry" in mcp_servers
        assert "tracecat_registry" not in mcp_servers

    @pytest.mark.anyio
    async def test_sets_max_buffer_size_on_sdk_options(
        self,
        mock_socket_writer: MagicMock,
        mock_claude_sdk_client: MagicMock,
        sample_init_payload: RuntimeInitPayload,
    ) -> None:
        """Test that runtime overrides the SDK buffer limit for larger outputs."""
        captured_options: list[Any] = []

        def _mock_client_ctor(*_args: Any, **kwargs: Any) -> MagicMock:
            captured_options.append(kwargs["options"])
            return mock_claude_sdk_client

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
            runtime = ClaudeAgentRuntime(
                mock_socket_writer, transport_factory=lambda _: MagicMock()
            )
            await runtime.run(sample_init_payload)

        assert captured_options
        assert captured_options[0].max_buffer_size == CLAUDE_SDK_MAX_BUFFER_SIZE_BYTES

    @pytest.mark.anyio
    async def test_sets_auto_compact_window_for_custom_model_provider(
        self,
        mock_socket_writer: MagicMock,
        mock_claude_sdk_client: MagicMock,
        sample_init_payload: RuntimeInitPayload,
    ) -> None:
        """Custom model providers should lower Claude Code's auto-compact window."""
        captured_options: list[Any] = []

        def _mock_client_ctor(*_args: Any, **kwargs: Any) -> MagicMock:
            captured_options.append(kwargs["options"])
            return mock_claude_sdk_client

        custom_payload = replace(
            sample_init_payload,
            config=replace(
                sample_init_payload.config,
                model_provider="custom-model-provider",
            ),
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
            runtime = ClaudeAgentRuntime(
                mock_socket_writer, transport_factory=lambda _: MagicMock()
            )
            await runtime.run(custom_payload)

        assert captured_options
        assert captured_options[0].env["CLAUDE_CODE_AUTO_COMPACT_WINDOW"] == "128000"

    @pytest.mark.parametrize(
        "disable_nsjail",
        [
            pytest.param(True, id="direct"),
            pytest.param(False, id="nsjail"),
        ],
    )
    @pytest.mark.anyio
    async def test_run_invokes_approval_hook_from_sdk_turn_in_each_sandbox_mode(
        self,
        monkeypatch: pytest.MonkeyPatch,
        mock_socket_writer: MagicMock,
        sample_init_payload: RuntimeInitPayload,
        disable_nsjail: bool,
    ) -> None:
        """A mocked SDK turn should still drive runtime approval interrupts."""
        monkeypatch.setattr(runtime_module, "TRACECAT__DISABLE_NSJAIL", disable_nsjail)
        captured_options: list[Any] = []

        class ApprovalHookClient:
            def __init__(self, options: Any) -> None:
                self.options = options
                self.query = AsyncMock(side_effect=self._query)
                self.interrupt = AsyncMock()

            async def __aenter__(self) -> ApprovalHookClient:
                return self

            async def __aexit__(
                self,
                exc_type: object,
                exc: object,
                tb: object,
            ) -> None:
                del exc_type, exc, tb

            async def _query(self, _prompt: str) -> None:
                [matcher] = self.options.hooks["PreToolUse"]
                [hook] = matcher.hooks
                await hook(
                    input_data=make_hook_input(
                        tool_name="mcp__tracecat-registry__core__http_request",
                        tool_input={"url": "https://example.com", "method": "GET"},
                        tool_use_id="call-approval",
                    ),
                    tool_use_id="call-approval",
                    context=make_hook_context(),
                )

            async def receive_response(self) -> Any:
                return
                yield  # pragma: no cover  # noqa: B901

        clients: list[ApprovalHookClient] = []

        def _mock_client_ctor(*_args: Any, **kwargs: Any) -> ApprovalHookClient:
            captured_options.append(kwargs["options"])
            client = ApprovalHookClient(kwargs["options"])
            clients.append(client)
            return client

        approval_payload = replace(
            sample_init_payload,
            config=replace(
                sample_init_payload.config,
                tool_approvals={"core.http_request": True},
            ),
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
            runtime = ClaudeAgentRuntime(
                mock_socket_writer, transport_factory=lambda _: MagicMock()
            )
            await runtime.run(approval_payload)

        assert captured_options
        assert captured_options[0].sandbox["enabled"] is disable_nsjail
        [client] = clients
        client.query.assert_awaited_once_with(approval_payload.user_prompt)
        client.interrupt.assert_awaited_once()

        approval_events = [
            call.args[0]
            for call in mock_socket_writer.send_stream_event.await_args_list
            if call.args[0].type == StreamEventType.APPROVAL_REQUEST
        ]
        assert len(approval_events) == 1
        [approval_item] = approval_events[0].approval_items or []
        assert approval_item.id == "call-approval"
        assert approval_item.name == "core.http_request"
        assert approval_item.input == {
            "url": "https://example.com",
            "method": "GET",
        }

    @pytest.mark.parametrize(
        "disable_nsjail",
        [
            pytest.param(True, id="direct"),
            pytest.param(False, id="nsjail"),
        ],
    )
    @pytest.mark.anyio
    async def test_approval_continuation_sends_tool_result_input_in_each_sandbox_mode(
        self,
        monkeypatch: pytest.MonkeyPatch,
        mock_socket_writer: MagicMock,
        mock_claude_sdk_client: MagicMock,
        sample_init_payload: RuntimeInitPayload,
        tmp_path: Path,
        disable_nsjail: bool,
    ) -> None:
        """Approval continuations send tool_result blocks as SDK input."""
        monkeypatch.setattr(runtime_module, "TRACECAT__DISABLE_NSJAIL", disable_nsjail)
        captured_options: list[Any] = []

        def _mock_client_ctor(*_args: Any, **kwargs: Any) -> MagicMock:
            captured_options.append(kwargs["options"])
            return mock_claude_sdk_client

        continued_payload = replace(
            sample_init_payload,
            sdk_session_id="eed8297f-26fb-4e00-905f-a10f0cf20704",
            sdk_session_data=(
                '{"type":"assistant","message":{"content":[{"type":"tool_use",'
                '"id":"call_123","name":"core__http_request","input":{}}]}}\n'
            ),
            is_approval_continuation=True,
            approval_tool_results=[
                RuntimeToolResult(
                    tool_call_id="call_123",
                    content='{"status":"success"}',
                    is_error=False,
                )
            ],
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
            runtime = ClaudeAgentRuntime(
                mock_socket_writer,
                transport_factory=lambda _options: MagicMock(),
                session_home_dir=tmp_path / "claude-home",
                cwd=tmp_path / "claude-project",
                cwd_setup_path=tmp_path / "claude-project",
            )
            await runtime.run(continued_payload)

        assert captured_options
        assert captured_options[0].resume == continued_payload.sdk_session_id
        assert captured_options[0].fork_session is False
        assert captured_options[0].sandbox["enabled"] is disable_nsjail
        mock_claude_sdk_client.query.assert_awaited_once()
        query_input = mock_claude_sdk_client.query.await_args.args[0]
        assert not isinstance(query_input, str)
        messages = [message async for message in query_input]
        assert messages == [
            {
                "type": "user",
                "message": {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "call_123",
                            "content": '{"status":"success"}',
                            "is_error": False,
                        }
                    ],
                },
                "parent_tool_use_id": None,
                "session_id": "default",
            }
        ]

    @pytest.mark.parametrize(
        "disable_nsjail",
        [
            pytest.param(True, id="direct"),
            pytest.param(False, id="nsjail"),
        ],
    )
    @pytest.mark.anyio
    async def test_forked_resume_sets_sdk_fork_flag_and_skips_parent_history(
        self,
        monkeypatch: pytest.MonkeyPatch,
        mock_socket_writer: MagicMock,
        mock_claude_sdk_client: MagicMock,
        sample_init_payload: RuntimeInitPayload,
        tmp_path: Path,
        disable_nsjail: bool,
    ) -> None:
        """Forked sessions should pass fork_session=True and not re-emit parent JSONL."""
        monkeypatch.setattr(runtime_module, "TRACECAT__DISABLE_NSJAIL", disable_nsjail)
        captured_options: list[Any] = []

        parent_sdk_session_id = "eed8297f-26fb-4e00-905f-a10f0cf20704"
        child_sdk_session_id = "eed8297f-26fb-4e00-905f-a10f0cf20705"
        parent_history = (
            '{"type":"user","message":{"content":"parent prompt"}}\n'
            '{"type":"assistant","message":{"content":[{"type":"text","text":"parent answer"}]}}\n'
        )
        fork_payload = replace(
            sample_init_payload,
            sdk_session_id=parent_sdk_session_id,
            sdk_session_data=parent_history,
            is_fork=True,
        )

        session_home_dir = tmp_path / "claude-home"
        runtime_cwd = tmp_path / "claude-project"
        runtime = ClaudeAgentRuntime(
            mock_socket_writer,
            transport_factory=lambda _options: MagicMock(),
            session_home_dir=session_home_dir,
            cwd=runtime_cwd,
            cwd_setup_path=runtime_cwd,
        )
        child_session_file = runtime._get_session_file_path(child_sdk_session_id)
        child_session_file.parent.mkdir(parents=True, exist_ok=True)
        child_session_file.write_text(parent_history)

        async def mock_receive() -> Any:
            yield StreamEvent(
                uuid="stream-1",
                session_id=child_sdk_session_id,
                event={
                    "type": "content_block_delta",
                    "index": 0,
                    "delta": {"type": "text_delta", "text": "forked"},
                },
            )
            yield ResultMessage(
                subtype="success",
                duration_ms=1,
                duration_api_ms=1,
                is_error=False,
                num_turns=1,
                session_id=child_sdk_session_id,
                usage={},
                result="done",
            )

        mock_claude_sdk_client.receive_response = mock_receive

        def _mock_client_ctor(*_args: Any, **kwargs: Any) -> MagicMock:
            captured_options.append(kwargs["options"])
            return mock_claude_sdk_client

        mock_adapter = MagicMock()
        mock_adapter.to_unified_event.return_value = UnifiedStreamEvent(
            type=StreamEventType.TEXT_DELTA,
            text="forked",
            part_id=0,
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
            patch(
                "tracecat.agent.runtime.claude_code.runtime.ClaudeSDKAdapter",
                return_value=mock_adapter,
            ),
        ):
            await runtime.run(fork_payload)

        assert captured_options
        assert captured_options[0].resume == parent_sdk_session_id
        assert captured_options[0].fork_session is True
        assert captured_options[0].sandbox["enabled"] is disable_nsjail
        mock_claude_sdk_client.query.assert_awaited_once_with(
            sample_init_payload.user_prompt
        )
        mock_socket_writer.send_session_line.assert_not_awaited()
        mock_socket_writer.send_result.assert_awaited_once()

    @pytest.mark.anyio
    async def test_resume_writes_session_file_when_using_custom_transport(
        self,
        mock_socket_writer: MagicMock,
        mock_claude_sdk_client: MagicMock,
        sample_init_payload: RuntimeInitPayload,
        tmp_path: Path,
    ) -> None:
        """Broker mode still needs a local session file for Claude resume."""
        resumed_payload = replace(
            sample_init_payload,
            sdk_session_id="eed8297f-26fb-4e00-905f-a10f0cf20704",
            sdk_session_data='{"type":"user","message":{"content":"resume"}}\n',
        )
        session_home_dir = tmp_path / "claude-home"
        runtime_cwd = tmp_path / "claude-project"

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
            runtime = ClaudeAgentRuntime(
                mock_socket_writer,
                transport_factory=lambda _options: MagicMock(),
                session_home_dir=session_home_dir,
                cwd=runtime_cwd,
                cwd_setup_path=runtime_cwd,
            )
            await runtime.run(resumed_payload)

        session_file = (
            session_home_dir
            / ".claude"
            / "projects"
            / str(runtime_cwd).replace("/", "-")
            / "eed8297f-26fb-4e00-905f-a10f0cf20704.jsonl"
        )
        assert session_file.exists()
        assert session_file.read_text() == resumed_payload.sdk_session_data

    @pytest.mark.anyio
    async def test_does_not_set_host_home_when_custom_transport_is_configured(
        self,
        mock_socket_writer: MagicMock,
        mock_claude_sdk_client: MagicMock,
        sample_init_payload: RuntimeInitPayload,
        tmp_path: Path,
    ) -> None:
        captured_options = []
        session_home_dir = tmp_path / "claude-home"
        runtime_cwd = tmp_path / "claude-project"

        def _mock_client_ctor(*_args: Any, **kwargs: Any) -> MagicMock:
            captured_options.append(kwargs["options"])
            return mock_claude_sdk_client

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
            runtime = ClaudeAgentRuntime(
                mock_socket_writer,
                transport_factory=lambda _options: MagicMock(),
                session_home_dir=session_home_dir,
                cwd=runtime_cwd,
                cwd_setup_path=runtime_cwd,
            )
            await runtime.run(sample_init_payload)

        assert captured_options
        assert "HOME" not in captured_options[0].env

    @pytest.mark.anyio
    async def test_write_session_file_canonicalizes_registry_mcp_aliases(
        self,
        mock_socket_writer: MagicMock,
    ) -> None:
        """Test that resume JSONL is rewritten to the canonical registry MCP name."""
        runtime = ClaudeAgentRuntime(
            mock_socket_writer, transport_factory=lambda _: MagicMock()
        )
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
        runtime = ClaudeAgentRuntime(
            mock_socket_writer, transport_factory=lambda _: MagicMock()
        )
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
        runtime = ClaudeAgentRuntime(
            mock_socket_writer, transport_factory=lambda _: MagicMock()
        )
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
        runtime = ClaudeAgentRuntime(
            mock_socket_writer, transport_factory=lambda _: MagicMock()
        )

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
        runtime = ClaudeAgentRuntime(
            mock_socket_writer, transport_factory=lambda _: MagicMock()
        )

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
        runtime = ClaudeAgentRuntime(
            mock_socket_writer, transport_factory=lambda _: MagicMock()
        )
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


class TestClaudeAgentRuntimeStopHook:
    """Tests for ClaudeAgentRuntime._stop_hook().

    The Stop hook guards against structured-output death loops: the CLI re-invokes
    the model when its final message fails a stop-hook check (e.g. schema
    validation). Without a cap the loop can run indefinitely.
    """

    @staticmethod
    def _make_stop_input(*, stop_hook_active: bool) -> StopHookInput:
        return {
            "session_id": "test-session-id",
            "transcript_path": "/tmp/test-transcript.jsonl",
            "cwd": "/tmp",
            "hook_event_name": "Stop",
            "stop_hook_active": stop_hook_active,
        }

    @pytest.mark.anyio
    async def test_natural_stop_passes_through(
        self,
        mock_socket_writer: MagicMock,
    ) -> None:
        """A natural stop (stop_hook_active=False) must not count against the cap."""
        runtime = ClaudeAgentRuntime(
            mock_socket_writer, transport_factory=lambda _: MagicMock()
        )

        result = await runtime._stop_hook(
            input_data=self._make_stop_input(stop_hook_active=False),
            tool_use_id=None,
            context=make_hook_context(),
        )

        assert result == {}
        assert runtime._stop_hook_retries == 0

    @pytest.mark.anyio
    async def test_allows_retries_up_to_cap(
        self,
        mock_socket_writer: MagicMock,
    ) -> None:
        """The first MAX_STOP_HOOK_RETRIES active retries pass through unchanged."""
        from tracecat.agent.runtime.claude_code.runtime import MAX_STOP_HOOK_RETRIES

        runtime = ClaudeAgentRuntime(
            mock_socket_writer, transport_factory=lambda _: MagicMock()
        )

        for _ in range(MAX_STOP_HOOK_RETRIES):
            result = await runtime._stop_hook(
                input_data=self._make_stop_input(stop_hook_active=True),
                tool_use_id=None,
                context=make_hook_context(),
            )
            assert result == {}

        assert runtime._stop_hook_retries == MAX_STOP_HOOK_RETRIES

    @pytest.mark.anyio
    async def test_terminates_turn_when_cap_exceeded(
        self,
        mock_socket_writer: MagicMock,
    ) -> None:
        """Once retries exceed the cap, the hook must stop the turn cleanly."""
        from tracecat.agent.runtime.claude_code.runtime import MAX_STOP_HOOK_RETRIES

        runtime = ClaudeAgentRuntime(
            mock_socket_writer, transport_factory=lambda _: MagicMock()
        )
        runtime._stop_hook_retries = MAX_STOP_HOOK_RETRIES

        result = await runtime._stop_hook(
            input_data=self._make_stop_input(stop_hook_active=True),
            tool_use_id=None,
            context=make_hook_context(),
        )

        assert result.get("continue_") is False
        assert "retry cap" in (result.get("stopReason") or "").lower()
        mock_socket_writer.send_log.assert_awaited()


class TestClaudeAgentRuntimeInternalSessionLines:
    """Tests for ClaudeAgentRuntime internal session line filtering."""

    def test_does_not_hide_plain_text_that_mentions_summary_phrase(
        self,
        mock_socket_writer: MagicMock,
    ) -> None:
        """Natural-language text should not be hidden as a compaction artifact."""
        runtime = ClaudeAgentRuntime(
            mock_socket_writer, transport_factory=lambda _: MagicMock()
        )

        line_data = {
            "type": "assistant",
            "message": {
                "content": [
                    {
                        "type": "text",
                        "text": "Please provide your summary based on the conversation so far.",
                    }
                ]
            },
        }

        assert runtime._is_internal_session_line(line_data) is False

    def test_hides_structured_compaction_artifacts(
        self,
        mock_socket_writer: MagicMock,
    ) -> None:
        """Structured Claude compaction markup should remain internal."""
        runtime = ClaudeAgentRuntime(
            mock_socket_writer, transport_factory=lambda _: MagicMock()
        )

        line_data = {
            "type": "assistant",
            "message": {
                "content": [
                    {
                        "type": "text",
                        "text": "<command-name>/compact</command-name>",
                    }
                ]
            },
        }

        assert runtime._is_internal_session_line(line_data) is True

    @pytest.mark.anyio
    async def test_approval_continuation_hides_sdk_meta_prompt_and_reasoning(
        self,
        mock_socket_writer: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Approval continuation control rows should not show in chat or traces."""
        runtime = ClaudeAgentRuntime(
            mock_socket_writer,
            transport_factory=lambda _: MagicMock(),
            session_home_dir=tmp_path / "claude-home",
            cwd=tmp_path / "claude-project",
        )
        sdk_session_id = "eed8297f-26fb-4e00-905f-a10f0cf20704"
        runtime._sdk_session_id = sdk_session_id
        runtime._approval_continuation_active = True

        tool_result_uuid = "tool-result-uuid"
        meta_uuid = "meta-uuid"
        synthetic_uuid = "synthetic-uuid"
        prompt_uuid = "prompt-uuid"
        thinking_uuid = "thinking-uuid"
        answer_uuid = "answer-uuid"
        lines = [
            {
                "type": "user",
                "uuid": tool_result_uuid,
                "message": {
                    "role": "user",
                    "content": [{"type": "tool_result", "tool_use_id": "call_123"}],
                },
            },
            {
                "type": "user",
                "uuid": meta_uuid,
                "isMeta": True,
                "parentUuid": tool_result_uuid,
                "message": {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": "Continue from where you left off.",
                        }
                    ],
                },
            },
            {
                "type": "assistant",
                "uuid": synthetic_uuid,
                "parentUuid": meta_uuid,
                "message": {"model": "<synthetic>", "content": []},
            },
            {
                "type": "user",
                "uuid": prompt_uuid,
                "parentUuid": synthetic_uuid,
                "message": {
                    "role": "user",
                    "content": runtime_module.APPROVAL_CONTINUATION_PROMPT,
                },
            },
            {
                "type": "assistant",
                "uuid": thinking_uuid,
                "parentUuid": prompt_uuid,
                "message": {
                    "content": [
                        {
                            "type": "thinking",
                            "thinking": "Saw hidden continuation prompts.",
                        }
                    ]
                },
            },
            {
                "type": "assistant",
                "uuid": answer_uuid,
                "parentUuid": thinking_uuid,
                "message": {
                    "content": [
                        {
                            "type": "text",
                            "text": "There are no cases.",
                        }
                    ]
                },
            },
        ]

        session_file = runtime._get_session_file_path(sdk_session_id)
        session_file.parent.mkdir(parents=True, exist_ok=True)
        session_file.write_text(
            "\n".join(orjson.dumps(line).decode("utf-8") for line in lines) + "\n"
        )

        await runtime._emit_new_session_lines()

        persisted = [
            (orjson.loads(call.args[1]), call.kwargs["internal"])
            for call in mock_socket_writer.send_session_line.await_args_list
        ]
        internal_by_uuid = {
            line["uuid"]: internal
            for line, internal in persisted
            if isinstance(line.get("uuid"), str)
        }
        assert internal_by_uuid[tool_result_uuid] is False
        assert internal_by_uuid[meta_uuid] is True
        assert internal_by_uuid[synthetic_uuid] is True
        assert internal_by_uuid[prompt_uuid] is True
        assert internal_by_uuid[thinking_uuid] is True
        assert internal_by_uuid[answer_uuid] is False
