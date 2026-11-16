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
from tracecat.db.models import AgentChannelBot, AgentSession, AgentSessionThread
from tracecat.tiers.access import is_org_entitled
from tracecat.tiers.enums import Entitlement


async def channel_threads_by_session(
    session: AsyncSession,
    role: Role,
    sessions: Sequence[AgentSession],
) -> dict[uuid.UUID, InboxChannelThread]:
    """The linked thread's channel by session ID, in one query; empty without the
    channel bots preview."""
    session_ids = [
        s.id for s in sessions if s.entity_type == AgentSessionEntity.EXTERNAL_CHANNEL
    ]
    if (
        not session_ids
        or role.workspace_id is None
        or role.organization_id is None
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
        )
        .join(AgentChannelBot, AgentChannelBot.id == AgentSessionThread.bot_id)
        .where(
            AgentSessionThread.workspace_id == role.workspace_id,
            AgentSessionThread.session_id.in_(session_ids),
        )
    )
    result = await session.execute(stmt)
    threads: dict[uuid.UUID, InboxChannelThread] = {}
    for session_id, bot_id, provider_thread_id, provider_name in result.tuples():
        provider = ChannelBotProvider(provider_name)
        channel_id = admin.thread_channel_id(provider, provider_thread_id)
        if channel_id is not None:
            threads[session_id] = InboxChannelThread(
                bot_id=bot_id, provider=provider, provider_channel_id=channel_id
            )
    return threads
