"""Run updates and the channel publisher that posts them to a linked channel.

Agent workflows hand a thin update of what happened, by id, to the lifecycle
activity that makes it true; that activity publishes it after its own writes.
An installed channel publisher reads the turn through open-source services,
then renders and posts it. Updates are stored in Temporal history: evolve
additively with defaults, and roll a new field out to workers before workflows
send it, since unknown fields stay rejected.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import timedelta
from functools import lru_cache
from importlib.metadata import entry_points
from typing import Annotated, Literal, Protocol

from pydantic import ConfigDict, Field
from temporalio.exceptions import ApplicationError

from tracecat.auth.types import Role
from tracecat.core.schemas import Variant
from tracecat.logger import logger

CHANNEL_PUBLISHER_ENTRY_POINT_GROUP = "tracecat.agent_channel_publisher"

# Per update, retries included; a host activity's start_to_close grows by this.
PUBLISH_BUDGET = timedelta(seconds=20)
_PUBLISH_ATTEMPTS = 3
# Bounded well inside create_session_activity's 30 s budget.
CHANNEL_LINK_CHECK_TIMEOUT_SECONDS = 5


class RunUpdateBase(Variant, discriminator="kind"):
    model_config = ConfigDict(frozen=True, extra="forbid")

    session_id: uuid.UUID


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
    TurnFinished | ApprovalPending | ApprovalDecided, Field(discriminator="kind")
]


class ChannelPublishRetryAfter(Exception):
    """Raised by a publisher when the provider asks the caller to wait."""

    def __init__(self, retry_after: timedelta) -> None:
        super().__init__(f"Channel provider asked to retry after {retry_after}")
        self.retry_after = retry_after


class ChannelPublisher(Protocol):
    """Posts run updates to the channel a session is linked to.

    Raise ``ChannelPublishRetryAfter`` to honor a provider's Retry-After, and
    a non-retryable ``ApplicationError`` for a definitive rejection. The
    constructor runs once on the worker loop; methods must not block it.
    Publishing must be idempotent per update: a retried host activity resends.
    """

    async def is_linked(self, role: Role, session_id: uuid.UUID) -> bool:
        """Return whether the session is linked to a channel."""
        ...

    async def publish(self, role: Role, update: RunUpdate) -> str | None:
        """Post the update and return the posted message's ref, if any."""
        ...


@lru_cache(maxsize=1)
def get_channel_publisher() -> ChannelPublisher | None:
    """Build the installed publisher from its entry point; None when there is none.

    The entry point names a zero-argument callable, such as the publisher class.
    """
    installed = list(entry_points(group=CHANNEL_PUBLISHER_ENTRY_POINT_GROUP))
    if len(installed) > 1:
        names = ", ".join(sorted(entry_point.name for entry_point in installed))
        raise RuntimeError(f"Multiple channel publishers installed: {names}")
    return installed[0].load()() if installed else None


async def is_channel_linked(role: Role, session_id: uuid.UUID) -> bool:
    """Ask the publisher whether a session is linked, failing closed."""
    try:
        publisher = get_channel_publisher()
        if publisher is None:
            return False
        return await asyncio.wait_for(
            publisher.is_linked(role, session_id),
            timeout=CHANNEL_LINK_CHECK_TIMEOUT_SECONDS,
        )
    except Exception as e:
        logger.warning(
            "Failed to check the session's channel link",
            session_id=session_id,
            error=repr(e),
        )
        # Failing open would hold every session's turn on a broken publisher.
        return False


def publish_budget(updates: Sequence[RunUpdate]) -> timedelta:
    """What publishing these updates adds to a host activity's start_to_close."""
    return PUBLISH_BUDGET * len(updates)


async def publish_updates(role: Role, updates: Sequence[RunUpdate]) -> list[str | None]:
    """Publish in order, after the host's own writes; never fails the host."""
    return [(await _publish(role, update)).ref for update in updates]


async def publish_all(role: Role, updates: Sequence[RunUpdate]) -> bool:
    """Publish in order like ``publish_updates``; return whether every one went out."""
    return all([(await _publish(role, update)).sent for update in updates])


@dataclass(frozen=True, slots=True)
class _PublishResult:
    """Whether the update went out, and the posted message's ref (None when the
    publisher returned none, even if sent)."""

    sent: bool
    ref: str | None


async def _publish(role: Role, update: RunUpdate) -> _PublishResult:
    try:
        async with asyncio.timeout(PUBLISH_BUDGET.total_seconds()):
            publisher = get_channel_publisher()
            if publisher is None:
                # Only linked runs publish, and linking needs a publisher.
                raise RuntimeError("No channel publisher is installed")
            for attempt in range(1, _PUBLISH_ATTEMPTS + 1):
                try:
                    return _PublishResult(
                        sent=True, ref=await publisher.publish(role, update)
                    )
                except Exception as e:
                    definitive = isinstance(e, ApplicationError) and e.non_retryable
                    if definitive or attempt == _PUBLISH_ATTEMPTS:
                        raise
                    await asyncio.sleep(
                        e.retry_after.total_seconds()
                        if isinstance(e, ChannelPublishRetryAfter)
                        else 2**attempt
                    )
    except Exception as e:
        logger.warning(
            "Failed to publish run update",
            session_id=str(update.session_id),
            update=update.kind,
            error=repr(e),
        )
    return _PublishResult(sent=False, ref=None)
