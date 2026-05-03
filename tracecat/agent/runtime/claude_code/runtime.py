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
import tempfile
import uuid
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from time import perf_counter
from typing import Any, Literal, Protocol, cast

import orjson
from claude_agent_sdk import (
    ClaudeAgentOptions,
    ClaudeSDKClient,
    HookMatcher,
    SandboxSettings,
    Transport,
)
from claude_agent_sdk.types import (
    AgentDefinition,
    HookContext,
    HookInput,
    McpHttpServerConfig,
    McpServerConfig,
    McpStdioServerConfig,
    PreToolUseHookSpecificOutput,
    ResultMessage,
    StreamEvent,
    SyncHookJSONOutput,
    SystemMessage,
    ToolResultBlock,
    UserMessage,
)

from tracecat.agent.common.config import (
    TRACECAT__AGENT_MCP_BRIDGE_PORT,
    TRACECAT__DISABLE_NSJAIL,
)
from tracecat.agent.common.exceptions import AgentSandboxValidationError
from tracecat.agent.common.output_format import build_sdk_output_format
from tracecat.agent.common.protocol import RuntimeInitPayload
from tracecat.agent.common.stream_types import (
    StreamEventType,
    ToolCallContent,
    UnifiedStreamEvent,
)
from tracecat.agent.common.types import (
    MCPServerConfig,
    MCPStdioServerConfig,
    MCPToolDefinition,
    sandbox_requires_internet_access,
)
from tracecat.agent.llm_routing import get_litellm_route_model
from tracecat.agent.mcp.metadata import (
    PROXY_TOOL_CALL_ID_KEY,
    PROXY_TOOL_METADATA_KEY,
)
from tracecat.agent.mcp.utils import (
    action_name_to_mcp_tool_name,
    normalize_mcp_tool_name,
)
from tracecat.agent.runtime.claude_code.adapter import ClaudeSDKAdapter
from tracecat.agent.runtime.claude_code.session_lines import (
    APPROVAL_CONTINUATION_PROMPT,
    is_approval_continuation_prompt_line,
    is_meta_session_line,
    is_synthetic_session_line,
)
from tracecat.agent.subagents import AgentsConfig
from tracecat.logger import logger


class RuntimeEventWriter(Protocol):
    """Protocol for runtime event delivery."""

    async def send_stream_event(self, event: UnifiedStreamEvent) -> None:
        """Send a unified stream event."""

    async def send_session_line(
        self, sdk_session_id: str, line: str, *, internal: bool = False
    ) -> None:
        """Send a raw Claude session line."""

    async def send_result(
        self,
        usage: dict[str, Any] | None = None,
        num_turns: int | None = None,
        duration_ms: int | None = None,
        output: Any = None,
    ) -> None:
        """Send the final Claude result."""

    async def send_error(self, error: str) -> None:
        """Send a terminal runtime error."""

    async def send_done(self) -> None:
        """Signal that the runtime turn is complete."""

    async def send_log(self, level: str, message: str, **extra: object) -> None:
        """Send a structured runtime log event."""


def _configure_claude_sdk_process_env() -> None:
    """Prime process-level SDK env before ClaudeSDKClient.connect().

    The SDK checks this flag from ``os.environ`` during connect, before it merges
    ``ClaudeAgentOptions.env`` into the child process environment.
    """
    if "CLAUDE_AGENT_SDK_SKIP_VERSION_CHECK" not in os.environ:
        os.environ["CLAUDE_AGENT_SDK_SKIP_VERSION_CHECK"] = "1"


# Tools that are always disallowed regardless of sandbox mode
# These are interactive/planning tools that don't make sense for automation
CLAUDE_CODE_STATEFUL_TOOLS = [
    # Scheduled prompts and worktree lifecycle are host/session stateful.
    "CronCreate",
    "CronDelete",
    "CronList",
    "EnterWorktree",
    "ExitWorktree",
]

DISALLOWED_TOOLS = [
    *CLAUDE_CODE_STATEFUL_TOOLS,
    # Notebook tools
    "NotebookRead",
    "NotebookEdit",
    # Planning/interaction tools - agent is non-interactive
    "EnterPlanMode",
    "ExitPlanMode",
    "AskUserQuestion",
    "TodoRead",
    "TodoWrite",
    "Agent",
    "Task",
    "TaskOutput",
    "SlashCommand",
]

AGENT_TOOL_NAMES = frozenset({"Agent", "Task"})
CHILD_AGENT_DISALLOWED_TOOLS = [
    "Agent",
    "Task",
    "TaskOutput",
    *CLAUDE_CODE_STATEFUL_TOOLS,
]

# Tools that require internet access (these bypass sandbox network isolation
# because they're executed server-side by Anthropic, not in the sandbox)
INTERNET_TOOLS = [
    "WebSearch",
    "WebFetch",
]

# Registry MCP server naming.
# We canonicalize persisted session history to the hyphen form at the
# resume boundary so runtime configuration only exposes one logical server.
REGISTRY_MCP_SERVER_NAME = "tracecat-registry"
REGISTRY_MCP_TOOL_PREFIX = f"mcp__{REGISTRY_MCP_SERVER_NAME}__"
REGISTRY_MCP_DOT_PREFIX = f"mcp.{REGISTRY_MCP_SERVER_NAME}."
LEGACY_REGISTRY_MCP_TOOL_PREFIX = "mcp__tracecat_registry__"
LEGACY_REGISTRY_MCP_DOT_PREFIX = "mcp.tracecat_registry."
SUBAGENT_REGISTRY_MCP_SERVER_PREFIX = "tracecat-registry-"
TRUSTED_MCP_BRIDGE_URL = f"http://127.0.0.1:{TRACECAT__AGENT_MCP_BRIDGE_PORT}/mcp"

# Increase the SDK's stdout/stderr capture buffer above its default 1 MiB so
# larger tool responses do not truncate during agent execution.
CLAUDE_SDK_MAX_BUFFER_SIZE_BYTES = 5 * 1024 * 1024
CUSTOM_MODEL_PROVIDER_AUTO_COMPACT_WINDOW = "128000"

# Cap on how many times the CLI may re-invoke the model to satisfy a Stop hook
# (e.g. structured-output schema validation). Without a cap the CLI can death-loop
# when the model keeps emitting output that fails validation.
MAX_STOP_HOOK_RETRIES = 3


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

    def __init__(
        self,
        event_writer: RuntimeEventWriter,
        *,
        transport_factory: Callable[[ClaudeAgentOptions], Transport],
        session_home_dir: Path | None = None,
        cwd: Path | None = None,
        cwd_setup_path: Path | None = None,
    ):
        self._event_writer = event_writer
        self._session_id: uuid.UUID | None = None
        # Public for testing - these represent runtime configuration
        self.registry_tools: dict[str, MCPToolDefinition] | None = None
        self.tool_approvals: dict[str, bool] | None = None
        self._scope_tool_approvals: dict[str, dict[str, bool] | None] = {}
        self._scope_internet_access: dict[str, bool] = {}
        self._explicit_subagent_aliases: set[str] = set()
        self._registry_mcp_server_names: set[str] = {REGISTRY_MCP_SERVER_NAME}
        self._root_agents_enabled: bool = False
        self._pending_approval_tool_ids: set[str] = set()
        self.client: ClaudeSDKClient | None = None
        self._was_interrupted: bool = False
        # For incremental JSONL line tracking
        self._sdk_session_id: str | None = None
        self._last_seen_line_index: int = 0
        # One-shot guard for hiding our neutral approval-continuation prompt.
        # Other SDK metadata rows are hidden structurally by `_is_internal_session_line`.
        self._hide_next_approval_continuation_prompt: bool = False
        # Adapter for converting Claude SDK events - must be reused to track state
        self._stream_adapter = ClaudeSDKAdapter()
        # Working directory for session file path resolution
        # Must match the cwd passed to ClaudeAgentOptions for session resume
        self._cwd: Path | None = cwd
        self._cwd_setup_path = cwd_setup_path
        self._session_home_dir = session_home_dir
        self._transport_factory = transport_factory
        # Tracks Stop hook retries within this run to break structured-output loops
        self._stop_hook_retries: int = 0

    @staticmethod
    def _is_manual_compaction_prompt(prompt: str) -> bool:
        """Return True when the current prompt triggers manual compaction."""
        stripped = prompt.strip()
        return stripped == "/compact" or stripped.startswith("/compact ")

    @staticmethod
    def _build_compaction_status_event(
        *,
        phase: Literal["started", "completed"],
        pre_tokens: int | None = None,
    ) -> UnifiedStreamEvent:
        """Create a transient stream event for compaction UI feedback."""
        metadata: dict[str, Any] = {}
        if pre_tokens is not None:
            metadata["pre_tokens"] = pre_tokens
        return UnifiedStreamEvent.compaction_event(
            phase=phase,
            metadata=metadata or None,
        )

    def _should_inject_tool_metadata(self, tool_name: str, action_name: str) -> bool:
        """Return True when a tool executes through the registry proxy path."""
        for server_name in self._registry_mcp_server_names:
            if tool_name.startswith((f"mcp__{server_name}__", f"mcp.{server_name}.")):
                return not action_name.startswith(("mcp.", "internal."))
        return tool_name.startswith(
            (LEGACY_REGISTRY_MCP_TOOL_PREFIX, LEGACY_REGISTRY_MCP_DOT_PREFIX)
        ) and not action_name.startswith(("mcp.", "internal."))

    @staticmethod
    def _subagent_registry_server_name(alias: str) -> str:
        return f"{SUBAGENT_REGISTRY_MCP_SERVER_PREFIX}{alias}"

    @staticmethod
    def _trusted_mcp_server_config(auth_token: str) -> McpHttpServerConfig:
        return {
            "type": "http",
            "url": TRUSTED_MCP_BRIDGE_URL,
            "headers": {"Authorization": f"Bearer {auth_token}"},
        }

    @staticmethod
    def _mcp_tool_name_for_action(server_name: str, action_name: str) -> str:
        if action_name.startswith("mcp__"):
            concrete_name = action_name
        else:
            concrete_name = action_name_to_mcp_tool_name(action_name)
        return f"mcp__{server_name}__{concrete_name}"

    @staticmethod
    def _mcp_tool_wildcard_for_server(server_name: str) -> str:
        return f"mcp__{server_name}__*"

    @classmethod
    def _mcp_tool_names_for_actions(
        cls,
        server_name: str,
        actions: dict[str, MCPToolDefinition] | None,
    ) -> list[str]:
        if not actions:
            return []
        return sorted(
            cls._mcp_tool_name_for_action(server_name, action_name)
            for action_name in actions
        )

    @classmethod
    def _allowed_tools_for_mcp_scope(
        cls,
        *,
        registry_server_name: str,
        actions: dict[str, MCPToolDefinition] | None,
        stdio_server_names: list[str],
    ) -> list[str]:
        return sorted(
            {
                *cls._mcp_tool_names_for_actions(registry_server_name, actions),
                *(
                    cls._mcp_tool_wildcard_for_server(server_name)
                    for server_name in stdio_server_names
                ),
            }
        )

    @staticmethod
    def _stdio_mcp_server_config(
        config: MCPStdioServerConfig,
    ) -> McpStdioServerConfig:
        server_config: McpStdioServerConfig = {
            "type": "stdio",
            "command": config["command"],
        }
        if args := config.get("args"):
            server_config["args"] = args
        if env := config.get("env"):
            server_config["env"] = env
        return server_config

    @classmethod
    def _stdio_mcp_servers(
        cls,
        *,
        source_configs: list[MCPServerConfig] | None,
        name_prefix: str | None = None,
        existing_names: set[str] | None = None,
    ) -> dict[str, McpStdioServerConfig]:
        servers: dict[str, McpStdioServerConfig] = {}
        used_names = set(existing_names or ())
        if not source_configs:
            return servers

        for config in source_configs:
            if config.get("type", "http") != "stdio":
                continue
            stdio_config = cast(MCPStdioServerConfig, config)
            base_name = stdio_config["name"]
            if name_prefix:
                base_name = f"{name_prefix}-{base_name}"

            server_name = base_name
            suffix = 2
            while server_name in used_names:
                server_name = f"{base_name}-{suffix}"
                suffix += 1

            used_names.add(server_name)
            servers[server_name] = cls._stdio_mcp_server_config(stdio_config)

        return servers

    @staticmethod
    def _is_subagent_scope(input_data: HookInput) -> bool:
        return bool(input_data.get("agent_id"))

    def _hook_scope(self, input_data: HookInput) -> str:
        if self._is_subagent_scope(input_data):
            agent_type = input_data.get("agent_type")
            if isinstance(agent_type, str) and agent_type:
                return agent_type
        return "root"

    def _policy_scope(self, input_data: HookInput) -> str:
        scope = self._hook_scope(input_data)
        if scope in self._explicit_subagent_aliases:
            return scope
        # Dynamic/general-purpose subagents inherit root tools and approvals.
        return "root"

    def _approval_metadata(self, input_data: HookInput) -> dict[str, Any]:
        scope = self._hook_scope(input_data)
        return {
            "agent_scope": scope,
            "agent_alias": scope if scope in self._explicit_subagent_aliases else None,
            "agent_id": input_data.get("agent_id"),
        }

    @staticmethod
    def _agent_tool_target(tool_input: dict[str, Any]) -> str | None:
        for key in ("subagent_type", "agent_type", "type", "name"):
            value = tool_input.get(key)
            if isinstance(value, str) and value:
                return value
        return None

    def _validate_agent_tool_call(
        self,
        input_data: HookInput,
        tool_name: str,
        tool_input: dict[str, Any],
    ) -> str | None:
        """Return denial reason for invalid Agent/Task tool use, else None."""
        if tool_name not in AGENT_TOOL_NAMES:
            return None
        if not self._agents_enabled:
            return "Subagents are disabled for this agent configuration."
        if self._is_subagent_scope(input_data):
            return "Subagents cannot invoke other subagents."

        target = self._agent_tool_target(tool_input)
        if (
            target
            and target != "general-purpose"
            and target not in self._explicit_subagent_aliases
        ):
            allowed = ["general-purpose", *sorted(self._explicit_subagent_aliases)]
            return (
                f"Subagent '{target}' is not available. "
                f"Allowed subagents: {', '.join(allowed)}."
            )
        return None

    @property
    def _agents_enabled(self) -> bool:
        return bool(getattr(self, "_root_agents_enabled", False))

    def _with_tool_call_metadata(
        self,
        tool_input: dict[str, Any],
        tool_use_id: str,
    ) -> dict[str, Any]:
        """Attach Tracecat-internal tool metadata to proxy tool input."""
        return {
            **tool_input,
            PROXY_TOOL_METADATA_KEY: {
                PROXY_TOOL_CALL_ID_KEY: tool_use_id,
            },
        }

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

        if self._cwd is None:
            raise RuntimeError("Runtime working directory is not configured")
        encoded_cwd = str(self._cwd).replace("/", "-")
        claude_home_dir = self._session_home_dir or Path.home()
        claude_dir = claude_home_dir / ".claude" / "projects" / encoded_cwd
        return claude_dir / f"{sdk_session_id}.jsonl"

    async def _write_session_file(
        self,
        sdk_session_id: str,
        sdk_session_data: str,
    ) -> Path:
        """Write session data to local filesystem for SDK resume."""
        session_file_path = self._get_session_file_path(sdk_session_id)
        sdk_session_data = self._canonicalize_sdk_session_data(sdk_session_data)

        # Ensure the file ends with a newline so JSONL readers don't treat the last
        # record as truncated (some implementations are strict about this).
        if sdk_session_data and not sdk_session_data.endswith("\n"):
            sdk_session_data = f"{sdk_session_data}\n"

        def _write() -> None:
            session_file_path.parent.mkdir(parents=True, exist_ok=True)
            session_file_path.write_text(sdk_session_data)

        await asyncio.to_thread(_write)
        logger.debug("Wrote session file", path=str(session_file_path))
        return session_file_path

    async def _prepare_resume_and_mcp(
        self,
        payload: RuntimeInitPayload,
        *,
        write_session_file: bool = True,
    ) -> tuple[str | None, bool, dict[str, McpServerConfig]]:
        """Prepare resume state and MCP server config in parallel."""
        resume_session_id: str | None = None
        fork_session = False
        mcp_servers: dict[str, McpServerConfig] = {}

        session_file_task: asyncio.Task[Path] | None = None

        if payload.sdk_session_id and payload.sdk_session_data:
            resume_session_id = payload.sdk_session_id
            fork_session = payload.is_fork
            if not fork_session:
                self._sdk_session_id = resume_session_id
                self._last_seen_line_index = len(payload.sdk_session_data.splitlines())

        async with asyncio.TaskGroup() as tg:
            if (
                write_session_file
                and payload.sdk_session_id
                and payload.sdk_session_data
            ):
                session_file_task = tg.create_task(
                    self._write_session_file(
                        payload.sdk_session_id, payload.sdk_session_data
                    )
                )

        if session_file_task is not None:
            _ = session_file_task.result()

        if self.registry_tools:
            mcp_servers[REGISTRY_MCP_SERVER_NAME] = self._trusted_mcp_server_config(
                payload.mcp_auth_token
            )

        return resume_session_id, fork_session, mcp_servers

    def _canonicalize_sdk_session_data(self, sdk_session_data: str) -> str:
        """Canonicalize legacy registry MCP aliases in JSONL session history."""
        return sdk_session_data.replace(
            LEGACY_REGISTRY_MCP_TOOL_PREFIX, REGISTRY_MCP_TOOL_PREFIX
        ).replace(LEGACY_REGISTRY_MCP_DOT_PREFIX, REGISTRY_MCP_DOT_PREFIX)

    @staticmethod
    def _is_internal_compaction_prompt(text: str) -> bool:
        """Return True for structured Claude SDK compaction artifacts."""
        compact_markers = (
            "<local-command-caveat>",
            "<command-name>/compact</command-name>",
            "<local-command-stdout>Compacted ",
        )
        return any(marker in text for marker in compact_markers)

    @staticmethod
    async def _meta_text_input_stream(text: str) -> AsyncIterator[dict[str, Any]]:
        """Yield a hidden Claude SDK stream-json user message."""
        yield {
            "type": "user",
            "message": {"role": "user", "content": text},
            "parent_tool_use_id": None,
            "session_id": "default",
            "isMeta": True,
        }

    def _is_internal_session_line(self, line_data: dict[str, Any]) -> bool:
        """Determine if a session line is internal (not shown in UI timeline).

        Internal lines include:
        - queue-operation, compaction, summary, system messages
        - compaction continuation prompts/summarizer sidechain messages
        - Interrupt signals (tool_result with "doesn't want to take this action")
        - Interrupt markers ("[Request interrupted by user")
        - Synthetic messages (model="<synthetic>")
        - Raw tool result/error text injections from approval flow
        - SDK compaction summary and metadata messages (isCompactSummary, isMeta)

        Visible lines are:
        - User messages (actual user input)
        - Assistant messages (model responses with text/thinking/tool_use)

        Args:
            line_data: Parsed JSONL line content.

        Returns:
            True if this line should be marked as internal.
        """
        # SDK compaction artifacts marked with structural flags
        # isCompactSummary messages are persisted as kind='compaction' for badge rendering
        # isMeta messages (like caveats) are internal
        if is_meta_session_line(line_data) or line_data.get("isCompactSummary"):
            return True

        msg_type = line_data.get("type", "")

        # Only user and assistant messages can be visible
        if msg_type not in ("user", "assistant"):
            return True

        agent_id = line_data.get("agentId")
        if isinstance(agent_id, str) and agent_id.startswith("acompact-"):
            return True

        message = line_data.get("message", {})

        # Synthetic messages are internal (placeholders during approval flow)
        if is_synthetic_session_line(line_data):
            return True

        # "(no content)" placeholder assistant message from the SDK's
        # structured output stop hook flow.
        if msg_type == "assistant":
            msg_content_check = message.get("content", [])
            if isinstance(msg_content_check, list) and len(msg_content_check) == 1:
                only_part = msg_content_check[0]
                if (
                    isinstance(only_part, dict)
                    and only_part.get("type") == "text"
                    and only_part.get("text") == "(no content)"
                ):
                    return True

        # Check message content for internal patterns
        msg_content = message.get("content", [])

        # String content - check for raw tool result/error injection
        if isinstance(msg_content, str):
            if msg_content.startswith("Tool '") and (
                "Result:" in msg_content or "Error:" in msg_content
            ):
                return True
            if self._is_internal_compaction_prompt(msg_content):
                return True
            # Stop hook feedback injected by Claude SDK for structured output
            if "Stop hook feedback:" in msg_content:
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
                    # Interrupt text marker or stop hook feedback
                    if part_type == "text":
                        text = part.get("text", "")
                        if "[Request interrupted by user" in text:
                            return True
                        if self._is_internal_compaction_prompt(text):
                            return True
                        if "Stop hook feedback:" in text:
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

                if (
                    self._hide_next_approval_continuation_prompt
                    and is_approval_continuation_prompt_line(line_data)
                ):
                    internal = True
                    self._hide_next_approval_continuation_prompt = False

                await self._event_writer.send_session_line(
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
        *,
        metadata: dict[str, Any] | None = None,
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
                    metadata=metadata,
                )
            ]
        )
        await self._event_writer.send_stream_event(approval_event)

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
        if denial_reason := self._validate_agent_tool_call(
            input_data,
            tool_name,
            tool_input,
        ):
            return {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": denial_reason,
                }
            }

        policy_scope = self._policy_scope(input_data)
        if tool_name in INTERNET_TOOLS and not self._scope_internet_access.get(
            policy_scope, False
        ):
            return {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": (
                        f"Tool '{tool_name}' is disabled for agent scope "
                        f"'{policy_scope}'."
                    ),
                }
            }

        scope_approvals = self._scope_tool_approvals.get(
            policy_scope,
            self.tool_approvals,
        )
        requires_approval = (
            scope_approvals is not None and scope_approvals.get(action_name) is True
        )

        if requires_approval:
            # Requires approval - stream request and interrupt
            if not tool_use_id:
                raise ValueError("Missing tool use ID")

            await self._handle_approval_request(
                action_name,
                tool_input,
                tool_use_id,
                metadata=self._approval_metadata(input_data),
            )
            return {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": f"Tool '{action_name}' requires approval. Request sent for review.",
                }
            }

        hook_output: PreToolUseHookSpecificOutput = {
            "hookEventName": "PreToolUse",
            "permissionDecision": "allow",
        }
        if self._should_inject_tool_metadata(tool_name, action_name):
            if tool_use_id:
                hook_output["updatedInput"] = self._with_tool_call_metadata(
                    tool_input,
                    tool_use_id,
                )
            else:
                logger.warning(
                    "Missing tool use ID for registry tool execution",
                    action_name=action_name,
                )

        return {
            "hookSpecificOutput": hook_output,
        }

    async def _stop_hook(
        self,
        input_data: HookInput,
        tool_use_id: str | None,
        context: HookContext,
    ) -> SyncHookJSONOutput:
        """Stop hook: cap structured-output retry loops.

        The CLI runs this on every natural stop. When ``stop_hook_active`` is True
        the CLI is already inside a retry loop (e.g. structured-output schema
        validation failed and it wants the model to try again). We let the first
        ``MAX_STOP_HOOK_RETRIES`` retries through, then terminate the turn so a
        broken schema or stuck model can't death-loop.
        """
        if input_data["hook_event_name"] != "Stop":
            raise ValueError(
                f"Expected Stop hook event, got {input_data['hook_event_name']!r}"
            )
        if not input_data.get("stop_hook_active"):
            return {}

        self._stop_hook_retries += 1
        if self._stop_hook_retries <= MAX_STOP_HOOK_RETRIES:
            return {}

        reason = (
            f"Stop hook retry cap reached ({MAX_STOP_HOOK_RETRIES}); ending turn "
            "to prevent a structured-output death loop."
        )
        await self._event_writer.send_log(
            "warning",
            "Stop hook retry cap reached, terminating turn",
            retries=self._stop_hook_retries,
            cap=MAX_STOP_HOOK_RETRIES,
        )
        return {"continue_": False, "stopReason": reason}

    def _build_system_prompt(
        self, instructions: str | None, output_type: str | dict[str, Any] | None = None
    ) -> str:
        """Build the system prompt for the agent."""
        base = "If asked about your identity, you are a Tracecat automation assistant."

        # Only include structured output instruction if output_type is configured (not None)
        if output_type is not None:
            base += (
                "\n\nYou MUST produce structured output as the very last thing in EVERY turn"
                " — including follow-up turns. Do not add any commentary, explanation, or text"
                " after the structured output. This applies to every response, not just the first one."
            )

        return f"{base}\n\n{instructions}" if instructions else base

    @staticmethod
    def _agents_config_enabled(config: AgentsConfig) -> bool:
        return config.enabled

    def _build_agent_definitions(
        self,
        *,
        payload: RuntimeInitPayload,
    ) -> dict[str, AgentDefinition] | None:
        if not payload.subagents:
            return None

        definitions: dict[str, AgentDefinition] = {}
        for subagent in payload.subagents:
            registry_server_name = self._subagent_registry_server_name(subagent.alias)
            mcp_server_configs: list[str | dict[str, Any]] = []
            if subagent.allowed_actions:
                mcp_server_configs.append(
                    {
                        registry_server_name: self._trusted_mcp_server_config(
                            subagent.mcp_auth_token
                        )
                    }
                )
            stdio_mcp_servers = self._stdio_mcp_servers(
                source_configs=subagent.config.mcp_servers,
                name_prefix=f"subagent-{subagent.alias}",
                existing_names={registry_server_name},
            )
            mcp_server_configs.extend(
                {server_name: server_config}
                for server_name, server_config in stdio_mcp_servers.items()
            )

            disallowed_tools = list(CHILD_AGENT_DISALLOWED_TOOLS)
            if not subagent.config.enable_internet_access:
                disallowed_tools.extend(INTERNET_TOOLS)

            definitions[subagent.alias] = AgentDefinition(
                description=subagent.description,
                prompt=subagent.prompt,
                model=get_litellm_route_model(
                    model_provider=subagent.config.model_provider,
                    model_name=subagent.config.model_name,
                    passthrough=subagent.config.passthrough,
                ),
                tools=self._allowed_tools_for_mcp_scope(
                    registry_server_name=registry_server_name,
                    actions=subagent.allowed_actions,
                    stdio_server_names=list(stdio_mcp_servers),
                )
                or None,
                mcpServers=mcp_server_configs or None,
                disallowedTools=disallowed_tools,
                maxTurns=subagent.max_turns,
            )

        return definitions

    async def run(self, payload: RuntimeInitPayload) -> None:
        """Run an agent with the given initialization payload.

        This is the main entry point for sandboxed execution.
        Called after receiving init payload from orchestrator via socket.

        On resume after approval, session history already contains the
        approved or denied tool_result. The runtime sends a hidden meta tick so
        Claude Code consumes the completed history.
        """
        run_started_at = perf_counter()

        def log_benchmark_phase(phase: str, **extra: object) -> None:
            logger.info(
                "Agent benchmark phase",
                phase=phase,
                elapsed_ms=round((perf_counter() - run_started_at) * 1000, 2),
                session_id=payload.session_id,
                component="runtime",
                **extra,
            )

        self._session_id = payload.session_id
        log_benchmark_phase("runtime_start")
        await self._event_writer.send_log(
            "info",
            "Runtime initialized",
        )

        # Use resolved tool definitions from orchestrator
        self.registry_tools = payload.allowed_actions
        self.tool_approvals = payload.config.tool_approvals
        self._root_agents_enabled = self._agents_config_enabled(payload.config.agents)
        self._explicit_subagent_aliases = {
            subagent.alias for subagent in payload.subagents
        }
        self._registry_mcp_server_names = {
            REGISTRY_MCP_SERVER_NAME,
            *(
                self._subagent_registry_server_name(subagent.alias)
                for subagent in payload.subagents
                if subagent.allowed_actions
            ),
        }
        self._scope_tool_approvals = {
            "root": payload.config.tool_approvals,
            **{
                subagent.alias: subagent.config.tool_approvals
                for subagent in payload.subagents
            },
        }
        self._scope_internet_access = {
            "root": payload.config.enable_internet_access,
            **{
                subagent.alias: subagent.config.enable_internet_access
                for subagent in payload.subagents
            },
        }
        runtime_internet_access = sandbox_requires_internet_access(
            payload.config,
            payload.subagents,
        )

        # Stable per-session working directory for the Claude Code CLI.
        # IMPORTANT: Must be deterministic per session_id. The CLI indexes
        # sessions by project directory (cwd), so if cwd changes between
        # turns (e.g., random mkdtemp), --resume can't find the session.
        # Both nsjail and direct mode use the same scheme for parity.
        if self._cwd is None:
            self._cwd = (
                Path(tempfile.gettempdir()) / f"tracecat-agent-{payload.session_id}"
            )
        cwd_setup_path = self._cwd_setup_path or self._cwd
        cwd_setup_path.mkdir(parents=True, exist_ok=True)

        # Write session file locally if resuming or forking
        try:
            (
                resume_session_id,
                fork_session,
                mcp_servers,
            ) = await self._prepare_resume_and_mcp(
                payload,
                write_session_file=True,
            )
            log_benchmark_phase(
                "runtime_resume_ready",
                resumed=resume_session_id is not None,
                fork_session=fork_session,
            )

            stderr_queue: asyncio.Queue[str] = asyncio.Queue()
            stdio_mcp_servers = self._stdio_mcp_servers(
                source_configs=payload.config.mcp_servers,
                existing_names=set(mcp_servers),
            )
            mcp_servers.update(stdio_mcp_servers)
            agent_definitions = self._build_agent_definitions(payload=payload)

            def handle_claude_stderr(line: str) -> None:
                """Forward Claude CLI stderr to loopback via queue."""
                stderr_queue.put_nowait(line)

            await self._event_writer.send_log(
                "debug",
                "MCP servers configured",
                extra={
                    "server_count": len(mcp_servers),
                    "servers": list(mcp_servers.keys()),
                },
            )

            # Build disallowed tools list based on environment and config
            # - Always blocked: interactive/planning tools (DISALLOWED_TOOLS)
            # - Internet tools: blocked only when no scope can use them. The
            #   hook below enforces root/subagent-specific access.
            # Filesystem tools (Bash, Read, Write, etc.) are always allowed:
            # - nsjail mode: sandbox provides OS-level isolation
            # - direct mode: SandboxSettings + stable cwd scopes file access

            disallowed_tools: list[str] = [
                tool
                for tool in DISALLOWED_TOOLS
                if not (self._agents_enabled and tool in AGENT_TOOL_NAMES)
            ]
            if not runtime_internet_access:
                disallowed_tools.extend(INTERNET_TOOLS)
            allowed_tools = self._allowed_tools_for_mcp_scope(
                registry_server_name=REGISTRY_MCP_SERVER_NAME,
                actions=payload.allowed_actions,
                stdio_server_names=list(stdio_mcp_servers),
            )
            if self._agents_enabled:
                allowed_tools.extend(sorted(AGENT_TOOL_NAMES))

            sandbox_settings = SandboxSettings(enabled=TRACECAT__DISABLE_NSJAIL)
            if TRACECAT__DISABLE_NSJAIL:
                sandbox_settings["enableWeakerNestedSandbox"] = True
                sandbox_settings["allowUnsandboxedCommands"] = False
            # Build output_format from output_type if provided
            sdk_output_format = build_sdk_output_format(payload.config.output_type)
            options = ClaudeAgentOptions(
                include_partial_messages=True,
                resume=resume_session_id,
                fork_session=fork_session,  # If True, creates new session from parent's history
                thinking=(
                    {"type": "enabled", "budget_tokens": 1024}
                    if payload.config.enable_thinking
                    else {"type": "disabled"}
                ),
                setting_sources=["user"],
                env={
                    "ANTHROPIC_AUTH_TOKEN": payload.llm_gateway_auth_token,
                    **(
                        {
                            "CLAUDE_CODE_AUTO_COMPACT_WINDOW": CUSTOM_MODEL_PROVIDER_AUTO_COMPACT_WINDOW
                        }
                        if payload.config.model_provider == "custom-model-provider"
                        else {}
                    ),
                    **(
                        {"CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS": "1"}
                        if payload.config.passthrough
                        else {}
                    ),
                },
                model=get_litellm_route_model(
                    model_provider=payload.config.model_provider,
                    model_name=payload.config.model_name,
                    passthrough=payload.config.passthrough,
                ),
                system_prompt=self._build_system_prompt(
                    payload.config.instructions, payload.config.output_type
                ),
                mcp_servers=mcp_servers,
                allowed_tools=allowed_tools,
                disallowed_tools=disallowed_tools,
                agents=agent_definitions,
                stderr=handle_claude_stderr,
                sandbox=sandbox_settings,
                hooks={
                    "PreToolUse": [HookMatcher(hooks=[self._pre_tool_use_hook])],
                    "Stop": [HookMatcher(hooks=[self._stop_hook])],
                },
                # Stable per-session working directory (deterministic across turns)
                cwd=self._cwd,
                max_buffer_size=CLAUDE_SDK_MAX_BUFFER_SIZE_BYTES,
                output_format=sdk_output_format,
            )

            async def drain_stderr() -> None:
                """Background task to drain stderr queue to loopback."""
                while True:
                    line = await stderr_queue.get()
                    await self._event_writer.send_log("warning", f"[stderr] {line}")

            logger.debug(
                "Creating ClaudeSDKClient",
                mcp_servers=list(mcp_servers.keys()),
            )
            _configure_claude_sdk_process_env()
            if payload.is_approval_continuation:
                query_input = self._meta_text_input_stream(APPROVAL_CONTINUATION_PROMPT)
                self._hide_next_approval_continuation_prompt = True
                query_log_extra = {
                    "prompt_length": len(APPROVAL_CONTINUATION_PROMPT),
                    "is_meta": True,
                }
                logger.debug("Approval continuation with meta prompt")
            else:
                query_input = payload.user_prompt
                query_log_extra = {"prompt_length": len(query_input)}
                logger.debug("Normal turn with user prompt")

            transport = self._transport_factory(options)
            client = ClaudeSDKClient(options=options, transport=transport)
            logger.debug("Client created, entering context")
            async with client:
                self.client = client
                log_benchmark_phase("runtime_client_connected")
                stderr_task = asyncio.create_task(drain_stderr())
                try:
                    await self._event_writer.send_log(
                        "info",
                        "Sending query to Claude SDK",
                        is_continuation=payload.is_approval_continuation,
                        **query_log_extra,
                    )
                    if isinstance(
                        query_input, str
                    ) and self._is_manual_compaction_prompt(query_input):
                        await self._event_writer.send_stream_event(
                            self._build_compaction_status_event(phase="started")
                        )
                    await client.query(query_input)
                    log_benchmark_phase("runtime_query_sent")

                    await self._event_writer.send_log(
                        "debug", "Query sent, receiving response"
                    )

                    first_stream_event_logged = False
                    async for message in client.receive_response():
                        logger.debug(
                            "Received message", message_type=type(message).__name__
                        )
                        if isinstance(message, StreamEvent):
                            if not first_stream_event_logged:
                                first_stream_event_logged = True
                                log_benchmark_phase("runtime_first_stream_event")
                            # Capture SDK session ID from first StreamEvent
                            if (
                                message.session_id
                                and self._sdk_session_id != message.session_id
                            ):
                                previous = self._sdk_session_id
                                self._sdk_session_id = message.session_id
                                # If the CLI started a different session (e.g. fork), avoid
                                # re-persisting old history by jumping to current file length.
                                # For normal resumes we pre-seed `_last_seen_line_index` above.
                                if (
                                    previous is None
                                    and resume_session_id
                                    and fork_session
                                ):
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
                                    previous_sdk_session_id=previous,
                                )

                            # Partial streaming delta - forward to UI
                            unified = self._stream_adapter.to_unified_event(message)
                            await self._event_writer.send_stream_event(unified)

                        elif isinstance(message, ResultMessage):
                            # Final result - emit any remaining lines
                            await self._emit_new_session_lines()
                            await self._event_writer.send_log(
                                "info",
                                "Agent turn completed",
                                num_turns=message.num_turns,
                                duration_ms=message.duration_ms,
                                usage=message.usage,
                            )
                            log_benchmark_phase(
                                "runtime_result_received",
                                duration_ms=message.duration_ms,
                                num_turns=message.num_turns,
                            )
                            result_output = (
                                message.structured_output
                                if message.structured_output is not None
                                else message.result
                            )
                            await self._event_writer.send_result(
                                usage=message.usage,
                                num_turns=message.num_turns,
                                duration_ms=message.duration_ms,
                                output=result_output,
                            )
                            self._hide_next_approval_continuation_prompt = False

                        elif isinstance(message, SystemMessage):
                            await self._emit_new_session_lines()
                            if message.subtype != "compact_boundary":
                                continue

                            pre_tokens: int | None = None
                            compact_metadata = message.data.get("compact_metadata")
                            if isinstance(compact_metadata, dict):
                                pre_tokens_value = compact_metadata.get("pre_tokens")
                                if isinstance(pre_tokens_value, int):
                                    pre_tokens = pre_tokens_value

                            await self._event_writer.send_stream_event(
                                self._build_compaction_status_event(
                                    phase="completed",
                                    pre_tokens=pre_tokens,
                                )
                            )

                        else:
                            # AssistantMessage, UserMessage, etc.
                            await self._emit_new_session_lines()

                            # Stream tool results for UI
                            if isinstance(message, UserMessage) and isinstance(
                                message.content, list
                            ):
                                for block in message.content:
                                    if isinstance(block, ToolResultBlock):
                                        if (
                                            block.tool_use_id
                                            in self._pending_approval_tool_ids
                                        ):
                                            continue
                                        await self._event_writer.send_stream_event(
                                            UnifiedStreamEvent(
                                                type=StreamEventType.TOOL_RESULT,
                                                tool_call_id=block.tool_use_id,
                                                tool_output=block.content,
                                                is_error=block.is_error or False,
                                            )
                                        )
                finally:
                    stderr_task.cancel()
                    try:
                        await stderr_task
                    except asyncio.CancelledError:
                        pass

            # CLI has exited — session file is fully flushed.
            await self._emit_new_session_lines()
            log_benchmark_phase("runtime_complete")

        except Exception as e:
            await self._event_writer.send_log(
                "error",
                "Runtime error",
                error_type=type(e).__name__,
                error_message=str(e),
            )
            await self._event_writer.send_error(str(e))
            raise
        finally:
            self.client = None
            await self._event_writer.send_done()
