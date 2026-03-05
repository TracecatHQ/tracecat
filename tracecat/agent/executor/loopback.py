"""Loopback handler for NSJail runtime communication.

This module provides the socket event loop that:
1. Sends RuntimeInitPayload to the runtime
2. Reads events from the runtime
3. Forwards events to a pluggable stream sink (Redis or external channel)
4. Handles session updates
5. Persists messages to database (AgentSessionHistory + ChatMessage for chat namespace)

The loopback is used by the agent executor activity which handles:
- Job directory creation
- NSJail process spawning
- Cleanup
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

import orjson
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from tracecat.agent.channels.schemas import ChannelType
from tracecat.agent.channels.service import PENDING_SLACK_BOT_TOKEN, AgentChannelService
from tracecat.agent.channels.sinks import ExternalChannelSink
from tracecat.agent.channels.sinks.slack import SlackStreamSink
from tracecat.agent.common.protocol import RuntimeEventEnvelope, RuntimeInitPayload
from tracecat.agent.common.socket_io import MessageType, build_message, read_message
from tracecat.agent.common.stream_types import (
    StreamEventType,
    ToolCallContent,
    UnifiedStreamEvent,
)
from tracecat.agent.common.types import (
    MCPToolDefinition,
    SandboxAgentConfig,
)
from tracecat.agent.session.types import AgentSessionEntity
from tracecat.agent.stream.connector import AgentStream
from tracecat.agent.types import AgentConfig
from tracecat.db.engine import get_async_session_context_manager
from tracecat.db.models import AgentChannelToken, AgentSession, AgentSessionHistory
from tracecat.exceptions import TracecatValidationError
from tracecat.logger import logger


@dataclass(kw_only=True, slots=True)
class LoopbackInput:
    """Input for the loopback handler.

    Fields used by loopback for its own logic:
    - session_id, workspace_id: For stream sink routing and DB writes
    - socket_dir: For control socket path

    Fields passed through to RuntimeInitPayload:
    - user_prompt, config, mcp_auth_token, litellm_*, allowed_actions, sdk_session_*

    On resume after approval, the sdk_session_data contains the proper tool_result
    entry (inserted by execute_approved_tools_activity before reload).
    """

    session_id: uuid.UUID
    workspace_id: uuid.UUID
    user_prompt: str
    config: AgentConfig
    mcp_auth_token: str
    litellm_auth_token: str
    socket_dir: Path
    allowed_actions: dict[str, MCPToolDefinition] | None = None
    sdk_session_id: str | None = None
    sdk_session_data: str | None = None
    is_approval_continuation: bool = False
    is_fork: bool = False  # True when forking from parent session


@dataclass(kw_only=True, slots=True)
class LoopbackResult:
    """Result from the loopback handler."""

    success: bool
    error: str | None = None
    approval_requested: bool = False
    approval_items: list[ToolCallContent] = field(default_factory=list)
    output: Any = None
    result_usage: dict[str, Any] | None = None
    result_num_turns: int | None = None


@dataclass(frozen=True, kw_only=True, slots=True)
class ResolvedSlackContext:
    """Parsed and validated Slack channel context for sink construction."""

    channel_id: str
    thread_ts: str
    recipient_user_id: str | None = None
    recipient_team_id: str | None = None
    reaction_ts: str | None = None

    @classmethod
    def from_channel_context(cls, ctx: dict[str, Any]) -> ResolvedSlackContext | None:
        """Parse a channel_context dict into a validated context, or None on failure."""
        channel_id = ctx.get("channel_id")
        thread_ts = ctx.get("thread_ts")
        if not isinstance(channel_id, str) or not isinstance(thread_ts, str):
            return None

        recipient_user_id = ctx.get("user_id")
        recipient_team_id = ctx.get("team_id")
        reaction_ts = ctx.get("message_ts")
        if not isinstance(reaction_ts, str):
            reaction_ts = ctx.get("event_ts")

        return cls(
            channel_id=channel_id,
            thread_ts=thread_ts,
            recipient_user_id=recipient_user_id
            if isinstance(recipient_user_id, str)
            else None,
            recipient_team_id=recipient_team_id
            if isinstance(recipient_team_id, str)
            else None,
            reaction_ts=reaction_ts if isinstance(reaction_ts, str) else None,
        )


class LoopbackEventSink(Protocol):
    """Sink interface used by loopback for runtime event streaming."""

    async def append(self, event: UnifiedStreamEvent) -> None:
        """Append a runtime stream event."""

    async def error(self, error: str) -> None:
        """Emit a terminal error."""

    async def done(self) -> None:
        """Emit a terminal completion signal."""


@dataclass(kw_only=True, slots=True)
class AgentStreamSink:
    """Redis-backed stream sink used by UI sessions."""

    stream: AgentStream

    async def append(self, event: UnifiedStreamEvent) -> None:
        await self.stream.append(event)

    async def error(self, error: str) -> None:
        await self.stream.error(error)

    async def done(self) -> None:
        await self.stream.done()


class LoopbackHandler:
    """Handles socket communication with the NSJail runtime.

    This handler:
    1. Accepts connection from runtime on control socket
    2. Sends RuntimeInitPayload with agent config
    3. Reads events and forwards to stream sink
    4. Tracks session updates and approval requests
    5. Persists complete messages to database

    The handler does NOT spawn the NSJail process - that is done by
    the caller (activity) which manages the process lifecycle.
    """

    def __init__(self, input: LoopbackInput) -> None:
        self.input = input
        self._stream_sink: LoopbackEventSink | None = None
        self._result = LoopbackResult(success=False)
        self._sdk_session_id: str | None = None  # Track SDK session ID for this run
        self._stream_done_emitted: bool = False  # Dedupe flag for stream.done()
        # Track which session lines have been persisted to avoid duplicates
        self._persisted_line_uuids: set[str] = set()

    async def _emit_stream_done(self) -> None:
        """Emit stream.done() exactly once.

        This helper ensures the stream end marker is emitted exactly once,
        even if multiple code paths could trigger it (e.g., error + finally).
        """
        if self._stream_sink and not self._stream_done_emitted:
            self._stream_done_emitted = True
            try:
                await self._stream_sink.done()
            except Exception as e:
                logger.warning("Failed to emit stream done", error=str(e))

    async def emit_terminal_error(self, error: str) -> None:
        """Emit a terminal error through the resolved stream sink.

        This is used by executor-level crash/timeout paths that happen outside
        normal loopback event processing.
        """
        if self._stream_sink is None:
            self._stream_sink = await self._initialize_stream_sink()
        await self._stream_sink.error(error)
        await self._emit_stream_done()

    async def handle_connection(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> LoopbackResult:
        """Handle incoming connection from the NSJail runtime.

        This is the main entry point, called when the runtime connects
        to the control socket.

        Args:
            reader: Stream reader for reading runtime events.
            writer: Stream writer for sending init payload.

        Returns:
            LoopbackResult with success status and session updates.
        """
        logger.info(
            "Runtime connected to control socket",
            session_id=self.input.session_id,
        )

        try:
            # Initialize event sink (Redis for UI sessions, channel-specific for external)
            self._stream_sink = await self._initialize_stream_sink()

            # Send init payload to runtime
            await self._send_init_payload(writer)

            # Read and forward events until done
            await self._process_runtime_events(reader)

            # Only set success if no error occurred during event processing
            if self._result.error is None:
                self._result.success = True

        except asyncio.IncompleteReadError:
            # Connection closed during init payload send
            logger.warning("Runtime disconnected unexpectedly during init")
            self._result.error = "Runtime disconnected unexpectedly"
            if self._stream_sink:
                await self._stream_sink.error(self._result.error)
        except Exception as e:
            logger.exception("Error handling runtime connection", error=str(e))
            self._result.error = f"Connection error: {e}"
            if self._stream_sink:
                try:
                    await asyncio.wait_for(self._stream_sink.error(str(e)), timeout=5.0)
                except TimeoutError:
                    logger.warning("Timeout emitting stream error")
        finally:
            # ALWAYS emit done on any exit path to prevent SSE consumers from hanging
            await self._emit_stream_done()
            writer.close()
            await writer.wait_closed()

        return self._result

    async def _send_init_payload(self, writer: asyncio.StreamWriter) -> None:
        """Send RuntimeInitPayload to the runtime."""
        # Convert AgentConfig (Pydantic) to SandboxAgentConfig (dataclass)
        sandbox_config = SandboxAgentConfig.from_agent_config(self.input.config)

        payload = RuntimeInitPayload(
            session_id=self.input.session_id,
            mcp_auth_token=self.input.mcp_auth_token,
            config=sandbox_config,
            user_prompt=self.input.user_prompt,
            litellm_auth_token=self.input.litellm_auth_token,
            allowed_actions=self.input.allowed_actions,
            sdk_session_id=self.input.sdk_session_id,
            sdk_session_data=self.input.sdk_session_data,
            is_approval_continuation=self.input.is_approval_continuation,
            is_fork=self.input.is_fork,
        )

        payload_bytes = orjson.dumps(payload.to_dict())
        message = build_message(MessageType.INIT, payload_bytes)

        writer.write(message)
        await writer.drain()

        logger.debug(
            "Sent init payload to runtime",
            session_id=self.input.session_id,
            payload_size=len(payload_bytes),
        )

    async def _initialize_stream_sink(self) -> LoopbackEventSink:
        """Build stream sink for this session."""

        try:
            external_sink = await self._build_external_channel_sink()
        except SQLAlchemyError as e:
            logger.warning(
                "Failed to resolve external channel sink; falling back to Redis",
                session_id=self.input.session_id,
                workspace_id=self.input.workspace_id,
                error=str(e),
            )
            external_sink = None

        if external_sink is not None:
            return external_sink

        return await self._build_redis_stream_sink()

    async def _build_redis_stream_sink(self) -> AgentStreamSink:
        """Build Redis-backed stream sink."""

        redis_stream = await AgentStream.new(
            session_id=self.input.session_id,
            workspace_id=self.input.workspace_id,
        )
        return AgentStreamSink(stream=redis_stream)

    async def _build_external_channel_sink(self) -> ExternalChannelSink | None:
        """Resolve an external channel sink when session is external."""

        async with get_async_session_context_manager() as session:
            stmt = select(AgentSession).where(
                AgentSession.id == self.input.session_id,
                AgentSession.workspace_id == self.input.workspace_id,
            )
            result = await session.execute(stmt)
            agent_session = result.scalar_one_or_none()

            if agent_session is None:
                logger.warning(
                    "Agent session not found for stream sink resolution",
                    session_id=self.input.session_id,
                )
                return None

            if agent_session.entity_type != AgentSessionEntity.EXTERNAL_CHANNEL.value:
                return None

            if not isinstance(agent_session.channel_context, dict):
                logger.warning(
                    "External channel session missing channel_context; falling back to Redis",
                    session_id=self.input.session_id,
                )
                return None

            channel_context = agent_session.channel_context
            channel_type = channel_context.get("channel_type")
            if not isinstance(channel_type, str):
                # Backward-compatible inference for existing Slack channel context.
                if isinstance(channel_context.get("channel_id"), str) and isinstance(
                    channel_context.get("thread_ts"), str
                ):
                    channel_type = ChannelType.SLACK.value

            if channel_type != ChannelType.SLACK.value:
                logger.warning(
                    "Unsupported external channel type; falling back to Redis",
                    session_id=self.input.session_id,
                    channel_type=channel_type,
                )
                return None

            slack_ctx = ResolvedSlackContext.from_channel_context(channel_context)
            if slack_ctx is None:
                logger.warning(
                    "Slack channel context missing channel_id/thread_ts; falling back to Redis",
                    session_id=self.input.session_id,
                    channel_context=channel_context,
                )
                return None

            preset_id = agent_session.agent_preset_id or agent_session.entity_id
            token_stmt = (
                select(AgentChannelToken)
                .where(
                    AgentChannelToken.workspace_id == self.input.workspace_id,
                    AgentChannelToken.agent_preset_id == preset_id,
                    AgentChannelToken.channel_type == ChannelType.SLACK.value,
                    AgentChannelToken.is_active.is_(True),
                )
                .order_by(AgentChannelToken.updated_at.desc())
                .limit(1)
            )
            token_result = await session.execute(token_stmt)
            channel_token = token_result.scalar_one_or_none()
            if channel_token is None:
                logger.warning(
                    "Active Slack channel token not found; falling back to Redis",
                    session_id=self.input.session_id,
                    preset_id=preset_id,
                )
                return None

            try:
                config = AgentChannelService.parse_stored_channel_config(
                    channel_type=ChannelType.SLACK,
                    config_payload=channel_token.config,
                )
            except TracecatValidationError:
                logger.exception(
                    "Slack token config is invalid; falling back to Redis",
                    session_id=self.input.session_id,
                    token_id=channel_token.id,
                )
                return None

            logger.info(
                "Using Slack stream sink for external channel session",
                session_id=self.input.session_id,
                workspace_id=self.input.workspace_id,
                channel_type=ChannelType.SLACK.value,
                slack_channel_id=slack_ctx.channel_id,
                reaction_ts=slack_ctx.reaction_ts,
            )
            if config.slack_bot_token == PENDING_SLACK_BOT_TOKEN:
                logger.warning(
                    "Slack bot token is pending OAuth install; falling back to Redis",
                    session_id=self.input.session_id,
                    token_id=channel_token.id,
                )
                return None
            return SlackStreamSink(
                slack_bot_token=config.slack_bot_token,
                channel_id=slack_ctx.channel_id,
                thread_ts=slack_ctx.thread_ts,
                recipient_user_id=slack_ctx.recipient_user_id,
                recipient_team_id=slack_ctx.recipient_team_id,
                reaction_ts=slack_ctx.reaction_ts,
                session_id=str(self.input.session_id),
                workspace_id=str(self.input.workspace_id),
            )

    async def _process_runtime_events(self, reader: asyncio.StreamReader) -> None:
        """Read and process events from the runtime.

        Forwards streaming events to Redis, persists complete messages to DB,
        and handles session updates.
        """
        if self._stream_sink is None:
            raise RuntimeError("Stream sink not initialized")

        while True:
            try:
                _msg_type, payload_bytes = await read_message(
                    reader, expected_type=MessageType.EVENT
                )
            except asyncio.IncompleteReadError:
                # Connection closed unexpectedly - treat as error, not silent break
                logger.warning(
                    "Runtime connection closed unexpectedly during execution"
                )
                self._result.error = "Runtime disconnected during execution"
                await self._stream_sink.error(self._result.error)
                break  # done() will be called in finally of handle_connection

            # Parse the envelope
            envelope = RuntimeEventEnvelope.from_dict(orjson.loads(payload_bytes))

            match envelope.type:
                case "stream_event":
                    # Forward streaming event to sink (Redis/UI or external channel)
                    if envelope.event:
                        logger.debug(
                            "Forwarding stream event",
                            event_type=envelope.event.type,
                            session_id=self.input.session_id,
                        )
                        await self._stream_sink.append(envelope.event)

                        # Check for error events (e.g., from LiteLLM/SDK)
                        if envelope.event.type == StreamEventType.ERROR:
                            error_msg = envelope.event.error or "Unknown error"
                            logger.error(
                                "Error event received from runtime",
                                session_id=self.input.session_id,
                                error=error_msg,
                            )
                            await self._stream_sink.error(error_msg)
                            await self._emit_stream_done()
                            self._result.error = error_msg
                            break

                        # Check for approval request
                        if envelope.event.type == StreamEventType.APPROVAL_REQUEST:
                            logger.info(
                                "Approval request received",
                                session_id=self.input.session_id,
                                items=envelope.event.approval_items,
                            )
                            self._result.approval_requested = True
                            # Convert from shared dataclass to Pydantic model
                            self._result.approval_items = [
                                ToolCallContent(
                                    id=item.id,
                                    name=item.name,
                                    input=item.input,
                                )
                                for item in (envelope.event.approval_items or [])
                            ]

                case "message":
                    # Complete message (inner only) - legacy, skip if session_line is used
                    # Kept for backward compatibility with UI events
                    pass

                case "session_line":
                    # Raw JSONL line from SDK session file - persist for resume
                    if envelope.session_line and envelope.sdk_session_id:
                        await self._persist_session_line(
                            envelope.sdk_session_id,
                            envelope.session_line,
                            internal=envelope.internal,
                        )

                case "result":
                    # Final result with usage data and structured output
                    self._result.output = envelope.result_output
                    self._result.result_usage = envelope.result_usage
                    self._result.result_num_turns = envelope.result_num_turns

                case "error":
                    # Runtime error - stream error and close the stream
                    error_msg = envelope.error or "Unknown runtime error"
                    logger.error("Runtime error", error=error_msg)
                    await self._stream_sink.error(error_msg)
                    await self._emit_stream_done()  # Use helper (dedupes with finally)
                    self._result.error = error_msg
                    break

                case "done":
                    # Runtime completed successfully
                    logger.info(
                        "Runtime completed",
                        session_id=self.input.session_id,
                    )
                    await self._emit_stream_done()  # Use helper (dedupes with finally)
                    break

                case "log":
                    # Log message from runtime - forward to worker logger
                    level = envelope.log_level or "info"
                    message = envelope.log_message or "Runtime log"
                    extra = envelope.log_extra or {}
                    log_fn = getattr(logger, level, logger.info)
                    # Use opt(raw=False) to prevent loguru from parsing {} in message
                    log_fn(
                        "[runtime] {}",
                        message,
                        session_id=self.input.session_id,
                        **extra,
                    )

    async def _persist_session_line(
        self, sdk_session_id: str, session_line: str, *, internal: bool = False
    ) -> None:
        """Persist sanitized JSONL line from SDK session file.

        Writes to AgentSessionHistory only. The session_id in self.input
        is the AgentSession.id for new chats, so all writes go to the
        correct session history table.

        Deduplication: Each JSONL line has a unique 'uuid' field. We track
        persisted UUIDs to prevent duplicates from race conditions (e.g.,
        session lines arriving before StreamEvent sets up indexes).

        Args:
            sdk_session_id: The SDK's internal session ID (for JSONL reconstruction).
            session_line: Raw JSONL line from the SDK session file.
            internal: If True, this is internal state not shown in UI timeline.
        """
        # Parse and sanitize to prevent XSS from untrusted content (e.g., tool results)
        line_data = orjson.loads(session_line)

        # Deduplicate by UUID - each JSONL line has a unique uuid field
        line_uuid = line_data.get("uuid")
        if line_uuid and line_uuid in self._persisted_line_uuids:
            logger.debug(
                "Skipping duplicate session line",
                uuid=line_uuid,
                session_id=self.input.session_id,
            )
            return

        logger.debug(
            "Persisting session line",
            session_id=self.input.session_id,
            sdk_session_id=sdk_session_id,
            internal=internal,
            uuid=line_uuid,
        )

        async with get_async_session_context_manager() as session:
            # On first session line, update AgentSession with sdk_session_id
            if self._sdk_session_id is None:
                self._sdk_session_id = sdk_session_id
                stmt = select(AgentSession).where(
                    AgentSession.id == self.input.session_id,
                    AgentSession.workspace_id == self.input.workspace_id,
                )
                result = await session.execute(stmt)
                agent_session = result.scalar_one_or_none()
                if agent_session and agent_session.sdk_session_id is None:
                    agent_session.sdk_session_id = sdk_session_id
                    logger.info(
                        "Updated AgentSession with sdk_session_id",
                        session_id=self.input.session_id,
                        sdk_session_id=sdk_session_id,
                    )

            # Use explicit internal flag from runtime, not content-based heuristics
            kind = "internal" if internal else "chat-message"

            history_entry = AgentSessionHistory(
                session_id=self.input.session_id,
                workspace_id=self.input.workspace_id,
                content=line_data,
                kind=kind,
            )
            session.add(history_entry)
            await session.commit()

        # Track as persisted after successful commit
        if line_uuid:
            self._persisted_line_uuids.add(line_uuid)
