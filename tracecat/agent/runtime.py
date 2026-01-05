"""Agent runtimes for Tracecat.

This module provides:
- ClaudeAgentRuntime: Stateless, sandboxed runtime for Claude SDK agents
- run_agent: Legacy sync agent execution using Pydantic AI
- run_agent_sync: Synchronous agent execution helper
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path
from timeit import default_timer
from typing import Any

from claude_agent_sdk import (
    ClaudeAgentOptions,
    ClaudeSDKClient,
    HookMatcher,
)
from claude_agent_sdk.types import (
    HookContext,
    HookInput,
    StreamEvent,
    SyncHookJSONOutput,
    ToolResultBlock,
    UserMessage,
)
from pydantic_ai import Agent, UsageLimits
from pydantic_ai.tools import DeferredToolResults

from tracecat.agent.adapter.claude import ClaudeSDKAdapter
from tracecat.agent.exceptions import AgentRunError
from tracecat.agent.executor.aio import AioStreamingAgentExecutor
from tracecat.agent.mcp.proxy_server import create_proxy_mcp_server
from tracecat.agent.mcp.types import MCPToolDefinition
from tracecat.agent.parsers import try_parse_json
from tracecat.agent.sandbox.exceptions import AgentSandboxValidationError
from tracecat.agent.sandbox.protocol import RuntimeInitPayload
from tracecat.agent.sandbox.socket_io import SocketStreamWriter
from tracecat.agent.schemas import AgentOutput, RunAgentArgs
from tracecat.agent.stream.common import PersistableStreamingAgentDeps
from tracecat.agent.stream.types import (
    StreamEventType,
    ToolCallContent,
    UnifiedStreamEvent,
)
from tracecat.agent.types import (
    AgentConfig,
    AgentRuntime,
    MCPServerConfig,
    OutputType,
)
from tracecat.config import (
    TRACECAT__AGENT_MAX_REQUESTS,
    TRACECAT__AGENT_MAX_RETRIES,
    TRACECAT__AGENT_MAX_TOOL_CALLS,
)
from tracecat.contexts import ctx_role, ctx_session_id
from tracecat.exceptions import TracecatAuthorizationError
from tracecat.logger import logger

_REGISTRY_META_TOOL_GUIDANCE = """
<TOOLING>
Tracecat registry access is exposed via a small set of MCP meta-tools (to avoid loading 400+ tools into context).

When you need to use a Tracecat registry action:
- First call `mcp__tracecat-registry__list_tools` to discover candidate tools. Prefer filtering by `namespace` (e.g. "core", "tools.slack") and/or `search`.
- Then call `mcp__tracecat-registry__get_tool_schema` with {"tool_name": "<full.tool.name>"} to see the required/optional input fields.
- Finally call `mcp__tracecat-registry__execute_tool` with:
  {
    "tool_name": "<full.tool.name>",
    "args": { ... validated arguments matching the schema ... }
  }
</TOOLING>
""".strip()


def _build_claude_system_prompt(base_prompt: str | None) -> str:
    """Build the Claude system prompt with Tracecat MCP tool guidance appended."""
    if base_prompt and base_prompt.strip():
        return base_prompt.strip() + "\n\n" + _REGISTRY_META_TOOL_GUIDANCE
    return _REGISTRY_META_TOOL_GUIDANCE


async def run_agent_sync(
    agent: Agent[Any, Any],
    user_prompt: str,
    max_requests: int,
    max_tools_calls: int | None = None,
    *,
    deferred_tool_results: DeferredToolResults | None = None,
) -> AgentOutput:
    """Run an agent synchronously."""

    if max_tools_calls and max_tools_calls > TRACECAT__AGENT_MAX_TOOL_CALLS:
        raise ValueError(
            f"Cannot request more than {TRACECAT__AGENT_MAX_TOOL_CALLS} tool calls"
        )
    if max_requests > TRACECAT__AGENT_MAX_REQUESTS:
        raise ValueError(
            f"Cannot request more than {TRACECAT__AGENT_MAX_REQUESTS} requests"
        )

    start_time = default_timer()
    usage = UsageLimits(request_limit=max_requests, tool_calls_limit=max_tools_calls)
    result = await agent.run(
        user_prompt,
        usage_limits=usage,
        deferred_tool_results=deferred_tool_results,
    )
    end_time = default_timer()
    return AgentOutput(
        output=try_parse_json(result.output),
        message_history=result.all_messages(),
        duration=end_time - start_time,
        usage=result.usage(),
        session_id=uuid.uuid4(),
    )


async def run_agent(
    user_prompt: str,
    model_name: str,
    model_provider: str,
    actions: list[str] | None = None,
    namespaces: list[str] | None = None,
    tool_approvals: dict[str, bool] | None = None,
    mcp_server_url: str | None = None,
    mcp_server_headers: dict[str, str] | None = None,
    mcp_servers: list[MCPServerConfig] | None = None,
    instructions: str | None = None,
    output_type: OutputType | None = None,
    model_settings: dict[str, Any] | None = None,
    max_tool_calls: int = TRACECAT__AGENT_MAX_TOOL_CALLS,
    max_requests: int = TRACECAT__AGENT_MAX_REQUESTS,
    retries: int = TRACECAT__AGENT_MAX_RETRIES,
    base_url: str | None = None,
    deferred_tool_results: DeferredToolResults | None = None,
) -> AgentOutput:
    """Run an AI agent with specified configuration and actions.

    This function creates and executes a Tracecat AI agent with the provided
    model configuration, actions, and optional file attachments. It handles
    instruction enhancement, temporary file management, and optional Redis
    streaming for real-time execution updates.

    Args:
        user_prompt: The main prompt/message for the agent to process.
        model_name: Name of the LLM model to use (e.g., "gpt-4", "claude-3").
        model_provider: Provider of the model (e.g., "openai", "anthropic").
        actions: List of action names to make available to the agent
                (e.g., ["tools.slack.post_message", "tools.github.create_issue"]).
        namespaces: Optional list of namespaces to restrict available tools.
        tool_approvals: Optional per-tool approval requirements keyed by action name.
        instructions: Optional system instructions/context for the agent.
                     If provided, will be enhanced with tool guidance and error handling.
        mcp_server_url: (Legacy) Optional URL of the MCP server to use.
        mcp_server_headers: (Legacy) Optional headers for the MCP server.
        mcp_servers: Optional list of MCP server configurations (preferred over legacy params).
        output_type: Optional specification for the agent's output format.
                    Can be a string type name or a structured dictionary schema.
                    Supported types: bool, float, int, str, list[bool], list[float], list[int], list[str]
        model_settings: Optional model-specific configuration parameters
                       (temperature, max_tokens, etc.).
        max_tools_calls: Maximum number of tool calls to make per agent run (default: 5).
        max_requests: Maximum number of requests to make per agent run (default: 20).
        retries: Maximum number of retry attempts for agent execution (default: 3).
        base_url: Optional custom base URL for the model provider's API.
        stream_id: Optional identifier for Redis streaming of execution events.
                  If provided, execution steps will be streamed to Redis.

    Returns:
        A dictionary containing the agent's execution results:
        - "result": The primary output from the agent
        - "usage": Token usage information
        - Additional metadata depending on the agent's configuration

    Raises:
        ValueError: If no actions are provided in the actions list.
        Various exceptions: May raise model-specific, network, or action-related
                          exceptions during agent execution.

    Example:
        ```python
        result = await run_agent(
            user_prompt="Analyze this security alert",
            model_name="gpt-4",
            model_provider="openai",
            actions=["tools.slack.post_message"],
            instructions="You are a security analyst. Be thorough.",
        )
        ```
    """

    if max_tool_calls > TRACECAT__AGENT_MAX_TOOL_CALLS:
        raise ValueError(
            f"Cannot request more than {TRACECAT__AGENT_MAX_TOOL_CALLS} tool calls"
        )

    if max_requests > TRACECAT__AGENT_MAX_REQUESTS:
        raise ValueError(
            f"Cannot request more than {TRACECAT__AGENT_MAX_REQUESTS} requests"
        )

    start_time = default_timer()

    session_id = ctx_session_id.get() or uuid.uuid4()

    role = ctx_role.get()
    if role is None or role.workspace_id is None:
        raise TracecatAuthorizationError("Workspace context required for agent run")

    deps = await PersistableStreamingAgentDeps.new(
        session_id, role.workspace_id, persistent=False
    )
    executor = AioStreamingAgentExecutor(deps=deps, role=role)
    try:
        # Merge legacy mcp_server_url/headers with new mcp_servers format
        if mcp_server_url:
            if mcp_servers is None:
                mcp_servers = []
            legacy_mcp_server = MCPServerConfig(
                url=mcp_server_url,
                headers=mcp_server_headers or {},
            )
            mcp_servers.append(legacy_mcp_server)

        args = RunAgentArgs(
            user_prompt=user_prompt,
            session_id=session_id,
            config=AgentConfig(
                model_name=model_name,
                model_provider=model_provider,
                base_url=base_url,
                instructions=instructions,
                output_type=output_type,
                model_settings=model_settings,
                retries=retries,
                deps_type=type(deps),
                mcp_servers=mcp_servers or None,
                actions=actions,
                namespaces=namespaces,
                tool_approvals=tool_approvals,
            ),
            max_requests=max_requests,
            max_tool_calls=max_tool_calls,
            deferred_tool_results=deferred_tool_results,
        )
        handle = await executor.start(args)
        result = await handle.result()
        if result is None:
            raise RuntimeError("Agent run did not complete successfully.")
        end_time = default_timer()
        return AgentOutput(
            output=try_parse_json(result.output),
            message_history=result.all_messages(),
            duration=end_time - start_time,
            usage=result.usage(),
            session_id=session_id,
        )

    except Exception as e:
        logger.exception("Error in agent run", error=e)
        raise AgentRunError(
            exc_cls=type(e),
            exc_msg=str(e),
            message_history=[],
        ) from e


class ClaudeAgentRuntime(AgentRuntime):
    """Stateless, sandboxed Claude SDK runtime.

    This runtime is designed to run inside an NSJail sandbox without database access.
    All I/O happens via Unix sockets:
    - Control socket: Receives init payload, streams events back to orchestrator
    - MCP socket: Tool execution via trusted MCP server

    The orchestrator (outside the sandbox) handles:
    - Session persistence (SDK session files)
    - Message persistence (chat history)
    - Approval flow coordination
    """

    def __init__(self, socket_writer: SocketStreamWriter):
        self._socket_writer = socket_writer
        self._session_id: uuid.UUID | None = None
        self._allowed_actions: dict[str, MCPToolDefinition] | None = None
        self._tool_approvals: dict[str, bool] | None = None
        self._pending_approval_tool_ids: set[str] = set()
        self._client: ClaudeSDKClient | None = None
        self._was_interrupted: bool = False

    def _get_session_file_path(self, sdk_session_id: str) -> Path:
        """Derive the session file path from SDK session ID.

        The Claude SDK stores sessions at:
        ~/.claude/projects/{encoded-cwd}/{session_id}.jsonl

        Raises:
            AgentSandboxValidationError: If sdk_session_id contains path traversal.
        """
        # Validate session ID to prevent path traversal
        # Only allow alphanumeric, hyphens, and underscores
        if not sdk_session_id or not all(
            c.isalnum() or c in "-_" for c in sdk_session_id
        ):
            raise AgentSandboxValidationError(
                f"Invalid sdk_session_id: must be alphanumeric with hyphens/underscores only, got {sdk_session_id!r}"
            )

        cwd = os.getcwd()
        encoded_cwd = cwd.replace("/", "-")
        claude_dir = Path.home() / ".claude" / "projects" / encoded_cwd
        return claude_dir / f"{sdk_session_id}.jsonl"

    def _write_session_file(
        self,
        sdk_session_id: str,
        sdk_session_data: str,
    ) -> Path:
        """Write session data to local filesystem for SDK resume."""
        session_file_path = self._get_session_file_path(sdk_session_id)
        session_file_path.parent.mkdir(parents=True, exist_ok=True)
        session_file_path.write_text(sdk_session_data)
        logger.info(
            "Wrote session file for resume",
            sdk_session_id=sdk_session_id,
            path=str(session_file_path),
        )
        return session_file_path

    async def _handle_approval_request(
        self,
        tool_name: str,
        tool_input: dict[str, Any],
        tool_use_id: str,
    ) -> None:
        """Handle an approval request by streaming and interrupting.

        Streams APPROVAL_REQUEST event via socket and interrupts the client.
        The orchestrator handles persistence and coordination.
        """
        self._pending_approval_tool_ids.add(tool_use_id)

        approval_event = UnifiedStreamEvent.approval_request_event(
            [
                ToolCallContent(
                    id=tool_use_id,
                    name=tool_name,
                    input=tool_input,
                )
            ]
        )
        await self._socket_writer.send_event(approval_event)

        logger.info(
            "Approval request streamed, interrupting run",
            tool_name=tool_name,
        )

        if self._client is not None:
            self._was_interrupted = True
            await self._client.interrupt()

    async def _pre_tool_use_hook(
        self,
        input_data: HookInput,
        tool_use_id: str | None,
        context: HookContext,
    ) -> SyncHookJSONOutput:
        """PreToolUse hook invoked by Claude SDK before each tool use.

        Auto-approves:
        - Discovery tools (list_tools, get_tool_schema)
        - User MCP server tools
        - execute_tool when action is in allowed_actions and not requiring approval

        Denies (with approval request):
        - execute_tool when tool_approvals[action] is True (requires approval)
        """
        tool_name: str = input_data.get("tool_name", "")
        tool_input: dict[str, Any] = input_data.get("tool_input", {})

        # Auto-approve discovery tools
        if tool_name in (
            "mcp__tracecat-registry__list_tools",
            "mcp__tracecat-registry__get_tool_schema",
        ):
            logger.debug("Auto-approving discovery tool", tool_name=tool_name)
            return {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "allow",
                }
            }

        # Auto-approve user MCP server tools
        if tool_name.startswith("mcp__user-mcp-"):
            logger.debug("Auto-approving user MCP tool", tool_name=tool_name)
            return {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "allow",
                }
            }

        # For execute_tool, check if approval is required
        if tool_name == "mcp__tracecat-registry__execute_tool":
            underlying_action = tool_input.get("tool_name")

            if underlying_action:
                # Check if this action requires approval
                requires_approval = (
                    self._tool_approvals is not None
                    and self._tool_approvals.get(underlying_action) is True
                )

                if requires_approval:
                    # Requires approval - stream request and deny
                    if tool_use_id:
                        await self._handle_approval_request(
                            underlying_action,
                            tool_input.get("args", {}),
                            tool_use_id,
                        )
                    return {
                        "hookSpecificOutput": {
                            "hookEventName": "PreToolUse",
                            "permissionDecision": "deny",
                            "permissionDecisionReason": f"Tool '{underlying_action}' requires approval. Request sent for review.",
                        }
                    }

                # Auto-approve - if orchestrator passed it in allowed_actions, it's allowed
                logger.debug(
                    "Auto-approving execute_tool",
                    underlying_action=underlying_action,
                )
                return {
                    "hookSpecificOutput": {
                        "hookEventName": "PreToolUse",
                        "permissionDecision": "allow",
                    }
                }

        # For any other tool not in our allowed set, reject
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": f"Tool '{tool_name}' is not allowed.",
            }
        }

    async def _stop_hook(
        self,
        input_data: HookInput,
        tool_use_id: str | None,
        context: HookContext,
    ) -> SyncHookJSONOutput:
        """Stop hook invoked by Claude SDK when the agent run completes.

        Sends session update to orchestrator for persistence.
        """
        transcript_path = input_data.get("transcript_path")
        sdk_session_id = input_data.get("session_id")

        logger.debug(
            "Stop hook: sending session update",
            transcript_path=transcript_path,
            sdk_session_id=sdk_session_id,
        )

        if sdk_session_id and transcript_path:
            transcript_file = Path(transcript_path)
            if transcript_file.exists():
                sdk_session_data = transcript_file.read_text()
                await self._socket_writer.send_session_update(
                    sdk_session_id, sdk_session_data
                )

        return {}

    async def run(self, payload: RuntimeInitPayload) -> None:
        """Run an agent with the given initialization payload.

        This is the main entry point for sandboxed execution.
        Called after receiving init payload from orchestrator via socket.
        """
        self._session_id = payload.session_id

        # Use resolved tool definitions from orchestrator
        self._allowed_actions = payload.allowed_actions
        self._tool_approvals = payload.config.tool_approvals

        # Write session file locally if resuming
        resume_session_id: str | None = None
        if payload.sdk_session_id and payload.sdk_session_data:
            self._write_session_file(payload.sdk_session_id, payload.sdk_session_data)
            resume_session_id = payload.sdk_session_id

        # Stream user message event
        user_event = UnifiedStreamEvent.user_message_event(payload.user_prompt)
        await self._socket_writer.send_event(user_event)

        try:
            # Build MCP servers config
            mcp_servers: dict[str, Any] = {}

            # Create proxy MCP server using existing infrastructure
            if self._allowed_actions:
                proxy_config = await create_proxy_mcp_server(
                    allowed_actions=self._allowed_actions,
                    auth_token=payload.jwt_token,
                    trusted_socket_path=payload.mcp_socket_path,
                )
                mcp_servers["tracecat-registry"] = proxy_config

            # Add user-defined MCP servers
            if payload.config.mcp_servers:
                for i, server_config in enumerate(payload.config.mcp_servers):
                    mcp_servers[f"user-mcp-{i}"] = {
                        "type": "http",
                        "url": server_config["url"],
                        "headers": server_config.get("headers", {}),
                    }

            system_prompt = _build_claude_system_prompt(payload.config.instructions)

            options = ClaudeAgentOptions(
                include_partial_messages=True,
                resume=resume_session_id,
                env={
                    "MAX_THINKING_TOKENS": "1024",
                    "ANTHROPIC_AUTH_TOKEN": payload.litellm_auth_token,
                    "ANTHROPIC_BASE_URL": payload.litellm_base_url,
                },
                model="agent",
                system_prompt=system_prompt,
                mcp_servers=mcp_servers,
                hooks={
                    "PreToolUse": [HookMatcher(hooks=[self._pre_tool_use_hook])],
                    "Stop": [HookMatcher(hooks=[self._stop_hook])],
                },
            )

            async with ClaudeSDKClient(options=options) as client:
                self._client = client
                await client.query(payload.user_prompt)

                async for message in client.receive_response():
                    if isinstance(message, StreamEvent):
                        unified = ClaudeSDKAdapter().to_unified_event(message)
                        await self._socket_writer.send_event(unified)
                    elif isinstance(message, UserMessage):
                        # Stream tool results from user messages
                        if isinstance(message.content, list):
                            for block in message.content:
                                if isinstance(block, ToolResultBlock):
                                    # Skip denial results for pending approvals
                                    if (
                                        block.tool_use_id
                                        in self._pending_approval_tool_ids
                                    ):
                                        continue
                                    await self._socket_writer.send_event(
                                        UnifiedStreamEvent(
                                            type=StreamEventType.TOOL_RESULT,
                                            tool_call_id=block.tool_use_id,
                                            tool_output=block.content,
                                            is_error=block.is_error or False,
                                        )
                                    )

        except Exception as e:
            logger.error(
                "Error in ClaudeAgentRuntime",
                error=str(e),
                session_id=payload.session_id,
            )
            await self._socket_writer.send_error(str(e))
            raise
        finally:
            self._client = None
            await self._socket_writer.send_done()
