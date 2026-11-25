"""A session's sync with its channel thread: the flag is the thread link's.

The installed channel notifier opens threads and posts to them; this module reads
and flips ``AgentSessionThread.sync_enabled``, and ends a turn that started unlinked.
"""

from __future__ import annotations

import uuid

from sqlalchemy import exists, select, update

from tracecat.agent.channel_bots.channel_threads import channel_threads
from tracecat.agent.channel_notifier import (
    ChannelDestinationRef,
    ChannelReplyError,
    NoChannelBot,
    TurnFinished,
    can_open_channel_thread,
    check_channel_reply_thread,
    get_channel_notifier,
    is_channel_linked,
    notify_sync_stopped,
    open_channel_reply_thread,
    open_channel_thread,
    push_updates,
)
from tracecat.agent.session.schemas import AgentSessionChannelSyncRead
from tracecat.agent.session.service import AgentSessionService
from tracecat.agent.stream.feed import publish_channel_sync_failed
from tracecat.auth.types import Role
from tracecat.db.models import AgentChannelBot, AgentSession, AgentSessionThread
from tracecat.exceptions import TracecatConflictError
from tracecat.logger import logger


class ChannelThreadUnavailable(TracecatConflictError):
    """The session has no thread and no channel to open one in."""


async def get_channel_sync(
    svc: AgentSessionService, session_id: uuid.UUID
) -> AgentSessionChannelSyncRead:
    """The session's sync state; a session without a thread asks the notifier."""
    enabled = await get_channel_sync_enabled(svc, session_id)
    if enabled is not None:
        return await with_channel_thread(
            svc,
            session_id,
            AgentSessionChannelSyncRead(available=True, enabled=enabled, linked=True),
        )
    # End the read transaction: no connection sits idle across the notifier's calls.
    await svc.session.commit()
    available = await can_open_channel_thread(svc.role, session_id)
    return AgentSessionChannelSyncRead(available=available, enabled=False)


async def set_channel_sync(
    svc: AgentSessionService,
    session_id: uuid.UUID,
    *,
    enabled: bool,
    destination: ChannelDestinationRef | None = None,
) -> AgentSessionChannelSyncRead:
    """Turn sync on or off as ``svc.role``; forward only, nothing earlier is replayed.

    ``destination`` picks where a session without a thread opens one.

    Raises:
        ChannelThreadUnavailable: Turning on a session no thread can be opened for.
    """
    if enabled and await get_channel_sync_enabled(svc, session_id) is None:
        # End the read transaction: no connection sits idle across the Slack calls.
        await svc.session.commit()
        if not await open_channel_thread(svc.role, session_id, destination):
            raise ChannelThreadUnavailable(
                "No channel thread can be opened for this session"
            )
        return await with_channel_thread(
            svc,
            session_id,
            AgentSessionChannelSyncRead(available=True, enabled=True, linked=True),
        )
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
    return await with_channel_thread(
        svc,
        session_id,
        AgentSessionChannelSyncRead(
            available=current is not None,
            enabled=bool(current),
            linked=current is not None,
        ),
    )


async def with_channel_thread(
    svc: AgentSessionService,
    session_id: uuid.UUID,
    sync: AgentSessionChannelSyncRead,
) -> AgentSessionChannelSyncRead:
    """``sync`` with its linked thread's channel, which names Slack-sent messages."""
    if not sync.linked:
        return sync
    threads = await channel_threads(svc.session, svc.role, [session_id])
    return sync.model_copy(update={"thread": threads.get(session_id)})


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


async def check_turn_destination(
    role: Role, agent_session: AgentSession, destination: ChannelDestinationRef
) -> None:
    """Refuse a web turn's destination before the turn starts.

    Raises:
        ChannelReplyError: The session's agent cannot open a thread there.
    """
    if role.type != "user" or role.user_id is None:
        # The thread's root is the sender's prompt, as them; a service has none.
        raise ChannelReplyError("Only a person's message can open a Slack thread")
    if agent_session.agent_preset_id is None:
        raise NoChannelBot("The agent has no live channel bot that opens threads")
    try:
        await check_channel_reply_thread(
            role, agent_session.agent_preset_id, destination
        )
    except TimeoutError as e:
        raise ChannelReplyError(
            "Slack didn't respond. Turn sync on to try again."
        ) from e


async def has_channel_bot(role: Role) -> bool:
    """Whether the workspace has an active channel bot, failing closed.

    Gates a turn's end-of-turn link lookup: sessions without channels pay nothing.
    """
    if role.workspace_id is None:
        return False
    try:
        if get_channel_notifier() is None:
            return False
        async with AgentSessionService.with_session(role) as svc:
            return bool(
                await svc.session.scalar(
                    select(
                        exists().where(
                            AgentChannelBot.workspace_id == svc.workspace_id,
                            AgentChannelBot.status == "active",
                        )
                    )
                )
            )
    except Exception as e:
        logger.warning("Failed to check for a channel bot", error=repr(e))
        return False


async def push_late_turn_finished(
    role: Role,
    session_id: uuid.UUID,
    run_id: uuid.UUID,
    destination: ChannelDestinationRef | None = None,
) -> None:
    """End a turn that started unlinked: open the thread it carried, then post the reply.

    Reads the link at delivery, so sync turned on mid-turn gets this reply. Never raises.
    """
    try:
        if destination is not None:
            if not await open_turn_destination(role, session_id, destination):
                return
        elif not await is_channel_linked(role, session_id):
            return
        await push_updates(role, [TurnFinished(session_id=session_id, run_id=run_id)])
    except Exception as e:
        logger.warning(
            "Failed to deliver a turn to a thread linked after it started",
            session_id=str(session_id),
            error=repr(e),
        )


async def open_turn_destination(
    role: Role, session_id: uuid.UUID, destination: ChannelDestinationRef
) -> bool:
    """Open the thread a web turn carried; False, with a feed notice, when it can't."""
    try:
        await open_channel_reply_thread(role, session_id, destination)
        return True
    except ChannelReplyError as e:
        reason = str(e)
    except Exception as e:
        logger.warning(
            "Failed to open a turn's channel thread",
            session_id=str(session_id),
            error=repr(e),
        )
        reason = "Slack didn't respond. Turn sync on to try again."
    if role.workspace_id is not None:
        await publish_channel_sync_failed(
            workspace_id=role.workspace_id,
            session_id=session_id,
            reason=f"Couldn't open Slack thread: {reason}",
        )
    return False
