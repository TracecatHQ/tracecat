"""Claude Agent Runtime for sandboxed execution.

This module provides ClaudeAgentRuntime, a stateless runtime designed to run
inside an NSJail sandbox without database access. All I/O happens via Unix sockets.

Key design principles:
- No database imports (no SQLAlchemy, no DB services)
- No pydantic-ai imports
- Minimal import footprint for fast cold start
"""

from __future__ import annotations

import asyncio
import os
import uuid
from pathlib import Path
from typing import Any

import orjson
from claude_agent_sdk import (
    ClaudeAgentOptions,
    ClaudeSDKClient,
    HookMatcher,
)
from claude_agent_sdk.types import (
    AssistantMessage,
    HookContext,
    HookInput,
    ResultMessage,
    StreamEvent,
    SyncHookJSONOutput,
    SystemMessage,
    ToolResultBlock,
    UserMessage,
)

from tracecat.agent.common.exceptions import AgentSandboxValidationError
from tracecat.agent.common.protocol import RuntimeInitPayload
from tracecat.agent.common.socket_io import SocketStreamWriter
from tracecat.agent.common.stream_types import (
    StreamEventType,
    ToolCallContent,
    UnifiedStreamEvent,
)
from tracecat.agent.common.types import MCPToolDefinition
from tracecat.agent.mcp.proxy_server import create_proxy_mcp_server
from tracecat.agent.mcp.utils import normalize_mcp_tool_name
from tracecat.agent.runtime.claude_code.adapter import ClaudeSDKAdapter
from tracecat.logger import logger

# LiteLLM URL - points to LLM bridge inside sandbox (which proxies to host LiteLLM)
# Must match LLM_BRIDGE_PORT in llm_bridge.py
LITELLM_URL = "http://127.0.0.1:4100"

DISALLOWED_TOOLS = [
    "EnterPlanMode",
    "ExitPlanMode",
    "AskUserQuestion",
    "TodoRead",
    "TodoWrite",
    "Task",
    "Skill",
]

# Tools that require internet access (these bypass sandbox network isolation
# because they're executed server-side by Anthropic, not in the sandbox)
INTERNET_TOOLS = [
    "WebSearch",
    "WebFetch",
]


class ClaudeAgentRuntime:
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
        # Public for testing - these represent runtime configuration
        self.registry_tools: dict[str, MCPToolDefinition] | None = None
        self.tool_approvals: dict[str, bool] | None = None
        self._pending_approval_tool_ids: set[str] = set()
        self.client: ClaudeSDKClient | None = None
        self._was_interrupted: bool = False
        # For incremental JSONL line tracking
        self._sdk_session_id: str | None = None
        self._last_seen_line_index: int = 0
        # Flag to mark continuation prompt as internal (consumed after first use)
        self._is_continuation: bool = False

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

    async def _write_session_file(
        self,
        sdk_session_id: str,
        sdk_session_data: str,
    ) -> Path:
        """Write session data to local filesystem for SDK resume."""
        session_file_path = self._get_session_file_path(sdk_session_id)

        def _write() -> None:
            session_file_path.parent.mkdir(parents=True, exist_ok=True)
            session_file_path.write_text(sdk_session_data)

        await asyncio.to_thread(_write)
        logger.debug("Wrote session file", path=str(session_file_path))
        return session_file_path

    def _is_internal_session_line(self, line_data: dict[str, Any]) -> bool:
        """Determine if a session line is internal (not shown in UI timeline).

        Internal lines include:
        - queue-operation, compaction, summary, system messages
        - Interrupt signals (tool_result with "doesn't want to take this action")
        - Interrupt markers ("[Request interrupted by user")
        - Synthetic messages (model="<synthetic>")
        - Raw tool result/error text injections from approval flow

        Visible lines are:
        - User messages (actual user input)
        - Assistant messages (model responses with text/thinking/tool_use)

        Args:
            line_data: Parsed JSONL line content.

        Returns:
            True if this line should be marked as internal.
        """
        msg_type = line_data.get("type", "")

        # Only user and assistant messages can be visible
        if msg_type not in ("user", "assistant"):
            return True

        message = line_data.get("message", {})

        # Synthetic messages are internal (placeholders during approval flow)
        if message.get("model") == "<synthetic>":
            return True

        # Check message content for internal patterns
        msg_content = message.get("content", [])

        # String content - check for raw tool result/error injection
        if isinstance(msg_content, str):
            if msg_content.startswith("Tool '") and (
                "Result:" in msg_content or "Error:" in msg_content
            ):
                return True

        # List content - check for interrupt patterns
        if isinstance(msg_content, list):
            for part in msg_content:
                if isinstance(part, dict):
                    part_type = part.get("type")
                    # Interrupt tool_result with error
                    if (
                        part_type == "tool_result"
                        and part.get("is_error")
                        and "doesn't want to take this action"
                        in str(part.get("content", ""))
                    ):
                        return True
                    # Interrupt text marker
                    if part_type == "text":
                        text = part.get("text", "")
                        if "[Request interrupted by user" in text:
                            return True

        return False

    async def _emit_new_session_lines(self) -> None:
        """Read and emit new JSONL lines from the SDK session file.

        This reads the session file written by Claude SDK and emits any new lines
        (past _last_seen_line_index) to the orchestrator for persistence.
        The lines contain the full JSONL envelope (uuid, timestamp, parentUuid, etc.)
        needed for proper resume.

        If a line fails to parse (e.g., incomplete write by SDK), we stop processing
        at that point and keep the index there. The incomplete line will be retried
        on the next call once the SDK finishes writing it.

        Race condition handling: If called before _sdk_session_id is set (i.e., before
        the first StreamEvent), this is a no-op. The loopback handler uses UUID-based
        deduplication to handle any duplicates that might occur from out-of-order events.
        """
        if not self._sdk_session_id:
            return

        session_file = self._get_session_file_path(self._sdk_session_id)
        if not session_file.exists():
            return

        try:
            file_content = await asyncio.to_thread(session_file.read_text)
            lines = file_content.splitlines()
            new_lines = lines[self._last_seen_line_index :]

            for line in new_lines:
                if not line.strip():
                    # Empty line - advance index and continue
                    self._last_seen_line_index += 1
                    continue

                # Parse to determine visibility, but send raw line for SDK resume
                try:
                    line_data = orjson.loads(line)
                except orjson.JSONDecodeError:
                    # Stop at the first decode failure - this line may be incomplete
                    # because the SDK is still writing it. We'll retry on the next pass.
                    logger.debug(
                        "Stopping at incomplete session line, will retry",
                        line_index=self._last_seen_line_index,
                    )
                    break

                internal = self._is_internal_session_line(line_data)

                # On continuation, mark the continuation prompt (first user message) as internal
                if self._is_continuation and line_data.get("type") == "user":
                    internal = True
                    self._is_continuation = False

                await self._socket_writer.send_session_line(
                    self._sdk_session_id, line, internal=internal
                )

                # Only advance the index after successfully processing the line
                self._last_seen_line_index += 1

        except Exception as e:
            logger.warning("Failed to read session file", error=str(e))

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
        await self._socket_writer.send_stream_event(approval_event)

        logger.info("Approval request streamed, interrupting", tool_name=tool_name)

        if self.client is not None:
            self._was_interrupted = True
            await self.client.interrupt()

    async def _pre_tool_use_hook(
        self,
        input_data: HookInput,
        tool_use_id: str | None,
        context: HookContext,  # noqa: ARG002
    ) -> SyncHookJSONOutput:
        """PreToolUse hook invoked by Claude SDK before each tool use.

        The proxy MCP server only exposes pre-filtered allowed actions.
        Tools are either:
        - Auto-approved (tool_approvals[action] is False or not set)
        - Require approval (tool_approvals[action] is True) -> trigger approval request

        User MCP servers are auto-approved.
        """

        tool_name: str = input_data.get("tool_name", "")
        tool_input: dict[str, Any] = input_data.get("tool_input", {})

        action_name = normalize_mcp_tool_name(tool_name)
        requires_approval = (
            self.tool_approvals is not None
            and self.tool_approvals.get(action_name) is True
        )

        if requires_approval:
            # Requires approval - stream request and interrupt
            if not tool_use_id:
                raise ValueError("Missing tool use ID")

            await self._handle_approval_request(
                action_name,
                tool_input,
                tool_use_id,
            )
            return {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": f"Tool '{action_name}' requires approval. Request sent for review.",
                }
            }

        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "allow",
            }
        }

    def _build_system_prompt(
        self, instructions: str | None, model: str | None = None
    ) -> str:
        """Build the system prompt for the agent."""
        base = "If asked about your identity, you are a Tracecat automation assistant."
        return f"{base}\n\n{instructions}" if instructions else base

    async def run(self, payload: RuntimeInitPayload) -> None:
        """Run an agent with the given initialization payload.

        This is the main entry point for sandboxed execution.
        Called after receiving init payload from orchestrator via socket.

        On resume after approval, the session history already contains the proper
        tool_result entry (inserted by execute_approved_tools_activity), so we just
        resume the session normally and the agent will continue from there.
        """
        self._session_id = payload.session_id
        await self._socket_writer.send_log(
            "info",
            "Runtime initialized",
        )

        # Use resolved tool definitions from orchestrator
        self.registry_tools = payload.allowed_actions
        self.tool_approvals = payload.config.tool_approvals

        # Write session file locally if resuming or forking
        resume_session_id: str | None = None
        fork_session: bool = False
        if payload.sdk_session_id and payload.sdk_session_data:
            await self._write_session_file(
                payload.sdk_session_id, payload.sdk_session_data
            )
            resume_session_id = payload.sdk_session_id
            # If forking, tell the SDK to create a new session from the parent's history
            fork_session = payload.is_fork

        try:
            # Build MCP servers config and tool whitelist
            # We only allow MCP tools - Claude's default toolset (Bash, Read, etc.) is disabled
            mcp_servers: dict[str, Any] = {}
            if self.registry_tools:
                proxy_config = await create_proxy_mcp_server(
                    allowed_actions=self.registry_tools,
                    auth_token=payload.mcp_auth_token,
                )
                mcp_servers["tracecat-registry"] = proxy_config

            # User MCP tools are now handled via the proxy server
            # They're included in allowed_actions and routed through the trusted server
            # (The sandbox has no network access, so direct HTTP connections don't work)

            stderr_queue: asyncio.Queue[str] = asyncio.Queue()

            def handle_claude_stderr(line: str) -> None:
                """Forward Claude CLI stderr to loopback via queue."""
                stderr_queue.put_nowait(line)

            await self._socket_writer.send_log(
                "debug",
                "MCP servers configured",
                extra={
                    "server_count": len(mcp_servers),
                    "servers": list(mcp_servers.keys()),
                },
            )

            # Build disallowed tools list - add internet tools if internet access is disabled
            disallowed_tools = list(DISALLOWED_TOOLS)
            if not payload.config.enable_internet_access:
                disallowed_tools.extend(INTERNET_TOOLS)

            options = ClaudeAgentOptions(
                include_partial_messages=True,
                resume=resume_session_id,
                fork_session=fork_session,  # If True, creates new session from parent's history
                env={
                    "ANTHROPIC_AUTH_TOKEN": payload.litellm_auth_token,
                    "ANTHROPIC_BASE_URL": LITELLM_URL,
                },
                model=payload.config.model_name,
                system_prompt=self._build_system_prompt(payload.config.instructions),
                mcp_servers=mcp_servers,
                disallowed_tools=disallowed_tools,
                stderr=handle_claude_stderr,
                hooks={
                    "PreToolUse": [HookMatcher(hooks=[self._pre_tool_use_hook])],
                },
            )

            async def drain_stderr() -> None:
                """Background task to drain stderr queue to loopback."""
                while True:
                    line = await stderr_queue.get()
                    await self._socket_writer.send_log("warning", f"[stderr] {line}")

            logger.debug(
                "Creating ClaudeSDKClient",
                mcp_servers=list(mcp_servers.keys()),
            )
            client = ClaudeSDKClient(options=options)
            logger.debug("Client created, entering context")
            async with client:
                self.client = client
                stderr_task = asyncio.create_task(drain_stderr())
                try:
                    # On approval continuation, send hidden continuation prompt
                    # On normal turn (fresh or resume), send the user's prompt
                    if payload.is_approval_continuation:
                        query_prompt = "[INTERNAL] End of Tool Call"
                        self._is_continuation = True
                        logger.debug("Approval continuation with hidden prompt")
                    else:
                        query_prompt = payload.user_prompt
                        logger.debug("Normal turn with user prompt")

                    await self._socket_writer.send_log(
                        "info",
                        "Sending query to Claude SDK",
                        prompt_length=len(query_prompt),
                        is_continuation=payload.is_approval_continuation,
                    )
                    await client.query(query_prompt)

                    await self._socket_writer.send_log(
                        "debug", "Query sent, receiving response"
                    )

                    async for message in client.receive_response():
                        logger.debug(
                            "Received message", message_type=type(message).__name__
                        )
                        if isinstance(message, StreamEvent):
                            # Capture SDK session ID from first StreamEvent
                            if not self._sdk_session_id and message.session_id:
                                self._sdk_session_id = message.session_id
                                # If resuming, set last_seen_line_index to current file length
                                if resume_session_id:
                                    session_file = self._get_session_file_path(
                                        self._sdk_session_id
                                    )
                                    if session_file.exists():
                                        file_content = await asyncio.to_thread(
                                            session_file.read_text
                                        )
                                        self._last_seen_line_index = len(
                                            file_content.splitlines()
                                        )
                                logger.debug(
                                    "Captured SDK session ID",
                                    sdk_session_id=self._sdk_session_id,
                                )

                            # Partial streaming delta - forward to UI
                            unified = ClaudeSDKAdapter().to_unified_event(message)
                            await self._socket_writer.send_stream_event(unified)

                        elif isinstance(message, AssistantMessage):
                            # Emit new JSONL lines for persistence (with full envelope)
                            await self._emit_new_session_lines()

                            # Also stream tool results for UI
                            # (keep send_message for backward compat / UI events)
                            await self._socket_writer.send_message(message)

                        elif isinstance(message, UserMessage):
                            # Emit new JSONL lines for persistence (with full envelope)
                            await self._emit_new_session_lines()

                            # Also send message for UI events
                            await self._socket_writer.send_message(message)

                            # Stream tool results for UI
                            if isinstance(message.content, list):
                                for block in message.content:
                                    if isinstance(block, ToolResultBlock):
                                        # Skip denial results for pending approvals
                                        if (
                                            block.tool_use_id
                                            in self._pending_approval_tool_ids
                                        ):
                                            continue
                                        await self._socket_writer.send_stream_event(
                                            UnifiedStreamEvent(
                                                type=StreamEventType.TOOL_RESULT,
                                                tool_call_id=block.tool_use_id,
                                                tool_output=block.content,
                                                is_error=block.is_error or False,
                                            )
                                        )

                        elif isinstance(message, SystemMessage):
                            # Emit new JSONL lines for persistence (with full envelope)
                            await self._emit_new_session_lines()

                            # Also send message for backward compat
                            await self._socket_writer.send_message(message)

                        elif isinstance(message, ResultMessage):
                            # Final result - emit any remaining lines
                            await self._emit_new_session_lines()
                            await self._socket_writer.send_log(
                                "info",
                                "Agent turn completed",
                                num_turns=message.num_turns,
                                duration_ms=message.duration_ms,
                                usage=message.usage,
                            )
                            # Send result with usage data back to orchestrator
                            await self._socket_writer.send_result(
                                usage=message.usage,
                                num_turns=message.num_turns,
                                duration_ms=message.duration_ms,
                            )
                finally:
                    stderr_task.cancel()
                    try:
                        await stderr_task
                    except asyncio.CancelledError:
                        pass

        except Exception as e:
            await self._socket_writer.send_log(
                "error",
                "Runtime error",
                error_type=type(e).__name__,
                error_message=str(e),
            )
            await self._socket_writer.send_error(str(e))
            raise
        finally:
            self.client = None
            await self._socket_writer.send_done()
