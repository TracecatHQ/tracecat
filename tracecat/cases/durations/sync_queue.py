"""Redis-backed queue helpers for async case duration materialization."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from typing import Literal

from sqlalchemy.ext.asyncio import AsyncSession

from tracecat import config
from tracecat.db.session_events import AfterCommitQueue
from tracecat.logger import logger
from tracecat.redis.client import get_redis_client

CaseDurationSyncReason = Literal[
    "case_event",
    "duration_definition_created",
    "duration_definition_updated",
    "duration_definition_backfill",
]
type InlineDurationSyncFallback = Callable[[], Awaitable[bool | None]]

# Delay before each inline fallback attempt when the case's sync advisory lock
# is held by another transaction. Bounded so a degraded (Redis-down) deployment
# never accumulates long-lived waiters; a sync that stays locked out past the
# last attempt heals on the next case event or definition backfill.
INLINE_FALLBACK_ATTEMPT_DELAYS_SECONDS: tuple[float, ...] = (0.0, 0.5, 2.0, 5.0)


async def publish_case_duration_sync(
    *,
    workspace_id: uuid.UUID,
    reason: CaseDurationSyncReason,
    case_id: uuid.UUID | None = None,
    event_type: str | None = None,
    cursor: int | None = None,
) -> str | None:
    """Publish a case duration sync job to Redis."""
    if not config.TRACECAT__CASE_DURATION_SYNC_ENABLED:
        return None

    fields = {
        "workspace_id": str(workspace_id),
        "reason": reason,
    }
    if case_id is not None:
        fields["case_id"] = str(case_id)
    if event_type is not None:
        fields["event_type"] = event_type
    if cursor is not None:
        fields["cursor"] = str(cursor)

    client = await get_redis_client()
    message_id = await client.xadd(
        stream_key=config.TRACECAT__CASE_DURATION_SYNC_STREAM_KEY,
        fields=fields,
        maxlen=config.TRACECAT__CASE_DURATION_SYNC_MAXLEN,
        approximate=True,
        expire_seconds=None,
    )
    logger.debug(
        "Queued case duration sync",
        message_id=message_id,
        workspace_id=str(workspace_id),
        case_id=str(case_id) if case_id is not None else None,
        reason=reason,
    )
    return message_id


def enqueue_case_duration_sync_after_commit(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    reason: CaseDurationSyncReason,
    case_id: uuid.UUID | None = None,
    event_type: str | None = None,
    cursor: int | None = None,
    inline_fallback: InlineDurationSyncFallback,
) -> None:
    """Register a duration sync publish after the current transaction commits."""

    async def _publish() -> None:
        try:
            message_id = await publish_case_duration_sync(
                workspace_id=workspace_id,
                case_id=case_id,
                event_type=event_type,
                reason=reason,
                cursor=cursor,
            )
            if message_id is not None:
                return
            logger.warning(
                "Case duration sync publish skipped; falling back inline",
                workspace_id=str(workspace_id),
                case_id=str(case_id) if case_id is not None else None,
                reason=reason,
            )
        except Exception as e:
            logger.warning(
                "Failed to publish case duration sync; falling back inline",
                workspace_id=str(workspace_id),
                case_id=str(case_id) if case_id is not None else None,
                reason=reason,
                error=str(e),
            )

        # Preserve duration materialization durability when Redis is unavailable.
        # Retry briefly when another transaction holds the case's sync lock: the
        # lock holder may have computed before this commit became visible, and
        # with Redis down there is no queued job to redo the work.
        for attempt, delay_seconds in enumerate(
            INLINE_FALLBACK_ATTEMPT_DELAYS_SECONDS, start=1
        ):
            if delay_seconds:
                await asyncio.sleep(delay_seconds)
            try:
                synced = await inline_fallback()
            except Exception:
                logger.exception(
                    "Inline case duration sync fallback failed",
                    workspace_id=str(workspace_id),
                    case_id=str(case_id) if case_id is not None else None,
                    reason=reason,
                )
                return
            if synced is not False:
                return
            logger.debug(
                "Inline case duration sync fallback lock busy; retrying",
                workspace_id=str(workspace_id),
                case_id=str(case_id) if case_id is not None else None,
                reason=reason,
                attempt=attempt,
            )

        logger.warning(
            "Inline case duration sync fallback skipped; sync lock stayed busy",
            workspace_id=str(workspace_id),
            case_id=str(case_id) if case_id is not None else None,
            reason=reason,
            attempts=len(INLINE_FALLBACK_ATTEMPT_DELAYS_SECONDS),
        )

    AfterCommitQueue.of(session).add(_publish)
