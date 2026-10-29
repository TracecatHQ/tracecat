"""External work and receipts; lifecycle decisions remain in Temporal."""

from datetime import UTC, datetime, timedelta
from typing import Literal, cast

from pydantic import TypeAdapter
from sqlalchemy.dialects.postgresql import insert
from temporalio import activity
from temporalio.exceptions import ApplicationError

from tracecat.cases.durations.reconciliation import CaseDurationBackfills
from tracecat.contexts import ctx_role
from tracecat.db.models import WorkspaceSyncOperation
from tracecat.exceptions import TracecatNotFoundError
from tracecat.sync import PullResult
from tracecat.workflow.schedules.reconciliation import ScheduleChanges
from tracecat.workspace_sync.operations.activities import (
    heartbeats,
    operation_context,
    receipt_context,
    safe_activity,
)
from tracecat.workspace_sync.operations.auth import refresh_sync_role
from tracecat.workspace_sync.operations.domain import (
    DurableSyncService,
    preview_summary,
)
from tracecat.workspace_sync.operations.lifecycle_types import (
    SyncApplyInput,
    SyncLifecycleInput,
    SyncLifecycleSnapshot,
    SyncPreparedRef,
    SyncReceiptStatus,
)
from tracecat.workspace_sync.operations.reconciliation import (
    CASE_DURATION_BACKFILLS,
    SCHEDULE_CHANGES,
    reconcile_case_duration_backfills,
    reconcile_schedules,
)
from tracecat.workspace_sync.operations.schemas import SyncPushResult
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
    SyncOperationRef,
)


def _ref(
    start: SyncLifecycleInput, phase: Literal["preview", "apply"] = "preview"
) -> SyncOperationRef:
    return SyncOperationRef(
        start.inputs.id, start.role, "apply" if phase == "apply" else "preview"
    )


def _prepared_ref(operation: WorkspaceSyncOperation) -> SyncPreparedRef:
    if operation.artifact_key is None or operation.commit_sha is None:
        raise StaleSyncPreviewError("Missing prepared artifact")
    summary = operation.summary or {}
    return SyncPreparedRef(
        artifact_key=operation.artifact_key,
        commit_sha=operation.commit_sha,
        diff_count=cast(int, summary.get("diff_count", 0)),
        read_scopes=TypeAdapter(list[str]).validate_python(
            summary.get("read_scopes", [])
        ),
        expires_at=operation.expires_at,
        valid=summary.get("preview_valid") is not False,
        write_scopes=TypeAdapter(list[str] | None).validate_python(
            summary.get("write_scopes")
        ),
    )


@activity.defn
@safe_activity
async def workspace_sync_initialize(start: SyncLifecycleInput) -> None:
    """Index a workflow that Temporal has already accepted; never start work here."""
    async with SyncOperationService.with_session(start.role) as service:
        await service.session.execute(
            insert(WorkspaceSyncOperation)
            .values(
                id=start.inputs.id,
                workspace_id=start.role.workspace_id,
                actor_id=start.role.actor_id,
                direction=start.inputs.direction,
                status="queued",
                stage="fetching",
                attempt=0,
                inputs=start.inputs.model_dump(mode="json"),
                actor=start.role.model_dump(mode="json"),
                summary={"lifecycle_version": 2, "lifecycle_revision": -1},
                expires_at=datetime.now(UTC) + timedelta(hours=24),
            )
            .on_conflict_do_nothing(index_elements=["id"])
        )
        operation = await service.get(start.inputs.id)
        if (
            operation.inputs != start.inputs.model_dump(mode="json")
            or (operation.summary or {}).get("lifecycle_version") != 2
        ):
            raise ApplicationError(
                "Operation ID already belongs to another request", non_retryable=True
            )
        await service.session.commit()


@activity.defn
@safe_activity
async def workspace_sync_prepare_lifecycle(
    start: SyncLifecycleInput,
) -> SyncPreparedRef:
    """Capture once and return only a small reference, never file contents."""
    ref = _ref(start)
    async with heartbeats():
        async with SyncOperationService.with_session(start.role) as service:
            operation = await service.get(start.inputs.id)
            # This is an immutable-artifact receipt, not a lifecycle status gate.
            if operation.artifact_key is not None:
                return _prepared_ref(operation)
            role = await refresh_sync_role(service.session, start.role)
            token = ctx_role.set(role)
            try:
                sync = await DurableSyncService.for_workspace(service.session, role)
                fetched = await sync.fetch_remote(
                    start.inputs, release_read_session=True
                )
            finally:
                ctx_role.reset(token)
        async with operation_context(ref) as (operation, sync, _):
            if operation.artifact_key is not None:
                return _prepared_ref(operation)
            prepared = await sync.prepare(start.inputs, fetched=fetched)
        key = await store_prepared(
            artifact_prefix(sync.workspace_id, start.inputs.id), prepared
        )
        async with operation_context(ref) as (operation, sync, session):
            if operation.artifact_key is not None:
                return _prepared_ref(operation)
            if (
                await sync.repository_fingerprint() != prepared.repository_fingerprint
                or await sync.local_fingerprint(start.inputs)
                != prepared.workspace_fingerprint
            ):
                raise StaleSyncPreviewError("Workspace changed during preparation")
            valid = (
                not isinstance(prepared.preview, PullResult) or prepared.preview.success
            )
            operation.artifact_key = key
            operation.commit_sha = prepared.compare_sha
            operation.summary = {
                **(operation.summary or {}),
                **json_document(preview_summary(prepared)),
                "preview_valid": valid,
            }
            operation.expires_at = datetime.now(UTC) + timedelta(hours=24)
            await session.commit()
            return _prepared_ref(operation)


@activity.defn
@safe_activity
async def workspace_sync_apply_lifecycle(params: SyncApplyInput) -> bool:
    """Save imported resources and their receipt together; ignore history status."""
    start = params.start
    async with (
        heartbeats(),
        receipt_context(_ref(start, "apply")) as (operation, session),
    ):
        if operation.result is not None:
            return start.inputs.direction == "pull"
        role = await refresh_sync_role(session, start.role)
        token = ctx_role.set(role)
        try:
            sync = await DurableSyncService.for_workspace(session, role)
            if operation.artifact_key != params.preview.artifact_key:
                raise StaleSyncPreviewError("Preview reference changed")
            try:
                prepared = await load_prepared(params.preview.artifact_key)
            except FileNotFoundError:
                raise StaleSyncPreviewError(
                    "Preview artifact is no longer available"
                ) from None
            with (
                ScheduleChanges.capture(session) as changes,
                CaseDurationBackfills.capture(session) as backfills,
            ):
                result = await sync.apply(start.inputs, prepared, start.inputs.id)
            operation.result = json_document(
                result.model_dump(mode="json")
                if isinstance(result, SyncPushResult)
                else TypeAdapter(PullResult).dump_python(result, mode="json")
            )
            operation.summary = {
                **(operation.summary or {}),
                "schedule_changes": SCHEDULE_CHANGES.dump_python(changes, mode="json"),
                "schedule_role": role.model_dump(mode="json"),
                "case_duration_backfills": CASE_DURATION_BACKFILLS.dump_python(
                    backfills, mode="json"
                ),
            }
            await session.commit()
        finally:
            ctx_role.reset(token)
    return start.inputs.direction == "pull"


@activity.defn
@safe_activity
async def workspace_sync_committed_lifecycle(
    start: SyncLifecycleInput,
) -> SyncReceiptStatus:
    """Resolve a lost commit acknowledgement before offering a user retry."""
    try:
        async with receipt_context(_ref(start, "apply")) as (operation, _):
            return "committed" if operation.result is not None else "uncommitted"
    except TracecatNotFoundError:
        # Deletion removes the imported resources and their pending effects.
        return "missing"


@activity.defn
@safe_activity
async def workspace_sync_finish_lifecycle(start: SyncLifecycleInput) -> None:
    """Finish committed effects even if the initiating actor's access was revoked."""
    async with heartbeats():
        try:
            async with receipt_context(_ref(start, "apply")) as (operation, session):
                if operation.result is None:
                    raise ApplicationError("Missing write receipt", non_retryable=True)
                await reconcile_schedules(session, start.role, operation)
                await reconcile_case_duration_backfills(operation)
                await session.commit()
        except TracecatNotFoundError:
            # Workspace deletion also removes its resources and pending effects.
            return


@activity.defn
@safe_activity
async def workspace_sync_project(snapshot: SyncLifecycleSnapshot) -> None:
    """Copy state to history in revision order; never authorize or initiate work."""
    try:
        async with receipt_context(_ref(snapshot.start)) as (operation, session):
            state = snapshot.state
            summary = operation.summary or {}
            if summary.get("lifecycle_version") != 2:
                return
            revision = summary.get("lifecycle_revision", -1)
            if isinstance(revision, int) and revision >= state.revision:
                return
            operation.status = state.status
            operation.stage = state.stage
            operation.error = state.error
            operation.expires_at = state.expires_at
            operation.summary = {
                **summary,
                "lifecycle_revision": state.revision,
                "preview_revision": state.preview_revision,
                "retryable": state.can_retry,
            }
            await session.commit()
    except TracecatNotFoundError:
        return
