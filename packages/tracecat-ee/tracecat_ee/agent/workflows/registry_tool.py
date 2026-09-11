from __future__ import annotations

from datetime import timedelta

from temporalio import workflow
from temporalio.exceptions import ActivityError, ApplicationError
from temporalio.exceptions import TimeoutError as TemporalTimeoutError

with workflow.unsafe.imports_passed_through():
    from tracecat import config
    from tracecat.agent.workflows.tool_execution import (
        AGENT_TOOL_PRIORITY,
        REGISTRY_TOOL_ACTIVITY_BUFFER_SECONDS,
        REGISTRY_TOOL_WORKFLOW_BUFFER_SECONDS,
        ExecuteRegistryToolWorkflowInput,
    )
    from tracecat.dsl.common import RETRY_POLICIES
    from tracecat.executor.activities import ExecutorActivities
    from tracecat.runtime.errors import (
        RetryDisposition,
        RuntimeErrorClassification,
        RuntimeErrorKind,
    )
    from tracecat.storage.object import StoredObject, StoredObjectValidator
    from tracecat.temporal.errors import application_error_from_classification
    from tracecat.temporal.patches import WorkflowPatch


def _activity_error_message(error: ActivityError) -> str:
    """Extract a user-facing message from a failed executor activity."""
    cause = error.cause
    if cause is not None:
        return str(cause)
    return str(error)


def _activity_timeout_error(
    cause: TemporalTimeoutError | None = None,
) -> ApplicationError:
    # Queueing, setup, persistence, or a lost worker can exhaust this budget;
    # it does not prove that the workload exceeded its own resource limit.
    classification = RuntimeErrorClassification.platform(
        kind=RuntimeErrorKind.EXECUTOR_ACTIVITY_TIMED_OUT,
        message="Tracecat executor activity timed out",
        retry_disposition=RetryDisposition.NON_RETRYABLE,
        cause=cause,
    )
    return application_error_from_classification(classification)


@workflow.defn
class ExecuteRegistryToolWorkflow:
    """Short workflow that routes a single registry UDF to executor."""

    @workflow.run
    async def run(self, input: ExecuteRegistryToolWorkflowInput) -> StoredObject:
        classify_timeout = workflow.patched(
            WorkflowPatch.REGISTRY_TOOL_ACTIVITY_TIMEOUT
        )
        timeout_seconds = config.TRACECAT__EXECUTOR_CLIENT_TIMEOUT
        schedule_to_close_timeout = None
        if classify_timeout:
            timeout_seconds += REGISTRY_TOOL_ACTIVITY_BUFFER_SECONDS
            schedule_to_close_timeout = timedelta(seconds=timeout_seconds)
            info = workflow.info()
            if info.run_timeout is not None:
                # Workflow startup may already have consumed part of the budget.
                remaining = (
                    info.workflow_start_time
                    + info.run_timeout
                    - workflow.now()
                    - timedelta(seconds=REGISTRY_TOOL_WORKFLOW_BUFFER_SECONDS)
                )
                if remaining <= timedelta(0):
                    raise _activity_timeout_error()
                schedule_to_close_timeout = min(schedule_to_close_timeout, remaining)
        else:
            timeout_seconds = int(timeout_seconds)
        try:
            stored = await workflow.execute_activity(
                ExecutorActivities.execute_action_activity,
                args=[input.run_input, input.role],
                task_queue=config.TRACECAT__EXECUTOR_QUEUE,
                start_to_close_timeout=timedelta(seconds=timeout_seconds),
                schedule_to_close_timeout=schedule_to_close_timeout,
                heartbeat_timeout=timedelta(
                    seconds=config.TRACECAT__ACTIVITY_HEARTBEAT_TIMEOUT
                )
                if config.TRACECAT__ACTIVITY_HEARTBEAT_TIMEOUT > 0
                else None,
                retry_policy=RETRY_POLICIES["activity:fail_fast"],
                priority=AGENT_TOOL_PRIORITY,
            )
        except ActivityError as e:
            if classify_timeout and isinstance(e.cause, TemporalTimeoutError):
                # Do not retry a tool whose side effects may already have occurred.
                raise _activity_timeout_error(e.cause) from e
            raise ApplicationError(
                _activity_error_message(e),
                non_retryable=True,
            ) from e
        return StoredObjectValidator.validate_python(stored)
