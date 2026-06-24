"""Consumer for async case duration materialization jobs."""

from __future__ import annotations

import asyncio
import os
import socket
import uuid
from dataclasses import dataclass
from time import monotonic
from typing import cast, get_args

from redis.exceptions import ResponseError
from sqlalchemy import or_, select
from tenacity import RetryError

from tracecat import config
from tracecat.auth.types import Role
from tracecat.authz.scopes import SERVICE_PRINCIPAL_SCOPES
from tracecat.cases.durations.service import CaseDurationService
from tracecat.cases.durations.sync_queue import (
    CaseDurationSyncReason,
    publish_case_duration_sync,
)
from tracecat.cases.enums import CaseEventType
from tracecat.db.engine import get_async_session_bypass_rls_context_manager
from tracecat.db.locks import (
    derive_lock_key_from_parts,
    try_pg_advisory_xact_lock,
)
from tracecat.db.models import Case, Workspace
from tracecat.db.models import CaseDurationDefinition as CaseDurationDefinitionDB
from tracecat.exceptions import TracecatNotFoundError
from tracecat.logger import logger
from tracecat.redis.client import RedisClient, get_redis_client

CASE_DURATION_SYNC_REASONS = frozenset(
    cast(tuple[str, ...], get_args(CaseDurationSyncReason))
)
STATUS_CHANGED_ALIASES = frozenset(
    (CaseEventType.CASE_CLOSED, CaseEventType.CASE_REOPENED)
)


@dataclass(frozen=True)
class CaseDurationSyncJob:
    workspace_id: uuid.UUID
    reason: CaseDurationSyncReason
    case_id: uuid.UUID | None = None
    event_type: str | None = None
    cursor: int | None = None


class CaseDurationSyncConsumer:
    """Consume and coalesce case duration sync jobs."""

    def __init__(
        self, client: RedisClient, *, consumer_name: str | None = None
    ) -> None:
        self.client = client
        self.stream_key = config.TRACECAT__CASE_DURATION_SYNC_STREAM_KEY
        self.group = config.TRACECAT__CASE_DURATION_SYNC_GROUP
        self.block_ms = config.TRACECAT__CASE_DURATION_SYNC_BLOCK_MS
        self.batch = config.TRACECAT__CASE_DURATION_SYNC_BATCH
        self.claim_idle_ms = config.TRACECAT__CASE_DURATION_SYNC_CLAIM_IDLE_MS
        self.backfill_batch = config.TRACECAT__CASE_DURATION_SYNC_BACKFILL_BATCH
        self.consumer_name = consumer_name or f"{socket.gethostname()}:{os.getpid()}"
        self._pending_check_interval = max(self.claim_idle_ms / 1000.0, 30.0)

    async def run(self) -> None:
        if not config.TRACECAT__CASE_DURATION_SYNC_ENABLED:
            logger.info("Case duration sync disabled; skipping consumer")
            return

        await self._ensure_group()
        logger.info(
            "Case duration sync consumer started",
            stream_key=self.stream_key,
            group=self.group,
            consumer=self.consumer_name,
        )
        last_pending_check = monotonic()
        try:
            while True:
                try:
                    messages = await self.client.xreadgroup(
                        group_name=self.group,
                        consumer_name=self.consumer_name,
                        streams={self.stream_key: ">"},
                        count=self.batch,
                        block=self.block_ms,
                    )
                except (ResponseError, RetryError) as e:
                    if self._is_nogroup_error(e):
                        logger.warning(
                            "Redis case duration sync stream/group missing; recreating",
                            stream_key=self.stream_key,
                            group=self.group,
                            error=str(e),
                        )
                        await self._ensure_group()
                        continue
                    raise
                if messages:
                    for _stream, entries in messages:
                        await self._handle_entries(entries)
                now = monotonic()
                if now - last_pending_check >= self._pending_check_interval:
                    await self._claim_idle_messages()
                    last_pending_check = now
                await asyncio.sleep(0)
        except asyncio.CancelledError:
            logger.info("Case duration sync consumer cancelled")
            raise
        except Exception as e:
            logger.error(
                "Case duration sync consumer stopped due to error", error=str(e)
            )
            raise

    def _is_nogroup_error(self, error: Exception) -> bool:
        if isinstance(error, ResponseError):
            return "NOGROUP" in str(error)
        if isinstance(error, RetryError):
            last_exc = error.last_attempt.exception()
            return isinstance(last_exc, ResponseError) and "NOGROUP" in str(last_exc)
        return False

    async def _ensure_group(self) -> None:
        try:
            # Read from the start of the stream ("0") rather than only new
            # messages ("$"). The consumer is started as an unawaited background
            # task, so jobs can be published before the group exists; "0" also
            # lets the group reclaim retained jobs after a NOGROUP recovery.
            await self.client.xgroup_create(
                self.stream_key,
                self.group,
                id="0",
                ignore_busygroup=True,
            )
        except ResponseError as e:
            if "BUSYGROUP" in str(e):
                return
            raise

    async def _handle_entries(self, entries: list[tuple[str, dict[str, str]]]) -> None:
        case_jobs: dict[tuple[uuid.UUID, uuid.UUID], list[str]] = {}
        case_event_types: dict[tuple[uuid.UUID, uuid.UUID], set[str]] = {}
        force_sync_keys: set[tuple[uuid.UUID, uuid.UUID]] = set()
        for message_id, fields in entries:
            job = self._parse_job(fields)
            if job is None:
                await self.client.xack(self.stream_key, self.group, [message_id])
                continue

            if job.case_id is None:
                try:
                    should_ack = await self._process_backfill_job(job)
                except Exception:
                    logger.exception(
                        "Failed to process case duration backfill job",
                        workspace_id=str(job.workspace_id),
                        reason=job.reason,
                    )
                    should_ack = False
                if should_ack:
                    await self.client.xack(self.stream_key, self.group, [message_id])
                continue

            key = (job.workspace_id, job.case_id)
            case_jobs.setdefault(key, []).append(message_id)
            if job.event_type:
                case_event_types.setdefault(key, set()).add(job.event_type)
            else:
                # A case-scoped job without an event type (e.g. a backfill job)
                # means "sync unconditionally". Record the key so a coalesced,
                # non-matching event type cannot make the event-type filter skip
                # and ack it.
                force_sync_keys.add(key)

        for (workspace_id, case_id), message_ids in case_jobs.items():
            key = (workspace_id, case_id)
            event_types = None if key in force_sync_keys else case_event_types.get(key)
            try:
                synced = await self._sync_case_duration(
                    workspace_id,
                    case_id,
                    event_types=event_types,
                )
            except Exception:
                logger.exception(
                    "Failed to process case duration sync job",
                    workspace_id=str(workspace_id),
                    case_id=str(case_id),
                )
                continue

            if synced:
                await self.client.xack(self.stream_key, self.group, message_ids)

    def _parse_job(self, fields: dict[str, str]) -> CaseDurationSyncJob | None:
        workspace_id = fields.get("workspace_id")
        reason = fields.get("reason")
        if not (workspace_id and reason):
            logger.warning("Malformed case duration sync message", fields=fields)
            return None
        if reason not in CASE_DURATION_SYNC_REASONS:
            logger.warning("Unknown case duration sync reason", fields=fields)
            return None
        try:
            return CaseDurationSyncJob(
                workspace_id=uuid.UUID(workspace_id),
                case_id=uuid.UUID(case_id)
                if (case_id := fields.get("case_id"))
                else None,
                event_type=fields.get("event_type"),
                reason=cast(CaseDurationSyncReason, reason),
                cursor=int(cursor) if (cursor := fields.get("cursor")) else None,
            )
        except (TypeError, ValueError):
            logger.warning("Invalid IDs in case duration sync message", fields=fields)
            return None

    async def _sync_case_duration(
        self,
        workspace_id: uuid.UUID,
        case_id: uuid.UUID,
        *,
        event_types: set[str] | None = None,
    ) -> bool:
        lock_key = derive_lock_key_from_parts(
            "case-duration-sync",
            str(workspace_id),
            str(case_id),
        )
        async with get_async_session_bypass_rls_context_manager() as session:
            role = await self._get_service_role(session, workspace_id)
            if role is None:
                return True

            if not await self._event_types_require_sync(
                session,
                workspace_id=workspace_id,
                event_types=event_types or set(),
            ):
                logger.debug(
                    "Skipping case duration sync; no definitions use event types",
                    workspace_id=str(workspace_id),
                    case_id=str(case_id),
                    event_types=sorted(event_types or set()),
                )
                return True

            locked = await try_pg_advisory_xact_lock(session, lock_key)
            if not locked:
                await session.rollback()
                logger.debug(
                    "Case duration sync already locked; leaving message pending",
                    workspace_id=str(workspace_id),
                    case_id=str(case_id),
                )
                return False

            try:
                await CaseDurationService(
                    session=session, role=role
                ).sync_case_durations(case_id)
                await session.commit()
                return True
            except TracecatNotFoundError:
                await session.rollback()
                logger.info(
                    "Skipping case duration sync for deleted case",
                    workspace_id=str(workspace_id),
                    case_id=str(case_id),
                )
                return True
            except Exception:
                await session.rollback()
                logger.exception(
                    "Failed to sync case durations",
                    workspace_id=str(workspace_id),
                    case_id=str(case_id),
                )
                return False

    async def _event_types_require_sync(
        self,
        session,
        *,
        workspace_id: uuid.UUID,
        event_types: set[str],
    ) -> bool:
        if not event_types:
            return True

        parsed_event_types: list[CaseEventType] = []
        for event_type in event_types:
            try:
                parsed_event_types.append(CaseEventType(event_type))
            except ValueError:
                logger.warning(
                    "Unknown case event type in duration sync job",
                    workspace_id=str(workspace_id),
                    event_type=event_type,
                )
                return True

        matching_event_types = list(parsed_event_types)
        if (
            any(
                event_type in STATUS_CHANGED_ALIASES
                for event_type in parsed_event_types
            )
            and CaseEventType.STATUS_CHANGED not in matching_event_types
        ):
            matching_event_types.append(CaseEventType.STATUS_CHANGED)

        stmt = (
            select(CaseDurationDefinitionDB.id)
            .where(
                CaseDurationDefinitionDB.workspace_id == workspace_id,
                or_(
                    CaseDurationDefinitionDB.start_event_type.in_(matching_event_types),
                    CaseDurationDefinitionDB.end_event_type.in_(matching_event_types),
                ),
            )
            .limit(1)
        )
        result = await session.execute(stmt)
        return result.scalar_one_or_none() is not None

    async def _process_backfill_job(self, job: CaseDurationSyncJob) -> bool:
        async with get_async_session_bypass_rls_context_manager() as session:
            stmt = (
                select(Case.surrogate_id, Case.id)
                .where(Case.workspace_id == job.workspace_id)
                .order_by(Case.surrogate_id.asc())
                .limit(self.backfill_batch)
            )
            if job.cursor is not None:
                stmt = stmt.where(Case.surrogate_id > job.cursor)
            result = await session.execute(stmt)
            case_rows = result.tuples().all()

        for _surrogate_id, case_id in case_rows:
            await publish_case_duration_sync(
                workspace_id=job.workspace_id,
                case_id=case_id,
                reason="duration_definition_backfill",
            )

        if len(case_rows) == self.backfill_batch:
            next_cursor = case_rows[-1][0]
            await publish_case_duration_sync(
                workspace_id=job.workspace_id,
                reason="duration_definition_backfill",
                cursor=next_cursor,
            )
        return True

    async def _get_service_role(self, session, workspace_id: uuid.UUID) -> Role | None:
        result = await session.execute(
            select(Workspace).where(Workspace.id == workspace_id)
        )
        workspace = result.scalars().first()
        if workspace is None:
            logger.info(
                "Skipping case duration sync for deleted workspace",
                workspace_id=str(workspace_id),
            )
            return None
        return Role(
            type="service",
            workspace_id=workspace_id,
            organization_id=workspace.organization_id,
            user_id=None,
            service_id="tracecat-case-duration-sync",
            scopes=SERVICE_PRINCIPAL_SCOPES["tracecat-case-duration-sync"],
        )

    async def _claim_idle_messages(self) -> None:
        pending = await self.client.xpending_range(
            self.stream_key,
            self.group,
            min_id="-",
            max_id="+",
            count=self.batch,
            idle=self.claim_idle_ms,
        )
        if not pending:
            return

        message_ids: list[str] = []
        for entry in pending:
            msg_id = None
            if isinstance(entry, dict):
                msg_id = entry.get("message_id") or entry.get("id")
            else:
                msg_id = getattr(entry, "message_id", None)
            if msg_id:
                message_ids.append(msg_id)

        if not message_ids:
            return

        claimed = await self.client.xclaim(
            self.stream_key,
            self.group,
            self.consumer_name,
            self.claim_idle_ms,
            message_ids,
        )
        await self._handle_entries(claimed)


async def start_case_duration_sync_consumer() -> None:
    client = await get_redis_client()
    consumer = CaseDurationSyncConsumer(client)
    await consumer.run()
