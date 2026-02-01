from __future__ import annotations

from enum import StrEnum
from functools import cached_property

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
    DETACHED = "DETACHED"


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

    WORKFLOW_EXECUTION_SIGNALED = "WORKFLOW_EXECUTION_SIGNALED"
    WORKFLOW_EXECUTION_UPDATE_ACCEPTED = "WORKFLOW_EXECUTION_UPDATE_ACCEPTED"
    WORKFLOW_EXECUTION_UPDATE_REJECTED = "WORKFLOW_EXECUTION_UPDATE_REJECTED"
    WORKFLOW_EXECUTION_UPDATE_COMPLETED = "WORKFLOW_EXECUTION_UPDATE_COMPLETED"

    def to_status(self) -> WorkflowExecutionEventStatus:
        match self:
            case (
                WorkflowEventType.ACTIVITY_TASK_SCHEDULED
                | WorkflowEventType.START_CHILD_WORKFLOW_EXECUTION_INITIATED
                | WorkflowEventType.WORKFLOW_EXECUTION_UPDATE_ACCEPTED
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
                | WorkflowEventType.WORKFLOW_EXECUTION_UPDATE_COMPLETED
            ):
                return WorkflowExecutionEventStatus.COMPLETED
            case (
                WorkflowEventType.ACTIVITY_TASK_FAILED
                | WorkflowEventType.CHILD_WORKFLOW_EXECUTION_FAILED
                | WorkflowEventType.WORKFLOW_EXECUTION_UPDATE_REJECTED
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
    CASE = "case"

    def to_temporal_search_attr_pair(self) -> SearchAttributePair[str]:
        return TemporalSearchAttr.TRIGGER_TYPE.create_pair(self.value)


class ExecutionType(StrEnum):
    """Execution type for a workflow execution.

    Distinguishes between draft (development) and published (production) executions.
    """

    DRAFT = "draft"
    """Draft execution uses the draft workflow graph and resolves aliases from draft workflows."""

    PUBLISHED = "published"
    """Published execution uses the committed workflow definition and resolves aliases from committed workflows."""

    def to_temporal_search_attr_pair(self) -> SearchAttributePair[str]:
        return TemporalSearchAttr.EXECUTION_TYPE.create_pair(self.value)


class TemporalSearchAttr(StrEnum):
    """Temporal search attribute keys."""

    TRIGGER_TYPE = "TracecatTriggerType"
    """The `Keyword` Search Attribute for the trigger type of the workflow execution."""

    TRIGGERED_BY_USER_ID = "TracecatTriggeredByUserId"
    """The `Keyword` Search Attribute for the user ID that triggered the workflow execution."""

    WORKSPACE_ID = "TracecatWorkspaceId"
    """The `Keyword` Search Attribute for the workspace that owns the workflow execution."""

    ALIAS = "TracecatAlias"
    """The `Keyword` Search Attribute for a human-friendly workflow alias (e.g., workflow or agent slugs)."""

    EXECUTION_TYPE = "TracecatExecutionType"
    """The `Keyword` Search Attribute for the execution type (draft or published)."""

    @cached_property
    def key(self) -> SearchAttributeKey[str]:
        return SearchAttributeKey.for_keyword(self.value)

    def create_pair(self, value: str) -> SearchAttributePair[str]:
        return SearchAttributePair(
            key=self.key,
            value=value,
        )
