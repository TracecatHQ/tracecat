"""Temporal owns preparation, confirmation, retries, expiry, and completion."""

from dataclasses import replace
from datetime import timedelta
from typing import Literal

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError, ApplicationError

with workflow.unsafe.imports_passed_through():
    from tracecat.workspace_sync.operations.lifecycle_types import (
        SyncApplyInput,
        SyncCommand,
        SyncCommandReceipt,
        SyncLifecycleInput,
        SyncLifecycleSnapshot,
        SyncLifecycleState,
        SyncPreparedRef,
    )

_CONFIRMATION_WINDOW = timedelta(hours=24)
_RETRY_WINDOW = timedelta(hours=24)
_MAX_COMMANDS = 128


@workflow.defn
class WorkspaceSyncLifecycleWorkflow:
    """One operation remains open while its initiating actor reviews the preview."""

    @workflow.init
    def __init__(self, start: SyncLifecycleInput) -> None:
        self.start = start
        now = workflow.now()
        self.state = SyncLifecycleState(
            status="queued",
            stage="fetching",
            revision=0,
            created_at=now,
            expires_at=now + _CONFIRMATION_WINDOW,
        )
        self.phase: Literal["prepare", "apply", "finish"] = "prepare"
        self.pending = False
        self.commands: dict[str, SyncCommandReceipt] = {}

    @workflow.query
    def snapshot(self) -> SyncLifecycleSnapshot:
        """Return state only; queries never read or mutate external systems."""
        return SyncLifecycleSnapshot(self.start, self.state)

    def _remember(
        self, kind: Literal["apply", "retry"], command: SyncCommand
    ) -> SyncLifecycleState | None:
        receipt = self.commands.get(str(command.command_id))
        if receipt is not None:
            if receipt.kind != kind or receipt.command != command:
                raise ApplicationError(
                    "Command ID belongs to different inputs", type="SyncCommandConflict"
                )
            return receipt.state
        if len(self.commands) >= _MAX_COMMANDS:
            raise ApplicationError(
                "Too many commands; start a new preview", type="SyncCommandConflict"
            )
        return None

    def _accept(
        self, kind: Literal["apply", "retry"], command: SyncCommand
    ) -> SyncLifecycleState:
        self.commands[str(command.command_id)] = SyncCommandReceipt(
            kind, command, self.state
        )
        return self.state

    @workflow.update
    def apply(self, command: SyncCommand) -> SyncLifecycleState:
        """Reserve exactly one application of the reviewed immutable preview."""
        if previous := self._remember("apply", command):
            return previous
        if (
            command.preview_revision != self.state.preview_revision
            or not self.state.preview
        ):
            raise ApplicationError(
                "A fresh successful preview is required", type="SyncCommandConflict"
            )
        if self.state.status in {"applying", "completed"}:
            return self._accept("apply", command)
        if (
            self.state.status != "ready"
            or command.state_revision != self.state.revision
            or workflow.now() >= self.state.expires_at
        ):
            raise ApplicationError(
                "A fresh successful preview is required", type="SyncCommandConflict"
            )
        self.state = replace(
            self.state,
            status="applying",
            stage="applying",
            revision=self.state.revision + 1,
        )
        self.pending = True
        return self._accept("apply", command)

    @workflow.update
    def retry(self, command: SyncCommand) -> SyncLifecycleState:
        """Resume a failed stage without creating a new execution or preview."""
        if previous := self._remember("retry", command):
            return previous
        if (
            self.state.status != "failed"
            or not self.state.can_retry
            or command.state_revision != self.state.revision
            or command.preview_revision != self.state.preview_revision
            or workflow.now() >= self.state.expires_at
        ):
            raise ApplicationError(
                "This failure requires a new preview", type="SyncCommandConflict"
            )
        self.state = replace(
            self.state,
            status="running" if self.phase == "prepare" else "applying",
            revision=self.state.revision + 1,
            can_retry=False,
            error=None,
        )
        self.pending = True
        return self._accept("retry", command)

    async def _publish(self, *, terminal: bool = False) -> None:
        """A bounded history-copy failure never becomes a lifecycle decision."""
        try:
            await workflow.execute_activity(
                "workspace_sync_project",
                SyncLifecycleSnapshot(self.start, self.state),
                start_to_close_timeout=timedelta(seconds=10),
                schedule_to_close_timeout=None if terminal else timedelta(seconds=30),
                retry_policy=RetryPolicy(
                    maximum_attempts=0 if terminal else 3,
                    maximum_interval=timedelta(minutes=1),
                ),
            )
        except ActivityError:
            if terminal:
                raise
            workflow.logger.warning(
                "Sync history copy delayed", extra={"revision": self.state.revision}
            )

    async def _wait(self) -> bool:
        """Temporal records this deadline and resumes the wait after restarts."""
        try:
            await workflow.wait_condition(
                lambda: self.pending,
                timeout=max(timedelta(), self.state.expires_at - workflow.now()),
            )
        except TimeoutError:
            self.state = replace(
                self.state,
                status="expired",
                stage="finished",
                can_retry=False,
                revision=self.state.revision + 1,
            )
            await self._publish(terminal=True)
            return False
        self.pending = False
        return True

    @workflow.run
    async def run(self, start: SyncLifecycleInput) -> None:
        while True:
            try:
                if self.phase == "prepare":
                    await workflow.execute_activity(
                        "workspace_sync_initialize",
                        start,
                        start_to_close_timeout=timedelta(minutes=1),
                        retry_policy=RetryPolicy(maximum_attempts=5),
                    )
                    self.state = replace(
                        self.state,
                        status="running",
                        stage="preparing",
                        revision=self.state.revision + 1,
                    )
                    await self._publish()
                    preview = await workflow.execute_activity(
                        "workspace_sync_prepare_lifecycle",
                        start,
                        start_to_close_timeout=timedelta(hours=2),
                        schedule_to_close_timeout=timedelta(hours=6),
                        heartbeat_timeout=timedelta(seconds=60),
                        retry_policy=RetryPolicy(
                            maximum_attempts=5, maximum_interval=timedelta(minutes=1)
                        ),
                        result_type=SyncPreparedRef,
                    )
                    self.state = replace(
                        self.state,
                        status="ready" if preview.valid else "failed",
                        stage="awaiting_confirmation" if preview.valid else "preparing",
                        preview=preview,
                        preview_revision=1,
                        expires_at=preview.expires_at,
                        revision=self.state.revision + 1,
                        error=None
                        if preview.valid
                        else "Resolve the preview diagnostics and start a new preview.",
                    )
                    await self._publish()
                    if not preview.valid:
                        await self._publish(terminal=True)
                        return
                    if not await self._wait():
                        return
                    self.phase = "apply"
                if self.phase == "apply":
                    assert self.state.preview is not None
                    await self._publish()
                    data_applied = await workflow.execute_activity(
                        "workspace_sync_apply_lifecycle",
                        SyncApplyInput(start, self.state.preview),
                        start_to_close_timeout=timedelta(hours=2),
                        schedule_to_close_timeout=timedelta(hours=6),
                        heartbeat_timeout=timedelta(seconds=60),
                        retry_policy=RetryPolicy(
                            maximum_attempts=5, maximum_interval=timedelta(minutes=1)
                        ),
                        result_type=bool,
                    )
                    self.state = replace(
                        self.state,
                        stage="finishing",
                        data_applied=data_applied,
                        revision=self.state.revision + 1,
                    )
                    self.phase = "finish"
                    await self._publish()
                # Receipt reconciliation retries within this execution. A preview
                # deadline cannot abandon changes that have already committed.
                await workflow.execute_activity(
                    "workspace_sync_finish_lifecycle",
                    start,
                    start_to_close_timeout=timedelta(hours=2),
                    heartbeat_timeout=timedelta(seconds=60),
                    retry_policy=RetryPolicy(maximum_interval=timedelta(minutes=5)),
                )
                self.state = replace(
                    self.state,
                    status="completed",
                    stage="finished",
                    error=None,
                    revision=self.state.revision + 1,
                )
                await self._publish(terminal=True)
                return
            except ActivityError as exc:
                if self.phase == "apply":
                    receipt = await workflow.execute_activity(
                        "workspace_sync_committed_lifecycle",
                        start,
                        start_to_close_timeout=timedelta(minutes=1),
                        retry_policy=RetryPolicy(maximum_interval=timedelta(minutes=1)),
                        result_type=str,
                    )
                    if receipt == "missing":
                        self.state = replace(
                            self.state,
                            status="expired",
                            stage="finished",
                            can_retry=False,
                            error="Workspace no longer available.",
                            revision=self.state.revision + 1,
                        )
                        await self._publish(terminal=True)
                        return
                    if receipt == "committed":
                        self.phase = "finish"
                        self.state = replace(
                            self.state,
                            status="applying",
                            stage="finishing",
                            data_applied=start.inputs.direction == "pull",
                            can_retry=False,
                            error=None,
                            revision=self.state.revision + 1,
                        )
                        await self._publish()
                        continue
                cause = exc.cause
                permanent = isinstance(cause, ApplicationError) and cause.non_retryable
                stale = (
                    isinstance(cause, ApplicationError)
                    and cause.type == "StaleSyncPreviewError"
                )
                authorization = (
                    isinstance(cause, ApplicationError)
                    and cause.type == "TracecatAuthorizationError"
                )
                error = (
                    "Workspace, repository, or preview validity changed. Start a fresh preview."
                    if stale
                    else "Access changed. Restore access and start a fresh preview."
                    if authorization
                    else "Validation failed. Review the inputs and start a fresh preview."
                    if permanent
                    else "Sync failed at this stage. Retry to resume the operation."
                )
                self.state = replace(
                    self.state,
                    status="failed",
                    can_retry=not permanent,
                    expires_at=workflow.now() + _RETRY_WINDOW,
                    error=error,
                    revision=self.state.revision + 1,
                )
                await self._publish()
                if permanent:
                    await self._publish(terminal=True)
                    return
                if not await self._wait():
                    return
