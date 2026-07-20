"""Redis-backed queue helpers for async case duration materialization."""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from typing import Literal

from sqlalchemy.ext.asyncio import AsyncSession

from tracecat import config
from tracecat.db.session_events import add_after_commit_callback
from tracecat.logger import logger
from tracecat.redis.client import get_redis_client

CaseDurationSyncReason = Literal[
    "case_event",
    "duration_definition_created",
    "duration_definition_updated",
    "duration_definition_backfill",
]
type InlineDurationSyncFallback = Callable[[], Awaitable[bool | None]]


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

        if synced is False:
            logger.warning(
                "Inline case duration sync fallback skipped; sync lock is busy",
                workspace_id=str(workspace_id),
                case_id=str(case_id) if case_id is not None else None,
                reason=reason,
            )

    add_after_commit_callback(session, _publish)
