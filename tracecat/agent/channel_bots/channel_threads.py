"""The channel each external-channel session's linked provider thread is in."""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.agent.channel_bots.provider import get_channel_bot_admin
from tracecat.agent.channel_bots.schemas import (
    ChannelBotProvider,
    InboxChannelThread,
)
from tracecat.agent.session.types import AgentSessionEntity
from tracecat.auth.types import Role
from tracecat.authz.controls import has_scope
from tracecat.db.models import (
    AgentChannelBot,
    AgentSession,
    AgentSessionHistory,
    AgentSessionThread,
)
from tracecat.tiers.access import is_org_entitled
from tracecat.tiers.enums import Entitlement


async def channel_threads_by_session(
    session: AsyncSession,
    role: Role,
    sessions: Sequence[AgentSession],
) -> dict[uuid.UUID, InboxChannelThread]:
    """The linked thread's channel by external-channel session ID, in one query."""
    session_ids = [
        s.id for s in sessions if s.entity_type == AgentSessionEntity.EXTERNAL_CHANNEL
    ]
    return await channel_threads(session, role, session_ids)


async def channel_threads(
    session: AsyncSession,
    role: Role,
    session_ids: Sequence[uuid.UUID],
) -> dict[uuid.UUID, InboxChannelThread]:
    """The linked thread's channel by session ID, for any session type; empty without
    the channel bots preview or `agent:read`, which the channel bot API requires."""
    if (
        not session_ids
        or role.workspace_id is None
        or role.organization_id is None
        or not has_scope(role.scopes or frozenset(), "agent:read")
        or (admin := get_channel_bot_admin()) is None
        or not await is_org_entitled(
            session, role.organization_id, Entitlement.AGENT_CHANNELS_PREVIEW
        )
    ):
        return {}
    stmt = (
        select(
            AgentSessionThread.session_id,
            AgentSessionThread.bot_id,
            AgentSessionThread.provider_thread_id,
            AgentChannelBot.provider,
            AgentChannelBot.provider_bot_user_id,
        )
        .join(AgentChannelBot, AgentChannelBot.id == AgentSessionThread.bot_id)
        .where(
            AgentSessionThread.workspace_id == role.workspace_id,
            AgentSessionThread.session_id.in_(session_ids),
        )
    )
    result = await session.execute(stmt)
    threads: dict[uuid.UUID, InboxChannelThread] = {}
    for session_id, bot_id, thread_id, provider_name, bot_user in result.tuples():
        provider = ChannelBotProvider(provider_name)
        channel_id = admin.thread_channel_id(provider, thread_id)
        if channel_id is not None:
            threads[session_id] = InboxChannelThread(
                bot_id=bot_id,
                provider=provider,
                provider_channel_id=channel_id,
                provider_bot_user_id=bot_user,
            )
    return threads


async def channel_thread_starters(
    session: AsyncSession,
    role: Role,
    session_ids: Sequence[uuid.UUID],
) -> dict[uuid.UUID, uuid.UUID]:
    """The author of each session's earliest authored message, by session ID.

    Display only: Inbox shows it as Created by; it never owns the session.
    """
    if not session_ids or role.workspace_id is None:
        return {}
    stmt = (
        select(AgentSessionHistory.session_id, AgentSessionHistory.author_id)
        .where(
            AgentSessionHistory.workspace_id == role.workspace_id,
            AgentSessionHistory.session_id.in_(session_ids),
            AgentSessionHistory.author_id.is_not(None),
        )
        .distinct(AgentSessionHistory.session_id)
        .order_by(AgentSessionHistory.session_id, AgentSessionHistory.surrogate_id)
    )
    result = await session.execute(stmt)
    return {
        session_id: author_id
        for session_id, author_id in result.tuples()
        if author_id is not None
    }
