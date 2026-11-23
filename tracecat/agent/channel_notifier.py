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

from pydantic import ConfigDict, Field
from temporalio.exceptions import ApplicationError

from tracecat.auth.types import Role
from tracecat.core.schemas import Variant
from tracecat.logger import logger

CHANNEL_NOTIFIER_ENTRY_POINT_GROUP = "tracecat.agent_channel_notifier"

# Per update, retries included; a host activity's start_to_close grows by this.
PUSH_BUDGET = timedelta(seconds=20)
# A status line is not worth holding the turn's start for.
TURN_STARTED_PUSH_BUDGET = timedelta(seconds=5)
_PUSH_ATTEMPTS = 3
# Bounded well inside create_session_activity's 30 s budget.
CHANNEL_LINK_CHECK_TIMEOUT_SECONDS = 5


class RunUpdateBase(Variant, discriminator="kind"):
    model_config = ConfigDict(frozen=True, extra="forbid")

    session_id: uuid.UUID


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
    TurnStarted | TurnFinished | ApprovalPending | ApprovalDecided,
    Field(discriminator="kind"),
]


class ChannelPushRetryAfter(Exception):
    """Raised by a notifier when the provider asks the caller to wait."""

    def __init__(self, retry_after: timedelta) -> None:
        super().__init__(f"Channel provider asked to retry after {retry_after}")
        self.retry_after = retry_after


class ChannelNotifier(Protocol):
    """Posts run updates to the channel a session is linked to.

    Raise ``ChannelPushRetryAfter`` to honor a provider's Retry-After, and
    a non-retryable ``ApplicationError`` for a definitive rejection. The
    constructor runs once on the worker loop; methods must not block it.
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
