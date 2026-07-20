"""Consumer for async case duration materialization jobs."""

from __future__ import annotations

import asyncio
import os
import socket
import uuid
from time import monotonic
from typing import Annotated, Final, Literal, NamedTuple, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    ValidationError,
    model_validator,
)
from redis.exceptions import ResponseError
from sqlalchemy import select
from tenacity import RetryError

from tracecat import config
from tracecat.cases.durations.materialization import sync_case_duration
from tracecat.cases.durations.sync_queue import (
    publish_case_duration_sync,
)
from tracecat.db.engine import get_async_session_bypass_rls_context_manager
from tracecat.db.models import Case
from tracecat.logger import logger
from tracecat.redis.client import RedisClient, get_redis_client

RETRY_BACKOFF_BASE_SECONDS: Final = 1.0
RETRY_BACKOFF_MAX_SECONDS: Final = 30.0


class CaseKey(NamedTuple):
    """Key used to coalesce case-scoped jobs."""

    workspace_id: uuid.UUID
    case_id: uuid.UUID


class _SyncJobBase(BaseModel):
    """Fields marked `None` are forbidden for that reason and reject non-null
    values, while unknown extra fields stay ignored (Pydantic's default)."""

    model_config = ConfigDict(frozen=True)

    workspace_id: uuid.UUID


class CaseEventSyncJob(_SyncJobBase):
    reason: Literal["case_event"]
    case_id: uuid.UUID
    event_type: str = Field(min_length=1)
    cursor: None = None


class DurationDefinitionSyncJob(_SyncJobBase):
    reason: Literal["duration_definition_created", "duration_definition_updated"]
    case_id: None = None
    event_type: None = None
    cursor: None = None


class DurationBackfillSyncJob(_SyncJobBase):
    reason: Literal["duration_definition_backfill"]
    case_id: uuid.UUID | None = None
    event_type: None = None
    cursor: int | None = None

    @model_validator(mode="after")
    def _validate_scope(self) -> Self:
        if (self.case_id is None) == (self.cursor is None):
            raise ValueError("exactly one of case_id or cursor must be set")
        return self


type CaseDurationSyncJob = Annotated[
    CaseEventSyncJob | DurationDefinitionSyncJob | DurationBackfillSyncJob,
    Field(discriminator="reason"),
]

_JOB_VALIDATOR: TypeAdapter[CaseDurationSyncJob] = TypeAdapter(CaseDurationSyncJob)


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

        last_pending_check = monotonic()
        retry_delay = RETRY_BACKOFF_BASE_SECONDS
        group_ready = False
        started = False
        while True:
            try:
                if not group_ready:
                    await self._ensure_group()
                    group_ready = True
                if not started:
                    logger.info(
                        "Case duration sync consumer started",
                        stream_key=self.stream_key,
                        group=self.group,
                        consumer=self.consumer_name,
                    )
                    started = True
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
                retry_delay = RETRY_BACKOFF_BASE_SECONDS
            except asyncio.CancelledError:
                logger.info("Case duration sync consumer cancelled")
                raise
            except Exception as e:
                logger.warning(
                    "Case duration sync consumer iteration failed; retrying",
                    error=str(e),
                    retry_in_seconds=retry_delay,
                )
                await asyncio.sleep(retry_delay)
                retry_delay = min(retry_delay * 2, RETRY_BACKOFF_MAX_SECONDS)

    def _is_nogroup_error(self, error: Exception) -> bool:
        if isinstance(error, ResponseError):
            return "NOGROUP" in str(error)
        if isinstance(error, RetryError):
            last_exc = error.last_attempt.exception()
            return isinstance(last_exc, ResponseError) and "NOGROUP" in str(last_exc)
        return False

    async def _ensure_group(self) -> None:
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

    async def _handle_entries(self, entries: list[tuple[str, dict[str, str]]]) -> None:
        case_jobs: dict[CaseKey, list[str]] = {}
        case_event_types: dict[CaseKey, set[str]] = {}
        force_sync_keys: set[CaseKey] = set()
        for message_id, fields in entries:
            job = self._parse_job(fields)
            if job is None:
                await self._ack_and_delete([message_id])
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
                    await self._ack_and_delete([message_id])
                continue

            key = CaseKey(workspace_id=job.workspace_id, case_id=job.case_id)
            case_jobs.setdefault(key, []).append(message_id)
            if job.event_type:
                case_event_types.setdefault(key, set()).add(job.event_type)
            else:
                # A case-scoped job without an event type (e.g. a backfill job)
                # means "sync unconditionally". Record the key so a coalesced,
                # non-matching event type cannot make the event-type filter skip
                # and ack it.
                force_sync_keys.add(key)

        for key, message_ids in case_jobs.items():
            event_types = None if key in force_sync_keys else case_event_types.get(key)
            try:
                synced = await self._sync_case_duration(
                    key.workspace_id,
                    key.case_id,
                    event_types=event_types,
                )
            except Exception:
                logger.exception(
                    "Failed to process case duration sync job",
                    workspace_id=str(key.workspace_id),
                    case_id=str(key.case_id),
                )
                continue

            if synced:
                await self._ack_and_delete(message_ids)

    async def _ack_and_delete(self, message_ids: list[str]) -> None:
        await self.client.xack(self.stream_key, self.group, message_ids)
        try:
            await self.client.xdel(self.stream_key, message_ids)
        except Exception as e:
            # The jobs are already acknowledged. Cleanup failures must not make
            # successfully processed work look pending or fail the consumer loop.
            logger.warning(
                "Failed to delete acknowledged case duration sync messages",
                stream_key=self.stream_key,
                message_ids=message_ids,
                error=str(e),
            )

    def _parse_job(self, fields: dict[str, str]) -> CaseDurationSyncJob | None:
        try:
            return _JOB_VALIDATOR.validate_python(fields)
        except ValidationError as e:
            logger.warning(
                "Invalid case duration sync message",
                fields=fields,
                error=str(e),
            )
            return None

    async def _sync_case_duration(
        self,
        workspace_id: uuid.UUID,
        case_id: uuid.UUID,
        *,
        event_types: set[str] | None = None,
    ) -> bool:
        return await sync_case_duration(
            workspace_id,
            case_id,
            event_types=event_types,
        )

    async def _process_backfill_job(
        self, job: DurationDefinitionSyncJob | DurationBackfillSyncJob
    ) -> bool:
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
            msg_id: str | None = None
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
