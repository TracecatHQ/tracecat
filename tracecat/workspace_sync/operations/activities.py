"""Heartbeat-backed activities with transactional receipts for sync writes."""

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager, suppress
from datetime import UTC, datetime, timedelta
from functools import wraps

from pydantic import TypeAdapter
from sqlalchemy.ext.asyncio import AsyncSession
from temporalio import activity
from temporalio.exceptions import ApplicationError

from tracecat.cases.durations.reconciliation import CaseDurationBackfills
from tracecat.contexts import ctx_role
from tracecat.db.engine import get_serialized_session
from tracecat.db.locks import pg_advisory_xact_lock
from tracecat.db.models import WorkspaceSyncOperation
from tracecat.exceptions import (
    EntitlementRequired,
    TracecatAuthorizationError,
    TracecatNotFoundError,
    TracecatValidationError,
)
from tracecat.sync import PullResult
from tracecat.workflow.schedules.reconciliation import ScheduleChanges
from tracecat.workspace_sync.operations.auth import refresh_sync_role
from tracecat.workspace_sync.operations.domain import (
    DurableSyncService,
    preview_summary,
)
from tracecat.workspace_sync.operations.reconciliation import (
    CASE_DURATION_BACKFILLS,
    SCHEDULE_CHANGES,
    reconcile_case_duration_backfills,
    reconcile_schedules,
)
from tracecat.workspace_sync.operations.schemas import SyncOperationCreate
from tracecat.workspace_sync.operations.service import (
    SyncOperationService,
    json_document,
)
from tracecat.workspace_sync.operations.storage import (
    artifact_prefix,
    load_prepared,
    store_prepared,
)
from tracecat.workspace_sync.operations.types import (
    StaleSyncPreviewError,
    SyncFailure,
    SyncOperationRef,
    lock_key,
)
from tracecat.workspace_sync.schemas import WorkspaceSyncExportResult
from tracecat.workspace_sync.types import SyncCommitConflictError


def safe_activity[**P, T](
    function: Callable[P, Awaitable[T]],
) -> Callable[P, Awaitable[T]]:
    """Keep provider responses and resource contents out of Temporal failures."""

    @wraps(function)
    async def wrapped(*args: P.args, **kwargs: P.kwargs) -> T:
        error_type = "SyncActivityError"
        non_retryable = True
        try:
            return await function(*args, **kwargs)
        except (StaleSyncPreviewError, SyncCommitConflictError):
            error_type = "StaleSyncPreviewError"
        except TracecatAuthorizationError:
            error_type = "TracecatAuthorizationError"
        except (TracecatValidationError, EntitlementRequired):
            error_type = "SyncValidationError"
        except ApplicationError as exc:
            non_retryable = exc.non_retryable
        except Exception:
            non_retryable = False
        # Raise after leaving the handler: suppressing __cause__ inside it still
        # retains the original, potentially sensitive exception in __context__.
        raise ApplicationError(
            "Background sync activity failed",
            type=error_type,
            non_retryable=non_retryable,
        )

    return wrapped


@asynccontextmanager
async def heartbeats() -> AsyncIterator[None]:
    """Keep long network and database work visible, and propagate cancellation."""

    async def beat() -> None:
        while True:
            activity.heartbeat()
            await asyncio.sleep(10)

    task = asyncio.create_task(beat())
    try:
        yield
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


@asynccontextmanager
async def receipt_context(
    ref: SyncOperationRef,
) -> AsyncIterator[tuple[WorkspaceSyncOperation, AsyncSession]]:
    """Serialize workspace mutations and fence overlapping activity attempts."""
    token = ctx_role.set(ref.role)
    try:
        async with get_serialized_session(
            lock_key(f"workspace-sync/{ref.role.workspace_id}")
        ) as session:
            service = SyncOperationService(session, ref.role)
            operation = await service.get(ref.operation_id)
            if operation.attempt != ref.attempt:
                raise StaleSyncPreviewError("Operation attempt was superseded")
            yield operation, session
    finally:
        ctx_role.reset(token)


@asynccontextmanager
async def operation_context(
    ref: SyncOperationRef,
) -> AsyncIterator[tuple[WorkspaceSyncOperation, DurableSyncService, AsyncSession]]:
    """Authorize a new preview or mutation within its locked transaction."""
    async with receipt_context(ref) as (operation, session):
        role = await refresh_sync_role(session, ref.role)
        token = ctx_role.set(role)
        try:
            sync = await DurableSyncService.for_workspace(session, role)
            yield operation, sync, session
        finally:
            ctx_role.reset(token)


async def set_stage(ref: SyncOperationRef, stage: str) -> None:
    async with SyncOperationService.with_session(ref.role) as service:
        operation = await service.get(ref.operation_id, lock=True)
        if operation.status in {"queued", "running"}:
            operation.status = "running"
            operation.stage = stage
            await service.session.commit()


@activity.defn
@safe_activity
async def workspace_sync_prepare(ref: SyncOperationRef) -> None:
    """Persist an immutable preview; retries reuse a committed preparation."""
    async with heartbeats():
        await set_stage(ref, "fetching")
        # Credential lookup and Git fetch use a short-lived ordinary session;
        # no serializable workspace snapshot or operation lock spans remote I/O.
        async with SyncOperationService.with_session(ref.role) as service:
            operation = await service.get(ref.operation_id)
            if operation.status in {"ready", "applying", "completed"}:
                return
            if operation.attempt != ref.attempt:
                raise StaleSyncPreviewError("Operation attempt was superseded")
            inputs = SyncOperationCreate.model_validate(operation.inputs)
            role = await refresh_sync_role(service.session, ref.role)
            token = ctx_role.set(role)
            try:
                sync = await DurableSyncService.for_workspace(service.session, role)
                fetched = await sync.fetch_remote(inputs, release_read_session=True)
            finally:
                ctx_role.reset(token)
        await set_stage(ref, "preparing")
        async with operation_context(ref) as (operation, sync, _):
            if operation.status in {"ready", "applying", "completed"}:
                return
            prepared = await sync.prepare(inputs, fetched=fetched)
        # Artifact uploads can also be slow. Recheck local state in a fresh,
        # fenced transaction before publishing the prepared snapshot as ready.
        key = await store_prepared(
            artifact_prefix(sync.workspace_id, ref.operation_id), prepared
        )
        async with operation_context(ref) as (operation, sync, session):
            if operation.status in {"ready", "applying", "completed"}:
                return
            if (
                await sync.repository_fingerprint() != prepared.repository_fingerprint
                or await sync.local_fingerprint(inputs)
                != prepared.workspace_fingerprint
            ):
                raise StaleSyncPreviewError("Workspace changed during preparation")
            operation.artifact_key = key
            operation.commit_sha = prepared.compare_sha
            operation.summary = json_document(preview_summary(prepared))
            operation.stage = "awaiting_confirmation"
            operation.status = "ready"
            operation.expires_at = datetime.now(UTC) + timedelta(hours=24)
            if (
                isinstance(prepared.preview, PullResult)
                and not prepared.preview.success
            ):
                operation.status = "failed"
                operation.summary = {**(operation.summary or {}), "retryable": False}
                operation.error = (
                    "Resolve the preview diagnostics and start a new preview."
                )
            await session.commit()


@activity.defn
@safe_activity
async def workspace_sync_apply(ref: SyncOperationRef) -> None:
    """Apply stored inputs and commit the receipt atomically with database writes."""
    async with heartbeats():
        async with receipt_context(ref) as (operation, session):
            if operation.status == "completed":
                return
            if operation.status != "applying" or operation.artifact_key is None:
                raise StaleSyncPreviewError("A successful preview is required")
            if operation.result is None:
                # Only a new mutation requires the initiating actor to still
                # have access. A committed receipt must always be completed.
                role = await refresh_sync_role(session, ref.role)
                ctx_role.set(role)
                sync = await DurableSyncService.for_workspace(session, role)
                inputs = SyncOperationCreate.model_validate(operation.inputs)
                try:
                    prepared = await load_prepared(operation.artifact_key)
                except FileNotFoundError:
                    raise StaleSyncPreviewError(
                        "Preview artifact is no longer available"
                    ) from None
                with (
                    ScheduleChanges.capture(session) as changes,
                    CaseDurationBackfills.capture(session) as backfills,
                ):
                    result = await sync.apply(inputs, prepared, operation.id)
                operation.result = json_document(
                    result.model_dump(mode="json")
                    if isinstance(result, WorkspaceSyncExportResult)
                    else TypeAdapter(PullResult).dump_python(result, mode="json")
                )
                operation.summary = {
                    **(operation.summary or {}),
                    "schedule_changes": SCHEDULE_CHANGES.dump_python(
                        changes, mode="json"
                    ),
                    "case_duration_backfills": CASE_DURATION_BACKFILLS.dump_python(
                        backfills, mode="json"
                    ),
                }
                # The import and its pending external effects commit atomically.
                # A retry with this receipt skips source guards and database writes.
                await session.commit()
        async with receipt_context(ref) as (operation, session):
            if operation.status == "completed":
                return
            await reconcile_schedules(session, ref.role, operation)
            await reconcile_case_duration_backfills(operation)
            operation.status = "completed"
            operation.stage = "finished"
            operation.error = None
            await session.commit()


@activity.defn
@safe_activity
async def workspace_sync_fail(failure: SyncFailure) -> None:
    """Record a safe failure without exposing provider responses or resource data."""
    ref = failure.ref
    async with SyncOperationService.with_session(ref.role) as service:
        await pg_advisory_xact_lock(
            service.session, lock_key(f"workspace-sync/{ref.role.workspace_id}")
        )
        try:
            operation = await service.get(ref.operation_id, lock=True)
        except TracecatNotFoundError:
            # Deleting a workspace also deletes its operations. There is no
            # remaining receipt to update and no reason to retry indefinitely.
            return
        if operation.status == "completed" or operation.attempt != ref.attempt:
            return
        if ref.phase == "apply" and operation.result is not None:
            # Committed external effects belong to the system, even if the
            # initiating actor can no longer retry. A new fenced attempt lets
            # the outbox resume receipt reconciliation after prolonged outages.
            operation.status = "applying"
            operation.attempt += 1
            operation.dispatched = False
            operation.dispatch_attempts = 0
            operation.next_dispatch_at = datetime.now(UTC) + timedelta(minutes=5)
            operation.error = (
                "Sync changes committed; retrying pending reconciliation automatically."
            )
            await service.session.commit()
            return
        operation.status = "failed"
        operation.summary = {
            **(operation.summary or {}),
            "retryable": failure.reason == "transient",
        }
        operation.error = {
            "stale": "Workspace, repository, or preview validity changed. Start a fresh preview.",
            "authorization": "Access changed. Restore access and start a fresh preview.",
            "validation": "Validation failed. Review the inputs and start a fresh preview.",
            "transient": "Sync failed at this stage. Retry to resume the operation.",
        }[failure.reason]
        await service.session.commit()
