"""External channel handler registry."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.agent.channels.handlers.base import ExternalChannelHandler
from tracecat.agent.channels.handlers.slack import SlackChannelHandler
from tracecat.agent.channels.schemas import ChannelType
from tracecat.auth.types import Role


def build_channel_handler(
    *,
    channel_type: ChannelType,
    session: AsyncSession,
    role: Role,
) -> ExternalChannelHandler:
    """Construct an external channel handler for the given channel type."""

    if channel_type is ChannelType.SLACK:
        return SlackChannelHandler(session=session, role=role)
    raise ValueError(f"Unsupported channel type: {channel_type.value}")
