from __future__ import annotations

from datetime import timedelta

from temporalio import workflow
from temporalio.exceptions import ActivityError, ApplicationError

with workflow.unsafe.imports_passed_through():
    from tracecat import config
    from tracecat.agent.workflows.tool_execution import (
        AGENT_TOOL_PRIORITY,
        ExecuteRegistryToolWorkflowInput,
    )
    from tracecat.dsl.common import RETRY_POLICIES
    from tracecat.executor.activities import ExecutorActivities
    from tracecat.storage.object import StoredObject, StoredObjectValidator


def _activity_error_message(error: ActivityError) -> str:
    """Extract a user-facing message from a failed executor activity."""
    cause = error.cause
    if cause is not None:
        return str(cause)
    return str(error)


@workflow.defn
class ExecuteRegistryToolWorkflow:
    """Short workflow that routes a single registry UDF to executor."""

    @workflow.run
    async def run(self, input: ExecuteRegistryToolWorkflowInput) -> StoredObject:
        try:
            stored = await workflow.execute_activity(
                ExecutorActivities.execute_action_activity,
                args=[input.run_input, input.role],
                task_queue=config.TRACECAT__EXECUTOR_QUEUE,
                start_to_close_timeout=timedelta(
                    seconds=int(config.TRACECAT__EXECUTOR_CLIENT_TIMEOUT)
                ),
                heartbeat_timeout=timedelta(
                    seconds=config.TRACECAT__ACTIVITY_HEARTBEAT_TIMEOUT
                )
                if config.TRACECAT__ACTIVITY_HEARTBEAT_TIMEOUT > 0
                else None,
                retry_policy=RETRY_POLICIES["activity:fail_fast"],
                priority=AGENT_TOOL_PRIORITY,
            )
        except ActivityError as e:
            raise ApplicationError(
                _activity_error_message(e),
                non_retryable=True,
            ) from e
        return StoredObjectValidator.validate_python(stored)
