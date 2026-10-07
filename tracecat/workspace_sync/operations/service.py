"""Operation lifecycle and idempotent Temporal dispatch."""

import uuid
from datetime import UTC, datetime, timedelta
from typing import cast

from pydantic import JsonValue, TypeAdapter
from sqlalchemy import Select, func, select
from sqlalchemy import cast as sql_cast
from sqlalchemy.dialects.postgresql import JSONB, insert
from temporalio.common import WorkflowIDReusePolicy
from temporalio.exceptions import WorkflowAlreadyStartedError

from tracecat import config
from tracecat.auth.types import Role
from tracecat.authz.controls import get_missing_scopes, has_scope
from tracecat.db.models import WorkspaceSyncOperation
from tracecat.dsl.client import get_temporal_client
from tracecat.exceptions import ScopeDeniedError, TracecatNotFoundError
from tracecat.pagination import Page, PageParams, paginate
from tracecat.service import BaseWorkspaceService
from tracecat.sync import PullResult
from tracecat.workspace_sync.adapters import WORKSPACE_RESOURCE_ADAPTERS
from tracecat.workspace_sync.operations.schemas import (
    SyncOperationCreate,
    SyncOperationRead,
)
from tracecat.workspace_sync.operations.types import (
    SyncDirection,
    SyncOperationConflictError,
    SyncOperationRef,
    SyncStage,
    SyncStatus,
)
from tracecat.workspace_sync.operations.workflows import WorkspaceSyncWorkflow
from tracecat.workspace_sync.schemas import (
    WorkspaceSyncExportPreview,
    WorkspaceSyncExportResult,
)

_PULL = TypeAdapter(PullResult)


def json_document(value: object) -> dict[str, JsonValue]:
    """Validate persisted JSON using the universal JSON-value contract."""
    return TypeAdapter(dict[str, JsonValue]).validate_python(value)


class SyncOperationService(BaseWorkspaceService):
    """Manage actor-scoped operations and repair ambiguous dispatch responses."""

    service_name = "workspace_sync_operations"

    def statement(self) -> Select[tuple[WorkspaceSyncOperation]]:
        """Constrain every access by both tenant and initiating actor."""
        return select(WorkspaceSyncOperation).where(
            WorkspaceSyncOperation.workspace_id == self.workspace_id,
            WorkspaceSyncOperation.actor_id == self.role.actor_id,
        )

    async def get(
        self, operation_id: uuid.UUID, *, lock: bool = False
    ) -> WorkspaceSyncOperation:
        statement = self.statement().where(WorkspaceSyncOperation.id == operation_id)
        if lock:
            statement = statement.with_for_update()
        operation = await self.session.scalar(statement)
        if operation is None:
            raise TracecatNotFoundError("Sync operation not found")
        self.check_read_scopes(operation)
        return operation

    def check_read_scopes(self, operation: WorkspaceSyncOperation) -> None:
        required = TypeAdapter(list[str]).validate_python(
            (operation.summary or {}).get("read_scopes", [])
        )
        missing = get_missing_scopes(self.role.scopes or frozenset(), set(required))
        if missing:
            raise ScopeDeniedError(
                required_scopes=required, missing_scopes=sorted(missing)
            )

    async def create(self, inputs: SyncOperationCreate) -> WorkspaceSyncOperation:
        if self.role.actor_id is None:
            raise SyncOperationConflictError("Git sync requires an attributed actor")
        await self.session.execute(
            insert(WorkspaceSyncOperation)
            .values(
                id=inputs.id,
                workspace_id=self.workspace_id,
                actor_id=self.role.actor_id,
                direction=inputs.direction,
                status="queued",
                stage="fetching",
                inputs=inputs.model_dump(mode="json"),
                actor=self.role.model_dump(mode="json"),
                expires_at=datetime.now(UTC) + timedelta(hours=24),
                attempt=0,
                dispatched=False,
            )
            .on_conflict_do_nothing(index_elements=["id"])
        )
        operation = await self.get(inputs.id)
        if operation.inputs != inputs.model_dump(mode="json"):
            raise SyncOperationConflictError(
                "Operation ID already belongs to different inputs"
            )
        await self.session.commit()
        return operation

    async def dispatch(self, operation: WorkspaceSyncOperation) -> None:
        """Starting the same workflow ID repairs a lost start response safely."""
        if operation.status not in {"queued", "running", "applying"}:
            return
        phase = "apply" if operation.status == "applying" else "preview"
        ref = SyncOperationRef(
            operation.id, Role.model_validate(operation.actor), phase, operation.attempt
        )
        client = await get_temporal_client()
        try:
            await client.start_workflow(
                WorkspaceSyncWorkflow.run,
                ref,
                id=f"workspace-sync/{self.workspace_id}/{operation.id}/{phase}/{operation.attempt}",
                task_queue=config.TRACECAT__BACKGROUND_QUEUE,
                id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE,
                rpc_timeout=timedelta(seconds=5),
            )
        except WorkflowAlreadyStartedError:
            pass

    async def apply(self, operation_id: uuid.UUID) -> WorkspaceSyncOperation:
        operation = await self.get(operation_id, lock=True)
        if operation.status in {"applying", "completed"}:
            await self.session.commit()
            return operation
        if operation.status != "ready" or operation.expires_at <= datetime.now(UTC):
            raise SyncOperationConflictError("A fresh successful preview is required")
        operation.status = "applying"
        operation.dispatched = False
        operation.next_dispatch_at = datetime.now(UTC)
        operation.dispatch_attempts = 0
        operation.stage = "applying"
        operation.actor = json_document(self.role.model_dump(mode="json"))
        await self.session.commit()
        return operation

    async def retry(self, operation_id: uuid.UUID) -> WorkspaceSyncOperation:
        operation = await self.get(operation_id, lock=True)
        if operation.status != "failed":
            await self.session.commit()
            return operation
        if (
            operation.artifact_key is not None
            and operation.stage != "applying"
            and operation.expires_at <= datetime.now(UTC)
        ):
            raise SyncOperationConflictError("Preview expired; start a fresh preview")
        if (operation.summary or {}).get("retryable") is False:
            raise SyncOperationConflictError(
                "Resolve the failure and start a fresh preview"
            )
        operation.attempt += 1
        operation.dispatched = False
        operation.next_dispatch_at = datetime.now(UTC)
        operation.dispatch_attempts = 0
        operation.status = "applying" if operation.stage == "applying" else "queued"
        operation.error = None
        operation.actor = json_document(self.role.model_dump(mode="json"))
        await self.session.commit()
        return operation

    async def list(self, page: PageParams) -> Page[SyncOperationRead]:
        # Filter before pagination so one revoked resource permission cannot
        # hide unrelated operations or produce empty intermediate pages.
        readable_scopes = sorted(
            {
                adapter.read_scope
                for adapter in WORKSPACE_RESOURCE_ADAPTERS
                if adapter.read_scope
                and has_scope(self.role.scopes or frozenset(), adapter.read_scope)
            }
        )
        required = func.coalesce(
            WorkspaceSyncOperation.summary["read_scopes"], sql_cast([], JSONB)
        )
        operations = await paginate(
            self.session,
            self.statement().where(required.op("<@")(sql_cast(readable_scopes, JSONB))),
            page=page,
            order_by=(
                WorkspaceSyncOperation.created_at.desc(),
                WorkspaceSyncOperation.id.desc(),
            ),
        )
        return Page(
            items=[self.read(item) for item in operations.items],
            next_cursor=operations.next_cursor,
            prev_cursor=operations.prev_cursor,
        )

    def read(self, operation: WorkspaceSyncOperation) -> SyncOperationRead:
        self.check_read_scopes(operation)
        preview = None
        diff_count = 0
        if operation.summary:
            diff_count = cast(int, operation.summary.get("diff_count", 0))
            preview_data = operation.summary.get("preview")
            if preview_data is not None:
                preview = (
                    WorkspaceSyncExportPreview.model_validate(preview_data)
                    if operation.direction == "push"
                    else _PULL.validate_python(preview_data)
                )
        result = None
        if operation.result and operation.status == "completed":
            result = (
                WorkspaceSyncExportResult.model_validate(operation.result)
                if operation.direction == "push"
                else _PULL.validate_python(operation.result)
            )
        status = cast(SyncStatus, operation.status)
        if status == "ready" and operation.expires_at <= datetime.now(UTC):
            status = "expired"
        return SyncOperationRead(
            id=operation.id,
            direction=cast(SyncDirection, operation.direction),
            status=status,
            stage=cast(SyncStage, operation.stage),
            created_at=operation.created_at,
            expires_at=operation.expires_at,
            commit_sha=operation.commit_sha,
            error=operation.error,
            preview=preview,
            result=result,
            diff_count=diff_count,
            can_retry=(
                status == "failed"
                and (
                    operation.stage == "applying"
                    or operation.artifact_key is None
                    or operation.expires_at > datetime.now(UTC)
                )
                and (operation.summary or {}).get("retryable") is not False
            ),
            data_applied=operation.direction == "pull" and operation.result is not None,
            inputs=SyncOperationCreate.model_validate(operation.inputs),
        )
