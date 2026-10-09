"""Durable orchestration for platform background work."""

from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError, ApplicationError

with workflow.unsafe.imports_passed_through():
    from tracecat.workspace_sync.operations.types import SyncFailure, SyncOperationRef


@workflow.defn
class WorkspaceSyncWorkflow:
    """Run one preview or apply phase, retaining failures in the operation record."""

    @workflow.run
    async def run(self, ref: SyncOperationRef) -> None:
        try:
            await workflow.execute_activity(
                "workspace_sync_prepare"
                if ref.phase == "preview"
                else "workspace_sync_apply",
                ref,
                start_to_close_timeout=timedelta(hours=2),
                schedule_to_close_timeout=timedelta(hours=6),
                heartbeat_timeout=timedelta(seconds=60),
                retry_policy=RetryPolicy(
                    initial_interval=timedelta(seconds=2),
                    maximum_interval=timedelta(minutes=1),
                    maximum_attempts=5,
                ),
            )
        except ActivityError as exc:
            reason = "transient"
            if isinstance(exc.cause, ApplicationError):
                if exc.cause.type == "StaleSyncPreviewError":
                    reason = "stale"
                elif exc.cause.type == "TracecatAuthorizationError":
                    reason = "authorization"
                elif exc.cause.non_retryable:
                    reason = "validation"
            reconcile_pending = await workflow.execute_activity(
                "workspace_sync_fail",
                SyncFailure(ref, reason),
                start_to_close_timeout=timedelta(minutes=1),
                retry_policy=RetryPolicy(maximum_interval=timedelta(minutes=1)),
                result_type=bool,
            )
            if not reconcile_pending:
                return
            await workflow.sleep(timedelta(minutes=5))
            # Keep prolonged reconciliation outages from growing history forever.
            workflow.continue_as_new(ref)
