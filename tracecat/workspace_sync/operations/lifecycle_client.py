"""Authenticated Temporal commands; database rows are history, never a dispatcher."""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Literal

from pydantic import TypeAdapter
from temporalio.client import WorkflowUpdateFailedError
from temporalio.common import WorkflowIDReusePolicy
from temporalio.exceptions import ApplicationError, WorkflowAlreadyStartedError
from temporalio.service import RPCError, RPCStatusCode

from tracecat import config
from tracecat.authz.controls import check_scopes
from tracecat.db.models import WorkspaceSyncOperation
from tracecat.dsl.client import get_temporal_client
from tracecat.exceptions import TracecatNotFoundError
from tracecat.service import BaseWorkspaceService
from tracecat.sync import PullResult
from tracecat.workspace_sync.operations.domain import fingerprint
from tracecat.workspace_sync.operations.lifecycle import WorkspaceSyncLifecycleWorkflow
from tracecat.workspace_sync.operations.lifecycle_types import (
    SyncCommand,
    SyncLifecycleInput,
    SyncLifecycleSnapshot,
    SyncLifecycleState,
)
from tracecat.workspace_sync.operations.schemas import (
    SyncOperationCommand,
    SyncOperationCreate,
    SyncOperationRead,
    SyncPushResult,
)
from tracecat.workspace_sync.operations.service import SyncOperationService
from tracecat.workspace_sync.operations.types import (
    SyncOperationConflictError,
    SyncOperationStartError,
)

_RPC_TIMEOUT = timedelta(seconds=5)


class SyncLifecycleService(BaseWorkspaceService):
    """Start once, query authoritative state, and send confirmed workflow commands."""

    service_name = "workspace_sync_lifecycle"

    def workflow_id(self, operation_id: uuid.UUID) -> str:
        return f"workspace-sync/v2/{self.workspace_id}/{operation_id}"

    def _log_failure(
        self, action: str, operation_id: uuid.UUID, exc: Exception
    ) -> None:
        # Provider strings can contain request data. Preserve typed causes/status
        # for operators without serializing those strings into logs or responses.
        self.logger.warning(
            "Temporal Git sync request failed",
            action=action,
            operation_id=operation_id,
            error_type=type(exc).__name__,
            rpc_status=exc.status.name if isinstance(exc, RPCError) else None,
        )

    def _authorize(self, snapshot: SyncLifecycleSnapshot) -> None:
        owner = snapshot.start.role
        if (
            owner.workspace_id != self.workspace_id
            or owner.organization_id != self.role.organization_id
            or owner.actor_id != self.role.actor_id
            or owner.type != self.role.type
        ):
            raise TracecatNotFoundError("Sync operation not found")
        if snapshot.state.preview is not None:
            check_scopes(self.role, *snapshot.state.preview.read_scopes)

    async def _query(self, operation_id: uuid.UUID) -> SyncLifecycleSnapshot:
        missing = False
        try:
            client = await get_temporal_client()
            handle = client.get_workflow_handle_for(
                WorkspaceSyncLifecycleWorkflow.run, self.workflow_id(operation_id)
            )
            snapshot = await handle.query(
                WorkspaceSyncLifecycleWorkflow.snapshot, rpc_timeout=_RPC_TIMEOUT
            )
        except RPCError as exc:
            missing = exc.status == RPCStatusCode.NOT_FOUND
            if not missing:
                self._log_failure("query", operation_id, exc)
        except Exception as exc:
            self._log_failure("query", operation_id, exc)
        else:
            self._authorize(snapshot)
            return snapshot
        if missing:
            raise TracecatNotFoundError("Sync operation not found")
        raise SyncOperationStartError(
            "Git sync status unavailable. Retry the same request."
        )

    async def _history(self, operation_id: uuid.UUID) -> WorkspaceSyncOperation | None:
        # This read owns its short-lived session. It never rolls back or commits
        # the request's session, and no history transaction spans a Temporal call.
        async with SyncOperationService.with_session(self.role) as service:
            try:
                return await service.get(operation_id)
            except TracecatNotFoundError:
                return None

    def _response(
        self, snapshot: SyncLifecycleSnapshot, history: SyncOperationRead | None
    ) -> SyncOperationRead:
        self._authorize(snapshot)
        state = snapshot.state
        if history is None:
            history = SyncOperationRead(
                id=snapshot.start.inputs.id,
                direction=snapshot.start.inputs.direction,
                status=state.status,
                stage=state.stage,
                created_at=state.created_at,
                expires_at=state.expires_at,
                inputs=snapshot.start.inputs,
            )
        return history.model_copy(
            update={
                "status": state.status,
                "stage": state.stage,
                "created_at": state.created_at,
                "expires_at": state.expires_at,
                "error": state.error,
                "can_retry": state.can_retry,
                "data_applied": state.data_applied,
                "preview_revision": state.preview_revision,
                "state_revision": state.revision,
                "diff_count": state.preview.diff_count if state.preview else 0,
                "commit_sha": state.preview.commit_sha if state.preview else None,
            }
        )

    async def create(self, inputs: SyncOperationCreate) -> SyncOperationRead:
        if self.role.actor_id is None:
            raise SyncOperationConflictError("Git sync requires an attributed actor")
        start = SyncLifecycleInput(
            inputs, self.role, fingerprint(inputs.model_dump(mode="json"))
        )
        # Temporal eventually removes old executions. The retained audit record
        # still reserves this operation ID; this check never advances a phase.
        history = await self._history(inputs.id)
        if history is not None:
            if history.inputs != inputs.model_dump(mode="json"):
                raise SyncOperationConflictError(
                    "Operation ID already belongs to different inputs"
                )
            return await self.get(inputs.id)
        exists = False
        try:
            client = await get_temporal_client()
            await client.start_workflow(
                WorkspaceSyncLifecycleWorkflow.run,
                start,
                id=self.workflow_id(inputs.id),
                task_queue=config.TRACECAT__BACKGROUND_QUEUE,
                id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE,
                rpc_timeout=_RPC_TIMEOUT,
            )
        except WorkflowAlreadyStartedError:
            exists = True
        except Exception as exc:
            self._log_failure("start", inputs.id, exc)
        else:
            now = datetime.now(UTC)
            return self._response(
                SyncLifecycleSnapshot(
                    start,
                    SyncLifecycleState(
                        "queued",
                        "fetching",
                        0,
                        now,
                        now + timedelta(hours=24),
                    ),
                ),
                None,
            )
        if exists:
            snapshot = await self._query(inputs.id)
            if snapshot.start.fingerprint != start.fingerprint:
                raise SyncOperationConflictError(
                    "Operation ID already belongs to different inputs"
                )
            return await self.get(inputs.id)
        raise SyncOperationStartError(
            "Unable to confirm Git sync startup. Retry the same operation."
        )

    async def get(self, operation_id: uuid.UUID) -> SyncOperationRead:
        history = await self._history(operation_id)
        if (
            history is not None
            and (history.summary or {}).get("lifecycle_version") != 2
        ):
            return SyncOperationService(self.session, self.role).read(history)
        if history is not None and (
            history.status in {"completed", "expired"}
            or (
                history.status == "failed"
                and (history.summary or {}).get("retryable") is False
            )
        ):
            # Terminal copies never transition again. Keep audit details readable
            # after Temporal retention removes the execution or a worker is down.
            return SyncOperationService(self.session, self.role).read(history)
        public = (
            SyncOperationService(self.session, self.role).read(history)
            if history is not None
            else None
        )
        snapshot = await self._query(operation_id)
        # Preparation may have finished after the first read; fetch its immutable
        # metadata/receipt again instead of showing an empty ready preview.
        needs_metadata = snapshot.state.preview is not None and (
            history is None
            or history.artifact_key != snapshot.state.preview.artifact_key
        )
        needs_result = snapshot.state.status == "completed" and (
            history is None or history.result is None
        )
        if needs_metadata or needs_result:
            history = await self._history(operation_id)
            if history is not None:
                public = SyncOperationService(self.session, self.role).read(history)
        response = self._response(snapshot, public)
        if snapshot.state.status == "completed" and history is not None:
            # Receipt loading must not depend on a possibly delayed status copy.
            if history.result is not None:
                response.result = (
                    SyncPushResult.model_validate(history.result)
                    if history.direction == "push"
                    else TypeAdapter(PullResult).validate_python(history.result)
                )
        return response

    async def command(
        self,
        operation_id: uuid.UUID,
        kind: Literal["apply", "retry"],
        params: SyncOperationCommand,
    ) -> SyncOperationRead:
        # Validate visibility before asking the workflow to accept a command.
        current = await self.get(operation_id)
        # Reject lost push permission before the workflow consumes its preview.
        # The activity still refreshes authorization at the write boundary.
        if current.direction == "push":
            check_scopes(self.role, "workspace_sync:sync")
        history = await self._history(operation_id)
        if (
            history is not None
            and (history.summary or {}).get("lifecycle_version") != 2
        ):
            raise SyncOperationConflictError(
                "Start a fresh preview to use the current Git sync lifecycle"
            )
        if (
            current.status == "completed"
            and params.preview_revision == current.preview_revision
        ):
            return current
        conflict = False
        missing = False
        try:
            client = await get_temporal_client()
            handle = client.get_workflow_handle_for(
                WorkspaceSyncLifecycleWorkflow.run, self.workflow_id(operation_id)
            )
            update = (
                WorkspaceSyncLifecycleWorkflow.apply
                if kind == "apply"
                else WorkspaceSyncLifecycleWorkflow.retry
            )
            await handle.execute_update(
                update,
                SyncCommand(
                    params.command_id, params.preview_revision, params.state_revision
                ),
                # Identical retransmissions deduplicate; changed inputs reach the
                # workflow's command-ID collision check instead of SDK caching.
                id=f"{kind}/{params.command_id}/{params.preview_revision}/{params.state_revision}",
                rpc_timeout=_RPC_TIMEOUT,
            )
        except WorkflowUpdateFailedError as exc:
            conflict = (
                isinstance(exc.cause, ApplicationError)
                and exc.cause.type == "SyncCommandConflict"
            )
            if not conflict:
                self._log_failure(kind, operation_id, exc)
        except RPCError as exc:
            missing = exc.status in {
                RPCStatusCode.NOT_FOUND,
                RPCStatusCode.FAILED_PRECONDITION,
            }
            if not missing:
                self._log_failure(kind, operation_id, exc)
        except Exception as exc:
            self._log_failure(kind, operation_id, exc)
        else:
            return await self.get(operation_id)
        if conflict or missing:
            raise SyncOperationConflictError(
                "This operation changed or expired. Refresh its state before confirming."
            )
        raise SyncOperationStartError(
            "Unable to confirm the Git sync command. Retry the same command."
        )
