from __future__ import annotations

from enum import StrEnum

from temporalio.common import SearchAttributeKey, SearchAttributePair


class WorkflowExecutionEventStatus(StrEnum):
    SCHEDULED = "SCHEDULED"
    STARTED = "STARTED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELED = "CANCELED"
    TERMINATED = "TERMINATED"
    TIMED_OUT = "TIMED_OUT"
    UNKNOWN = "UNKNOWN"


class WorkflowEventType(StrEnum):
    """The event types we care about."""

    WORKFLOW_EXECUTION_STARTED = "WORKFLOW_EXECUTION_STARTED"
    WORKFLOW_EXECUTION_COMPLETED = "WORKFLOW_EXECUTION_COMPLETED"
    WORKFLOW_EXECUTION_FAILED = "WORKFLOW_EXECUTION_FAILED"
    WORKFLOW_EXECUTION_TERMINATED = "WORKFLOW_EXECUTION_TERMINATED"
    WORKFLOW_EXECUTION_CANCELED = "WORKFLOW_EXECUTION_CANCELED"
    WORKFLOW_EXECUTION_CONTINUED_AS_NEW = "WORKFLOW_EXECUTION_CONTINUED_AS_NEW"
    WORKFLOW_EXECUTION_TIMED_OUT = "WORKFLOW_EXECUTION_TIMED_OUT"

    ACTIVITY_TASK_SCHEDULED = "ACTIVITY_TASK_SCHEDULED"
    ACTIVITY_TASK_STARTED = "ACTIVITY_TASK_STARTED"
    ACTIVITY_TASK_COMPLETED = "ACTIVITY_TASK_COMPLETED"
    ACTIVITY_TASK_FAILED = "ACTIVITY_TASK_FAILED"
    ACTIVITY_TASK_TIMED_OUT = "ACTIVITY_TASK_TIMED_OUT"
    ACTIVITY_TASK_CANCELED = "ACTIVITY_TASK_CANCELED"

    CHILD_WORKFLOW_EXECUTION_STARTED = "CHILD_WORKFLOW_EXECUTION_STARTED"
    CHILD_WORKFLOW_EXECUTION_COMPLETED = "CHILD_WORKFLOW_EXECUTION_COMPLETED"
    CHILD_WORKFLOW_EXECUTION_FAILED = "CHILD_WORKFLOW_EXECUTION_FAILED"
    CHILD_WORKFLOW_EXECUTION_CANCELED = "CHILD_WORKFLOW_EXECUTION_CANCELED"
    CHILD_WORKFLOW_EXECUTION_TERMINATED = "CHILD_WORKFLOW_EXECUTION_TERMINATED"
    START_CHILD_WORKFLOW_EXECUTION_INITIATED = (
        "START_CHILD_WORKFLOW_EXECUTION_INITIATED"
    )
    CHILD_WORKFLOW_EXECUTION_TIMED_OUT = "CHILD_WORKFLOW_EXECUTION_TIMED_OUT"

    def to_status(self) -> WorkflowExecutionEventStatus:
        match self:
            case (
                WorkflowEventType.ACTIVITY_TASK_SCHEDULED
                | WorkflowEventType.START_CHILD_WORKFLOW_EXECUTION_INITIATED
            ):
                return WorkflowExecutionEventStatus.SCHEDULED
            case (
                WorkflowEventType.ACTIVITY_TASK_STARTED
                | WorkflowEventType.CHILD_WORKFLOW_EXECUTION_STARTED
            ):
                return WorkflowExecutionEventStatus.STARTED
            case (
                WorkflowEventType.ACTIVITY_TASK_COMPLETED
                | WorkflowEventType.CHILD_WORKFLOW_EXECUTION_COMPLETED
            ):
                return WorkflowExecutionEventStatus.COMPLETED
            case (
                WorkflowEventType.ACTIVITY_TASK_FAILED
                | WorkflowEventType.CHILD_WORKFLOW_EXECUTION_FAILED
            ):
                return WorkflowExecutionEventStatus.FAILED
            case (
                WorkflowEventType.ACTIVITY_TASK_CANCELED
                | WorkflowEventType.CHILD_WORKFLOW_EXECUTION_CANCELED
            ):
                return WorkflowExecutionEventStatus.CANCELED
            case (
                WorkflowEventType.ACTIVITY_TASK_TIMED_OUT
                | WorkflowEventType.CHILD_WORKFLOW_EXECUTION_TIMED_OUT
            ):
                return WorkflowExecutionEventStatus.TIMED_OUT
            case WorkflowEventType.CHILD_WORKFLOW_EXECUTION_TERMINATED:
                return WorkflowExecutionEventStatus.TERMINATED
            case _:
                return WorkflowExecutionEventStatus.UNKNOWN


class TriggerType(StrEnum):
    """Trigger type for a workflow execution."""

    MANUAL = "manual"
    SCHEDULED = "scheduled"
    WEBHOOK = "webhook"

    def to_temporal_search_attr_pair(self) -> SearchAttributePair[str]:
        return SearchAttributePair(
            key=SearchAttributeKey.for_keyword("TracecatTriggerType"),
            value=self.value,
        )
