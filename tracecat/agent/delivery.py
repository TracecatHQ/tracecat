"""Channel delivery: notices, the delivery port, and its activity.

Agent workflows send a thin notice of what happened, by id. An installed
channel extension reads the turn through open-source services, then renders
and sends it. Notices are stored in Temporal history: evolve additively with
defaults, and roll a new field out to workers before workflows send it, since
unknown fields stay rejected.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import timedelta
from functools import lru_cache
from importlib.metadata import entry_points
from typing import Annotated, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field
from temporalio import activity
from temporalio.common import RetryPolicy
from temporalio.exceptions import ApplicationError

from tracecat.auth.types import Role
from tracecat.contexts import ctx_role
from tracecat.core.schemas import Variant
from tracecat.logger import logger

CHANNEL_DELIVERY_ENTRY_POINT_GROUP = "tracecat.agent_channel_delivery"

# Five attempts, exponential from 2 s, 60 s in total; then the caller moves on.
CHANNEL_DELIVERY_RETRY_POLICY = RetryPolicy(
    initial_interval=timedelta(seconds=2),
    backoff_coefficient=2.0,
    maximum_attempts=5,
)
CHANNEL_DELIVERY_SCHEDULE_TO_CLOSE_TIMEOUT = timedelta(seconds=60)
# Provider request timeouts must stay below this so attempts never overlap.
CHANNEL_DELIVERY_START_TO_CLOSE_TIMEOUT = timedelta(seconds=20)
# Bounded well inside create_session_activity's 30 s budget.
LINKED_THREAD_HINT_TIMEOUT_SECONDS = 5


class ChannelNoticeBase(Variant, discriminator="kind"):
    model_config = ConfigDict(frozen=True, extra="forbid")

    session_id: uuid.UUID


class TurnFinished(ChannelNoticeBase):
    """A run ended, whatever its outcome.

    Sent before the run releases the session: read its rows with
    ``include_active=True``.
    """

    kind: Literal["turn_finished"] = "turn_finished"
    run_id: uuid.UUID


def approval_batch_id(run_id: uuid.UUID, tool_call_ids: list[str]) -> uuid.UUID:
    """Identify one approval pause from the paused run and its pending tool calls."""
    # Prefixed so it never equals the continuation stream id derived from the same run.
    return uuid.uuid5(run_id, f"approval-batch:{','.join(sorted(tool_call_ids))}")


class ApprovalPending(ChannelNoticeBase):
    """A run paused on a batch of tool approvals."""

    kind: Literal["approval_pending"] = "approval_pending"
    run_id: uuid.UUID
    batch_id: uuid.UUID


class ApprovalDecided(ChannelNoticeBase):
    """Every approval in a batch is decided; edit the card posted for it."""

    kind: Literal["approval_decided"] = "approval_decided"
    batch_id: uuid.UUID
    # None when ApprovalPending returned no ref: find the card by batch_id.
    previous_ref: str | None


ChannelNotice = Annotated[
    TurnFinished | ApprovalPending | ApprovalDecided, Field(discriminator="kind")
]


class DeliverChannelNoticeInput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    role: Role
    notice: ChannelNotice


class ChannelNoticeResult(BaseModel):
    # Decoded in workflow code: a newer worker's extra field must not wedge it.
    model_config = ConfigDict(frozen=True, extra="ignore")

    # Opaque provider message id; None when nothing was posted.
    ref: str | None = None


class ChannelDeliveryRetryAfter(Exception):
    """Raised by a port when the provider asks the caller to wait."""

    def __init__(self, retry_after: timedelta) -> None:
        super().__init__(f"Channel provider asked to retry after {retry_after}")
        self.retry_after = retry_after


class ChannelDeliveryPort(Protocol):
    """Delivers notices to a session's linked thread.

    Raise ``ChannelDeliveryRetryAfter`` to honor a provider's Retry-After, and
    a non-retryable ``ApplicationError`` for a definitive rejection. The
    constructor runs once on the worker loop; methods must not block it.
    """

    async def has_linked_thread(self, role: Role, session_id: uuid.UUID) -> bool:
        """Return whether the session has a thread to deliver to."""
        ...

    async def deliver(self, role: Role, notice: ChannelNotice) -> str | None:
        """Deliver the notice and return the posted message's ref, if any."""
        ...


class NoopChannelDeliveryPort:
    """Default port used when no channel extension is installed."""

    async def has_linked_thread(self, role: Role, session_id: uuid.UUID) -> bool:
        del role, session_id
        return False

    async def deliver(self, role: Role, notice: ChannelNotice) -> str | None:
        del role, notice
        return None


@lru_cache(maxsize=1)
def get_channel_delivery_port() -> ChannelDeliveryPort:
    """Build the installed port from its entry point, else the no-op port.

    The entry point names a zero-argument callable, such as the port class.
    """
    installed = list(entry_points(group=CHANNEL_DELIVERY_ENTRY_POINT_GROUP))
    if len(installed) > 1:
        names = ", ".join(sorted(entry_point.name for entry_point in installed))
        raise RuntimeError(f"Multiple channel delivery ports installed: {names}")
    if installed:
        return installed[0].load()()
    return NoopChannelDeliveryPort()


async def linked_thread_hint(role: Role, session_id: uuid.UUID) -> bool:
    """Ask the port whether to deliver for a session, failing closed."""
    try:
        return await asyncio.wait_for(
            get_channel_delivery_port().has_linked_thread(role, session_id),
            timeout=LINKED_THREAD_HINT_TIMEOUT_SECONDS,
        )
    except Exception as e:
        logger.warning(
            "Failed to check the session's linked channel thread",
            session_id=session_id,
            error=repr(e),
        )
        # Failing open would hold every session's turn on a broken port.
        return False


@activity.defn
async def deliver_channel_notice(
    input: DeliverChannelNoticeInput,
) -> ChannelNoticeResult:
    """Forward a notice to the installed delivery port."""
    ctx_role.set(input.role)
    try:
        port = get_channel_delivery_port()
    except Exception as e:
        # A broken install fails the same way on every attempt.
        raise ApplicationError(
            f"Channel delivery port unavailable: {e}",
            type="ChannelDeliveryPortUnavailable",
            non_retryable=True,
        ) from e
    try:
        ref = await port.deliver(input.role, input.notice)
    except ChannelDeliveryRetryAfter as e:
        raise ApplicationError(
            str(e),
            type=ChannelDeliveryRetryAfter.__name__,
            next_retry_delay=e.retry_after,
        ) from e
    return ChannelNoticeResult(ref=ref)
