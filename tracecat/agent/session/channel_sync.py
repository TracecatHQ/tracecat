"""A session's sync with its channel thread: the flag is the thread link's.

The installed channel notifier opens threads and posts to them; this module only
reads and flips ``AgentSessionThread.sync_enabled``.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select, update

from tracecat.agent.channel_notifier import (
    can_open_channel_thread,
    notify_sync_stopped,
    open_channel_thread,
)
from tracecat.agent.session.schemas import AgentSessionChannelSyncRead
from tracecat.agent.session.service import AgentSessionService
from tracecat.db.models import AgentSessionThread
from tracecat.exceptions import TracecatConflictError


class ChannelThreadUnavailable(TracecatConflictError):
    """The session has no thread and no channel to open one in."""


async def get_channel_sync(
    svc: AgentSessionService, session_id: uuid.UUID
) -> AgentSessionChannelSyncRead:
    """The session's sync state; a session without a thread asks the notifier."""
    enabled = await get_channel_sync_enabled(svc, session_id)
    if enabled is not None:
        return AgentSessionChannelSyncRead(available=True, enabled=enabled)
    available = await can_open_channel_thread(svc.role, session_id)
    return AgentSessionChannelSyncRead(available=available, enabled=False)


async def set_channel_sync(
    svc: AgentSessionService, session_id: uuid.UUID, *, enabled: bool
) -> AgentSessionChannelSyncRead:
    """Turn sync on or off as ``svc.role``; forward only, nothing earlier is replayed.

    Raises:
        ChannelThreadUnavailable: Turning on a session no thread can be opened for.
    """
    if enabled and await get_channel_sync_enabled(svc, session_id) is None:
        if not await open_channel_thread(svc.role, session_id):
            raise ChannelThreadUnavailable(
                "No channel thread can be opened for this session"
            )
        return AgentSessionChannelSyncRead(available=True, enabled=True)
    changed = await svc.session.scalar(
        update(AgentSessionThread)
        .where(
            AgentSessionThread.session_id == session_id,
            AgentSessionThread.workspace_id == svc.workspace_id,
            AgentSessionThread.sync_enabled.is_not(enabled),
        )
        .values(sync_enabled=enabled)
        .returning(AgentSessionThread.session_id)
    )
    await svc.session.commit()
    if changed is not None and not enabled:
        # After the commit: the thread is told once, by the request that stopped it.
        await notify_sync_stopped(svc.role, session_id)
    current = await get_channel_sync_enabled(svc, session_id)
    return AgentSessionChannelSyncRead(
        available=current is not None, enabled=bool(current)
    )


async def get_channel_sync_enabled(
    svc: AgentSessionService, session_id: uuid.UUID
) -> bool | None:
    """The link's flag; None when the session has no thread."""
    return await svc.session.scalar(
        select(AgentSessionThread.sync_enabled).where(
            AgentSessionThread.session_id == session_id,
            AgentSessionThread.workspace_id == svc.workspace_id,
        )
    )
