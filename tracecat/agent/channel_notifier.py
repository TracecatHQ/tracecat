"""Run updates and the channel notifier that posts them to a linked channel.

Agent workflows hand a thin update of what happened, by id, to the lifecycle
activity that makes it true; that activity pushes it after its own writes.
An installed channel notifier reads the turn through open-source services,
then renders and posts it. Updates are stored in Temporal history: evolve
additively with defaults, and roll a new field out to workers before workflows
send it, since unknown fields stay rejected.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import Iterable, Sequence
from datetime import timedelta
from functools import lru_cache
from importlib.metadata import entry_points
from typing import Annotated, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field
from temporalio.exceptions import ApplicationError

from tracecat.agent.channels.messages import ChannelMessage
from tracecat.auth.types import Role
from tracecat.core.schemas import Variant
from tracecat.db.engine import get_async_session_context_manager
from tracecat.logger import logger
from tracecat.tiers.access import is_org_entitled
from tracecat.tiers.enums import Entitlement

CHANNEL_NOTIFIER_ENTRY_POINT_GROUP = "tracecat.agent_channel_notifier"

# Per update, retries included; a host activity's start_to_close grows by this.
PUSH_BUDGET = timedelta(seconds=20)
# A status line is not worth holding the turn's start for.
TURN_STARTED_PUSH_BUDGET = timedelta(seconds=5)
# A linked turn's start pushes at most a relayed message and the status line.
TURN_START_PUSH_BUDGET = 2 * TURN_STARTED_PUSH_BUDGET
_PUSH_ATTEMPTS = 3
# Bounded well inside create_session_activity's 30 s budget.
CHANNEL_LINK_CHECK_TIMEOUT_SECONDS = 5
# A turn's end read unlinked at its start: open its thread, check, post the reply.
LATE_TURN_FINISHED_BUDGET = 2 * PUSH_BUDGET + timedelta(
    seconds=CHANNEL_LINK_CHECK_TIMEOUT_SECONDS
)


class RunUpdateBase(Variant, discriminator="kind"):
    model_config = ConfigDict(frozen=True, extra="forbid")

    session_id: uuid.UUID


class MessageSent(RunUpdateBase):
    """A Tracecat user's message started a run; show it in the channel.

    Built inside the turn's start activity, never stored in workflow history.
    """

    kind: Literal["message_sent"] = "message_sent"
    # The run that first sent the message: a Retry keeps it, so the post is one.
    run_id: uuid.UUID
    user_id: uuid.UUID
    text: str = Field(min_length=1)


class TurnStarted(RunUpdateBase):
    """A run began work; show the channel that the agent is working."""

    kind: Literal["turn_started"] = "turn_started"
    run_id: uuid.UUID


class TurnFinished(RunUpdateBase):
    """A run ended, whatever its outcome.

    Sent before the run releases the session: read its rows with
    ``include_active=True``.
    """

    kind: Literal["turn_finished"] = "turn_finished"
    run_id: uuid.UUID


def approval_batch_id(run_id: uuid.UUID, tool_call_ids: Iterable[str]) -> uuid.UUID:
    """Identify one approval pause from the paused run and its pause's tool call ids.

    Callers must pass the same set: the pause's approval items, or the rows
    persisted for them.
    """
    # JSON keeps distinct sets distinct whatever the ids contain. Prefixed so it
    # never equals the continuation stream id derived from the same run.
    ids = json.dumps(sorted(set(tool_call_ids)))
    return uuid.uuid5(run_id, f"approval-batch:{ids}")


class ApprovalPending(RunUpdateBase):
    """A run paused on a batch of tool approvals."""

    kind: Literal["approval_pending"] = "approval_pending"
    run_id: uuid.UUID
    batch_id: uuid.UUID


class ApprovalDecided(RunUpdateBase):
    """Every approval in a batch is decided; edit the message posted for it."""

    kind: Literal["approval_decided"] = "approval_decided"
    batch_id: uuid.UUID
    # None when ApprovalPending returned no ref: find the message by batch_id.
    pending_ref: str | None


RunUpdate = Annotated[
    MessageSent | TurnStarted | TurnFinished | ApprovalPending | ApprovalDecided,
    Field(discriminator="kind"),
]


def turn_started_updates(
    role: Role,
    session_id: uuid.UUID,
    run_id: uuid.UUID,
    prompt: str | None,
    *,
    prompt_run_id: uuid.UUID | None = None,
) -> list[RunUpdate]:
    """What a linked turn's start pushes: a Tracecat user's prompt, then the status.

    Pass ``prompt`` only for a new message, never for a resumed run, and
    ``prompt_run_id`` when the prompt is a retry of the one that run sent.
    """
    started = TurnStarted(session_id=session_id, run_id=run_id)
    # Only a user role authors a prompt, as history stamps it; service roles never relay.
    if (
        role.type != "user"
        or role.user_id is None
        or prompt is None
        or not prompt.strip()
    ):
        return [started]
    # A web Retry of a Slack message resends its envelope; the thread already has it.
    if ChannelMessage.parse(prompt.lstrip()) is not None:
        return [started]
    sent = MessageSent(
        session_id=session_id,
        run_id=prompt_run_id or run_id,
        user_id=role.user_id,
        text=prompt,
    )
    return [sent, started]


class ChannelDestinationRef(BaseModel):
    """Where to open a session's thread: a channel by id, or a DM to the opting-in user."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["direct_message", "channel"]
    # A workflow's DM names its recipient by email; a web turn's DM is its sender's.
    id: str | None = Field(
        default=None, max_length=255, description="The channel's id; None for a DM"
    )

    @classmethod
    def from_input(cls, value: str) -> ChannelDestinationRef:
        """An action's ``channel`` input: an email names a DM, anything else a channel."""
        value = value.strip()
        return cls(kind="direct_message" if "@" in value else "channel", id=value)


class LateChannelDelivery(BaseModel):
    """A turn that started unlinked: open its destination, then post its reply if linked.

    Without a destination, the end only re-reads the link (sync turned on mid-turn).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    destination: ChannelDestinationRef | None = None


class ChannelDestination(ChannelDestinationRef):
    """A place the session's bot can open its thread, for the picker."""

    name: str = Field(description="The channel's name, without its prefix")
    recent: bool = Field(
        default=False, description="The user's sessions synced here lately"
    )


class ChannelReplyError(Exception):
    """A turn's destination cannot take its thread."""


class ChannelsNotEntitled(ChannelReplyError):
    """The organization has no Agent Channels preview."""


class NoChannelBot(ChannelReplyError):
    """The preset has no live bot that opens threads."""


class ChannelDestinationNotRoutable(ChannelReplyError):
    """The preset's bot cannot reach the destination."""


class ChannelRecipientNotLinked(ChannelDestinationNotRoutable):
    """The DM's recipient has not linked their provider account."""


class ChannelPushRetryAfter(Exception):
    """Raised by a notifier when the provider asks the caller to wait."""

    def __init__(self, retry_after: timedelta) -> None:
        super().__init__(f"Channel provider asked to retry after {retry_after}")
        self.retry_after = retry_after


class ChannelNotifier(Protocol):
    """Posts run updates to the channel a session is linked to.

    Raise ``ChannelPushRetryAfter`` to honor a provider's Retry-After, and
    a non-retryable ``ApplicationError`` for a definitive rejection. The
    constructor runs once per process, on the API or worker loop; methods
    must not block it.
    Pushing must be idempotent per update: a retried host activity resends.
    """

    # True when the channel draws artifact mentions in replies instead of raw URIs.
    renders_mentions: bool

    async def is_linked(self, role: Role, session_id: uuid.UUID) -> bool:
        """Return whether the session is linked to a channel."""
        ...

    async def push(self, role: Role, update: RunUpdate) -> str | None:
        """Post the update and return the posted message's ref, if any."""
        ...

    async def can_open_thread(self, role: Role, session_id: uuid.UUID) -> bool:
        """Return whether the session's agent has a bot that can open its thread."""
        ...

    async def list_destinations(
        self, role: Role, session_id: uuid.UUID
    ) -> list[ChannelDestination]:
        """Where ``role.user_id`` can open the session's thread; empty when nowhere."""
        ...

    async def open_thread(
        self,
        role: Role,
        session_id: uuid.UUID,
        destination: ChannelDestinationRef | None = None,
    ) -> bool:
        """Open a thread for an unlinked session and link it, synced; False when none can be.

        ``role.user_id`` is the Tracecat user who opted the session in. Without a
        ``destination`` the agent's one routed channel is used.
        """
        ...

    async def sync_stopped(self, role: Role, session_id: uuid.UUID) -> None:
        """Tell the linked thread that ``role.user_id`` stopped syncing the session."""
        ...

    async def check_reply_thread(
        self, role: Role, preset_id: uuid.UUID, destination: ChannelDestinationRef
    ) -> None:
        """Raise a ``ChannelReplyError`` unless the preset's bot can open a thread there.

        A DM without an id goes to ``role.user_id``.
        """
        ...

    async def open_reply_thread(
        self, role: Role, session_id: uuid.UUID, destination: ChannelDestinationRef
    ) -> None:
        """Root a synced thread at the latest prompt, else the reply, and link it.

        Raises as ``check_reply_thread`` does; a linked session is left as is.
        """
        ...


@lru_cache(maxsize=1)
def get_channel_notifier() -> ChannelNotifier | None:
    """Build the installed notifier from its entry point; None when there is none.

    The entry point names a zero-argument callable, such as the notifier class.
    """
    installed = list(entry_points(group=CHANNEL_NOTIFIER_ENTRY_POINT_GROUP))
    if len(installed) > 1:
        names = ", ".join(sorted(entry_point.name for entry_point in installed))
        raise RuntimeError(f"Multiple channel notifiers installed: {names}")
    return installed[0].load()() if installed else None


async def is_channel_linked(role: Role, session_id: uuid.UUID) -> bool:
    """Ask the notifier whether a session is linked, failing closed."""
    try:
        notifier = get_channel_notifier()
        if notifier is None:
            return False
        return await asyncio.wait_for(
            notifier.is_linked(role, session_id),
            timeout=CHANNEL_LINK_CHECK_TIMEOUT_SECONDS,
        )
    except Exception as e:
        logger.warning(
            "Failed to check the session's channel link",
            session_id=session_id,
            error=repr(e),
        )
        # Failing open would hold every session's turn on a broken notifier.
        return False


async def channel_renders_mentions(role: Role, session_id: uuid.UUID) -> bool:
    """Whether the session's linked channel draws artifact mentions, failing closed."""
    try:
        notifier = get_channel_notifier()
    except Exception as e:
        logger.warning("Failed to load the channel notifier", error=repr(e))
        return False
    # A notifier built before `renders_mentions` existed lacks the attribute.
    if notifier is None or not getattr(notifier, "renders_mentions", False):
        return False
    return await is_channel_linked(role, session_id)


async def can_open_channel_thread(role: Role, session_id: uuid.UUID) -> bool:
    """Ask the notifier whether the session can open a thread, failing closed."""
    try:
        notifier = get_channel_notifier()
        if notifier is None:
            return False
        return await asyncio.wait_for(
            notifier.can_open_thread(role, session_id),
            timeout=CHANNEL_LINK_CHECK_TIMEOUT_SECONDS,
        )
    except Exception as e:
        logger.warning(
            "Failed to check whether the session can open a channel thread",
            session_id=session_id,
            error=repr(e),
        )
        return False


async def list_channel_destinations(
    role: Role, session_id: uuid.UUID
) -> list[ChannelDestination]:
    """Ask the notifier where the session's thread can open, failing closed."""
    try:
        notifier = get_channel_notifier()
        if notifier is None:
            return []
        async with asyncio.timeout(PUSH_BUDGET.total_seconds()):
            return await notifier.list_destinations(role, session_id)
    except Exception as e:
        logger.warning(
            "Failed to list where the session's channel thread can open",
            session_id=session_id,
            error=repr(e),
        )
        return []


async def open_channel_thread(
    role: Role,
    session_id: uuid.UUID,
    destination: ChannelDestinationRef | None = None,
) -> bool:
    """Open and link the session's thread; False when there is none or opening failed."""
    try:
        notifier = get_channel_notifier()
        if notifier is None:
            return False
        async with asyncio.timeout(PUSH_BUDGET.total_seconds()):
            return await notifier.open_thread(role, session_id, destination)
    except Exception as e:
        logger.warning(
            "Failed to open a channel thread for the session",
            session_id=session_id,
            error=repr(e),
        )
        return False


async def notify_sync_stopped(role: Role, session_id: uuid.UUID) -> None:
    """Tell the session's thread it stopped syncing; never fails the caller."""
    try:
        notifier = get_channel_notifier()
        if notifier is None:
            return
        async with asyncio.timeout(PUSH_BUDGET.total_seconds()):
            await notifier.sync_stopped(role, session_id)
    except Exception as e:
        logger.warning(
            "Failed to tell the channel thread that sync stopped",
            session_id=session_id,
            error=repr(e),
        )


def push_budget(updates: Sequence[RunUpdate]) -> timedelta:
    """What pushing these updates adds to a host activity's start_to_close."""
    return PUSH_BUDGET * len(updates)


async def push_updates(
    role: Role,
    updates: Sequence[RunUpdate],
    *,
    budget: timedelta = PUSH_BUDGET,
) -> list[str | None]:
    """Push in order, after the host's own writes; never fails the host."""
    return [await _push(role, update, budget) for update in updates]


async def _push(role: Role, update: RunUpdate, budget: timedelta) -> str | None:
    try:
        async with asyncio.timeout(budget.total_seconds()):
            notifier = get_channel_notifier()
            if notifier is None:
                # Only linked runs push, and linking needs a notifier.
                raise RuntimeError("No channel notifier is installed")
            for attempt in range(1, _PUSH_ATTEMPTS + 1):
                try:
                    return await notifier.push(role, update)
                except Exception as e:
                    definitive = isinstance(e, ApplicationError) and e.non_retryable
                    if definitive or attempt == _PUSH_ATTEMPTS:
                        raise
                    await asyncio.sleep(
                        e.retry_after.total_seconds()
                        if isinstance(e, ChannelPushRetryAfter)
                        else 2**attempt
                    )
    except Exception as e:
        logger.warning(
            "Failed to push run update",
            session_id=str(update.session_id),
            update=update.kind,
            error=repr(e),
        )
    return None


async def _reply_notifier(role: Role) -> ChannelNotifier:
    """The notifier, for an entitled organization; raises a ``ChannelReplyError``."""
    if not await _channels_entitled(role):
        raise ChannelsNotEntitled("Agent Channels is not enabled for this organization")
    if (notifier := get_channel_notifier()) is None:
        raise NoChannelBot("No channel bots are installed")
    return notifier


async def _channels_entitled(role: Role) -> bool:
    if role.organization_id is None:
        return False
    async with get_async_session_context_manager() as session:
        return await is_org_entitled(
            session, role.organization_id, Entitlement.AGENT_CHANNELS_PREVIEW
        )


async def check_channel_reply_thread(
    role: Role, preset_id: uuid.UUID, destination: ChannelDestinationRef
) -> None:
    """Raise a ``ChannelReplyError`` unless a turn of the preset can open its thread there."""
    notifier = await _reply_notifier(role)
    async with asyncio.timeout(PUSH_BUDGET.total_seconds()):
        await notifier.check_reply_thread(role, preset_id, destination)


async def open_channel_reply_thread(
    role: Role, session_id: uuid.UUID, destination: ChannelDestinationRef
) -> None:
    """Open the synced thread a finished turn carried; raises, never falls back."""
    notifier = await _reply_notifier(role)
    async with asyncio.timeout(PUSH_BUDGET.total_seconds()):
        await notifier.open_reply_thread(role, session_id, destination)
