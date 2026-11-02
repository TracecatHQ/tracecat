"""Session event sinks shared by every agent backend.

A backend opens one sink per turn segment. Events reach the session's Redis
stream and, for external-channel sessions, the channel itself (e.g. a Slack
thread), so channel output does not depend on which runtime produced it.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from tracecat.agent.channels.schemas import ChannelType
from tracecat.agent.channels.service import PENDING_SLACK_BOT_TOKEN, AgentChannelService
from tracecat.agent.channels.sinks.base import ExternalChannelSink
from tracecat.agent.channels.sinks.slack import SlackStreamSink
from tracecat.agent.common.stream_types import UnifiedStreamEvent
from tracecat.agent.session.types import AgentSessionEntity
from tracecat.agent.stream.connector import AgentStream
from tracecat.db.engine import get_async_session_context_manager
from tracecat.db.models import AgentChannelToken, AgentSession
from tracecat.exceptions import TracecatValidationError
from tracecat.logger import logger

type SinkOperation = Callable[[SessionEventSink], Awaitable[None]]


@dataclass(frozen=True, kw_only=True, slots=True)
class ResolvedSlackContext:
    """Parsed and validated Slack channel context for sink construction."""

    channel_id: str
    thread_ts: str
    recipient_user_id: str | None = None
    recipient_team_id: str | None = None
    reaction_ts: str | None = None

    @classmethod
    def from_channel_context(
        cls,
        ctx: Mapping[str, object],
    ) -> ResolvedSlackContext | None:
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


class SessionEventSink(Protocol):
    """Sink interface for streaming a session's runtime events."""

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


@dataclass(kw_only=True, slots=True)
class FanoutStreamSink:
    """Broadcasts stream events to multiple sinks.

    Used for external-channel sessions where we need Slack thread updates and
    Redis stream updates (for inbox/UI) in parallel.
    """

    sinks: tuple[SessionEventSink, ...]

    async def _broadcast(
        self,
        operation_name: str,
        operation: SinkOperation,
        sinks: tuple[SessionEventSink, ...] | None = None,
    ) -> None:
        targets = self.sinks if sinks is None else sinks
        failures = 0
        for sink in targets:
            try:
                await operation(sink)
            except Exception as exc:
                failures += 1
                logger.warning(
                    "Fanout stream sink target failed",
                    method=operation_name,
                    sink_type=type(sink).__name__,
                    error=str(exc),
                )
        if targets and failures == len(targets):
            raise RuntimeError(f"All fanout sinks failed for method '{operation_name}'")

    async def append(self, event: UnifiedStreamEvent) -> None:
        await self._broadcast("append", lambda sink: sink.append(event))

    async def error(self, error: str) -> None:
        await self._broadcast("error", lambda sink: sink.error(error))

    async def done(self) -> None:
        await self._broadcast("done", lambda sink: sink.done())

    async def done_external(self) -> None:
        """Complete external-channel sinks while leaving Redis open."""
        external_sinks = tuple(
            sink for sink in self.sinks if not isinstance(sink, AgentStreamSink)
        )
        await self._broadcast(
            "done_external",
            lambda sink: sink.done(),
            external_sinks,
        )


async def open_session_event_sink(
    *,
    session_id: uuid.UUID,
    workspace_id: uuid.UUID,
    stream_id: uuid.UUID | None,
) -> SessionEventSink:
    """Open the session's Redis stream, fanned out to its external channel if any.

    Args:
        session_id: The agent session producing events.
        workspace_id: The session's workspace.
        stream_id: The per-turn stream identity shared with readers.

    Returns:
        A Redis sink, or a fanout sink when the session is bound to a
        configured external channel.
    """
    try:
        external_sink = await _build_external_channel_sink(
            session_id=session_id, workspace_id=workspace_id
        )
    except SQLAlchemyError as e:
        logger.warning(
            "Failed to resolve external channel sink; falling back to Redis",
            session_id=session_id,
            workspace_id=workspace_id,
            error=str(e),
        )
        external_sink = None

    redis_stream = await AgentStream.new(
        session_id=session_id,
        workspace_id=workspace_id,
        stream_id=stream_id,
    )
    redis_sink = AgentStreamSink(stream=redis_stream)
    if external_sink is not None:
        return FanoutStreamSink(sinks=(redis_sink, external_sink))

    return redis_sink


async def _build_external_channel_sink(
    *, session_id: uuid.UUID, workspace_id: uuid.UUID
) -> ExternalChannelSink | None:
    """Resolve an external channel sink when session is external."""

    async with get_async_session_context_manager() as session:
        stmt = select(AgentSession).where(
            AgentSession.id == session_id,
            AgentSession.workspace_id == workspace_id,
        )
        result = await session.execute(stmt)
        agent_session = result.scalar_one_or_none()

        if agent_session is None:
            logger.warning(
                "Agent session not found for stream sink resolution",
                session_id=session_id,
            )
            return None

        if agent_session.entity_type != AgentSessionEntity.EXTERNAL_CHANNEL.value:
            return None

        if not isinstance(agent_session.channel_context, dict):
            logger.warning(
                "External channel session missing channel_context; falling back to Redis",
                session_id=session_id,
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
                session_id=session_id,
                channel_type=channel_type,
            )
            return None

        slack_ctx = ResolvedSlackContext.from_channel_context(channel_context)
        if slack_ctx is None:
            logger.warning(
                "Slack channel context missing channel_id/thread_ts; falling back to Redis",
                session_id=session_id,
                channel_context=channel_context,
            )
            return None

        preset_id = agent_session.agent_preset_id or agent_session.entity_id
        token_stmt = (
            select(AgentChannelToken)
            .where(
                AgentChannelToken.workspace_id == workspace_id,
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
                session_id=session_id,
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
                session_id=session_id,
                token_id=channel_token.id,
            )
            return None

        logger.info(
            "Using Slack stream sink for external channel session",
            session_id=session_id,
            workspace_id=workspace_id,
            channel_type=ChannelType.SLACK.value,
            slack_channel_id=slack_ctx.channel_id,
            reaction_ts=slack_ctx.reaction_ts,
        )
        if config.slack_bot_token == PENDING_SLACK_BOT_TOKEN:
            logger.warning(
                "Slack bot token is pending OAuth install; falling back to Redis",
                session_id=session_id,
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
            session_id=str(session_id),
            workspace_id=str(workspace_id),
        )
