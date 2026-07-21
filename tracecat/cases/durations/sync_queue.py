"""Redis-backed queue helpers for async case duration materialization."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Final, Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat import config
from tracecat.db.engine import get_async_session_bypass_rls_context_manager
from tracecat.db.models import CaseDurationDefinition
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
) -> str:
    """Publish a case duration sync job to Redis."""
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


ROLLOUT_BACKFILL_MARKER_KEY: Final = "case-duration-sync:rollout-backfill:v1"
# Lease TTL covering one enqueue pass. A boot that dies mid-pass (crash or a
# cancelled lifespan task) simply lets the lease lapse, so a later boot
# retries instead of permanently skipping the rollout.
ROLLOUT_BACKFILL_LEASE_SECONDS: Final = 600


async def enqueue_rollout_backfill_once() -> None:
    """Queue a one-time backfill for workspaces with existing definitions.

    Deployments that predate async duration sync materialized duration rows
    on read; that read-time sync is gone, so cases without a subsequent event
    would otherwise stay unmaterialized forever. A short-lived Redis lease
    (``SET NX EX``) elects one replica; the marker is made permanent only
    after every workspace job is queued, so an interrupted pass is retried by
    a later boot. Re-runs are idempotent.
    """
    client = await get_redis_client()
    acquired = await client.set_if_not_exists(
        ROLLOUT_BACKFILL_MARKER_KEY,
        "pending",
        expire_seconds=ROLLOUT_BACKFILL_LEASE_SECONDS,
    )
    if not acquired:
        return

    try:
        async with get_async_session_bypass_rls_context_manager() as session:
            result = await session.execute(
                select(CaseDurationDefinition.workspace_id).distinct()
            )
            workspace_ids = list(result.scalars().all())
        for workspace_id in workspace_ids:
            await publish_case_duration_sync(
                workspace_id=workspace_id,
                reason="duration_definition_updated",
            )
    except Exception:
        # Release the lease so the next boot retries immediately rather than
        # waiting out the TTL. CancelledError deliberately bypasses this: the
        # lease then lapses on its own.
        try:
            await client.delete(ROLLOUT_BACKFILL_MARKER_KEY)
        except Exception:
            logger.warning(
                "Failed to release rollout duration backfill lease",
                key=ROLLOUT_BACKFILL_MARKER_KEY,
            )
        raise

    # All jobs are queued; overwrite the lease with a permanent marker (SET
    # without an expiry clears the TTL).
    await client.set(
        ROLLOUT_BACKFILL_MARKER_KEY,
        datetime.now(UTC).isoformat(),
        expire_seconds=None,
    )
    logger.info(
        "Queued rollout duration backfill",
        workspace_count=len(workspace_ids),
    )


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
            await publish_case_duration_sync(
                workspace_id=workspace_id,
                case_id=case_id,
                event_type=event_type,
                reason=reason,
                cursor=cursor,
            )
            return
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
        attempts = len(INLINE_FALLBACK_ATTEMPT_DELAYS_SECONDS)
        for attempt, delay_seconds in enumerate(
            INLINE_FALLBACK_ATTEMPT_DELAYS_SECONDS, start=1
        ):
            if delay_seconds:
                await asyncio.sleep(delay_seconds)
            try:
                synced = await inline_fallback()
            except Exception:
                # Transient failures (e.g. a pool timeout) get the same
                # bounded retries as lock contention: with Redis down there
                # is no queued job left to redo this work.
                if attempt == attempts:
                    logger.exception(
                        "Inline case duration sync fallback failed; giving up",
                        workspace_id=str(workspace_id),
                        case_id=str(case_id) if case_id is not None else None,
                        reason=reason,
                        attempts=attempts,
                    )
                    return
                logger.warning(
                    "Inline case duration sync fallback errored; retrying",
                    workspace_id=str(workspace_id),
                    case_id=str(case_id) if case_id is not None else None,
                    reason=reason,
                    attempt=attempt,
                )
                continue
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
            attempts=attempts,
        )

    AfterCommitQueue.of(session).add(_publish)
