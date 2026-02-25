from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any, Literal

import sqlalchemy as sa
from pydantic import ConfigDict, Field, RootModel, field_validator, model_validator

from tracecat import config
from tracecat.auth.schemas import UserRead
from tracecat.cases.constants import RESERVED_CASE_FIELDS
from tracecat.cases.dropdowns.schemas import CaseDropdownValueRead
from tracecat.cases.enums import (
    CaseEventType,
    CasePriority,
    CaseSeverity,
    CaseStatus,
    CaseTaskStatus,
)
from tracecat.cases.tags.schemas import CaseTagRead
from tracecat.core.schemas import Schema
from tracecat.custom_fields.schemas import (
    CustomFieldCreate,
    CustomFieldRead,
    CustomFieldUpdate,
)
from tracecat.identifiers.workflow import (
    AnyWorkflowID,
    WorkflowIDShort,
    WorkflowUUID,
)
from tracecat.pagination import CursorPaginatedResponse


class CaseReadMinimal(Schema):
    id: uuid.UUID
    short_id: str
    created_at: datetime
    updated_at: datetime
    summary: str
    status: CaseStatus
    priority: CasePriority
    severity: CaseSeverity
    assignee: UserRead | None = None
    tags: list[CaseTagRead] = Field(default_factory=list)
    dropdown_values: list[CaseDropdownValueRead]
    num_tasks_completed: int = Field(default=0)
    num_tasks_total: int = Field(default=0)


class CaseStatusGroupCounts(Schema):
    new: int = 0
    in_progress: int = 0
    on_hold: int = 0
    resolved: int = 0
    other: int = 0


class CaseSearchAggregateRead(Schema):
    total: int
    status_groups: CaseStatusGroupCounts


class CaseSearchAggregate(StrEnum):
    SUM = "sum"
    MIN = "min"
    MAX = "max"
    MEAN = "mean"
    MEDIAN = "median"
    MODE = "mode"
    N_UNIQUE = "n_unique"
    VALUE_COUNTS = "value_counts"


type CaseSearchGroupBy = Literal[
    "status",
    "priority",
    "severity",
    "assignee_id",
    "created_at",
    "updated_at",
]


type CaseSearchAggField = Literal[
    "case_number",
    "status",
    "priority",
    "severity",
    "assignee_id",
    "created_at",
    "updated_at",
]


type CaseSearchOrderBy = Literal[
    "created_at",
    "updated_at",
    "priority",
    "severity",
    "status",
    "tasks",
]


type CaseSearchAggregationScalar = (
    int | float | str | bool | datetime | uuid.UUID | None
)


class CaseSearchRequest(Schema):
    limit: int = Field(
        default=config.TRACECAT__LIMIT_DEFAULT,
        ge=config.TRACECAT__LIMIT_MIN,
        le=config.TRACECAT__LIMIT_CURSOR_MAX,
        description="Maximum items per page",
    )
    cursor: str | None = Field(default=None, description="Cursor for pagination")
    reverse: bool = Field(default=False, description="Reverse pagination direction")
    search_term: str | None = Field(
        default=None,
        description="Text to search for in case summary, description, or short ID",
    )
    status: list[CaseStatus] | None = Field(
        default=None, description="Filter by case status"
    )
    priority: list[CasePriority] | None = Field(
        default=None, description="Filter by case priority"
    )
    severity: list[CaseSeverity] | None = Field(
        default=None, description="Filter by case severity"
    )
    tags: list[str] | None = Field(
        default=None, description="Filter by tag IDs or slugs (AND logic)"
    )
    dropdown: list[str] | None = Field(
        default=None,
        description="Filter by dropdown values. Format: definition_ref:option_ref (AND across definitions, OR within)",
    )
    start_time: datetime | None = Field(
        default=None, description="Return cases created at or after this timestamp"
    )
    end_time: datetime | None = Field(
        default=None, description="Return cases created at or before this timestamp"
    )
    updated_after: datetime | None = Field(
        default=None, description="Return cases updated at or after this timestamp"
    )
    updated_before: datetime | None = Field(
        default=None, description="Return cases updated at or before this timestamp"
    )
    assignee_id: list[str] | None = Field(
        default=None, description="Filter by assignee ID or 'unassigned'"
    )
    order_by: CaseSearchOrderBy | None = Field(
        default=None,
        description="Column name to order by (e.g. created_at, updated_at, priority, severity, status, tasks). Default: created_at",
    )
    sort: Literal["asc", "desc"] | None = Field(
        default=None, description="Direction to sort (asc or desc)"
    )
    group_by: CaseSearchGroupBy | None = Field(
        default=None, description="Field to group aggregation results by"
    )
    agg: CaseSearchAggregate | None = Field(
        default=None,
        description="Aggregation operation. Supported values: sum, min, max, mean, median, mode, n_unique, value_counts",
    )
    agg_field: CaseSearchAggField | None = Field(
        default=None,
        description="Field to aggregate. Optional for sum (defaults to row count) and value_counts.",
    )

    @model_validator(mode="after")
    def validate_aggregation(self) -> CaseSearchRequest:
        has_group_by = self.group_by is not None
        has_agg = self.agg is not None
        has_agg_field = self.agg_field is not None

        if not has_agg and (has_group_by or has_agg_field):
            raise ValueError("group_by and agg_field require agg")
        if (
            has_agg
            and self.agg is CaseSearchAggregate.VALUE_COUNTS
            and not has_group_by
        ):
            raise ValueError("value_counts aggregation requires group_by")

        return self


class CaseSearchAggregationBucket(Schema):
    group: CaseSearchAggregationScalar
    value: CaseSearchAggregationScalar


class CaseSearchAggregationRead(Schema):
    agg: CaseSearchAggregate
    group_by: CaseSearchGroupBy | None = None
    agg_field: CaseSearchAggField | None = None
    value: CaseSearchAggregationScalar = None
    buckets: list[CaseSearchAggregationBucket] = Field(default_factory=list)


class CaseSearchResponse(CursorPaginatedResponse[CaseReadMinimal]):
    aggregation: CaseSearchAggregationRead | None = None


class CaseRead(Schema):
    id: uuid.UUID
    short_id: str
    created_at: datetime
    updated_at: datetime
    summary: str
    status: CaseStatus
    priority: CasePriority
    severity: CaseSeverity
    description: str
    fields: list[CaseFieldRead]
    assignee: UserRead | None = None
    payload: dict[str, Any] | None
    tags: list[CaseTagRead] = Field(default_factory=list)
    dropdown_values: list[CaseDropdownValueRead]


class CaseCreate(Schema):
    summary: str
    description: str
    status: CaseStatus
    priority: CasePriority
    severity: CaseSeverity
    fields: dict[str, Any] | None = None
    assignee_id: uuid.UUID | None = None
    payload: dict[str, Any] | None = None


class CaseUpdate(Schema):
    summary: str | None = None
    description: str | None = None
    status: CaseStatus | None = None
    priority: CasePriority | None = None
    severity: CaseSeverity | None = None
    fields: dict[str, Any] | None = None
    assignee_id: uuid.UUID | None = None
    payload: dict[str, Any] | None = None


# Case Fields


class CaseFieldReadMinimal(CustomFieldRead):
    """Minimal read model for a case field."""

    @classmethod
    def from_sa(
        cls,
        column: sa.engine.interfaces.ReflectedColumn,
        *,
        reserved_fields: set[str] | None = None,  # noqa: ARG003 - Ignored; case fields always use RESERVED_CASE_FIELDS
        field_schema: dict[str, Any] | None = None,
    ) -> CaseFieldReadMinimal:
        """Create a CaseFieldReadMinimal from a SQLAlchemy reflected column.

        Args:
            column: The reflected column metadata from SQLAlchemy.
            reserved_fields: Ignored. Case fields always use RESERVED_CASE_FIELDS.
            field_schema: Optional schema metadata for the field.

        Returns:
            A CaseFieldReadMinimal instance populated from the column data.
        """
        return cls.model_validate(
            super().from_sa(
                column,
                reserved_fields=set(RESERVED_CASE_FIELDS),
                field_schema=field_schema,
            )
        )


class CaseFieldCreate(CustomFieldCreate):
    """Create a new case field."""


class CaseFieldUpdate(CustomFieldUpdate):
    """Update a case field."""


class CaseFieldRead(CaseFieldReadMinimal):
    """Read model for a case field."""

    value: Any


# Case Comments


class CaseCommentRead(Schema):
    id: uuid.UUID
    created_at: datetime
    updated_at: datetime
    content: str
    parent_id: uuid.UUID | None = None
    user: UserRead | None = None
    last_edited_at: datetime | None = None


class CaseCommentCreate(Schema):
    content: str = Field(..., min_length=1, max_length=5_000)
    parent_id: uuid.UUID | None = Field(default=None)


class CaseCommentUpdate(Schema):
    content: str | None = Field(default=None, min_length=1, max_length=5_000)
    parent_id: uuid.UUID | None = Field(default=None)


# Case Tasks


class CaseTaskRead(Schema):
    id: uuid.UUID
    created_at: datetime
    updated_at: datetime
    case_id: uuid.UUID
    title: str
    description: str | None
    priority: CasePriority
    status: CaseTaskStatus
    assignee: UserRead | None = None
    workflow_id: WorkflowIDShort | None
    default_trigger_values: dict[str, Any] | None = None

    @field_validator("workflow_id", mode="before")
    @classmethod
    def convert_workflow_id(cls, v: AnyWorkflowID | None) -> WorkflowIDShort | None:
        """Convert any workflow ID format to short form."""
        if v is None:
            return None
        return WorkflowUUID.new(v).short()


class CaseTaskCreate(Schema):
    title: str = Field(..., min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=1000)
    priority: CasePriority = Field(default=CasePriority.UNKNOWN)
    status: CaseTaskStatus = Field(default=CaseTaskStatus.TODO)
    assignee_id: uuid.UUID | None = Field(default=None)
    workflow_id: AnyWorkflowID | None = Field(default=None)
    default_trigger_values: dict[str, Any] | None = Field(default=None)


class CaseTaskUpdate(Schema):
    title: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=1000)
    priority: CasePriority | None = Field(default=None)
    status: CaseTaskStatus | None = Field(default=None)
    assignee_id: uuid.UUID | None = Field(default=None)
    workflow_id: AnyWorkflowID | None = Field(default=None)
    default_trigger_values: dict[str, Any] | None = Field(default=None)


# Case Events


class CaseEventReadBase(Schema):
    """Base for reading events - rich user data."""

    user_id: uuid.UUID | None = Field(
        default=None, description="The user who performed the action."
    )
    created_at: datetime = Field(..., description="The timestamp of the event.")


class CaseEventBase(Schema):
    """Base for all case events."""

    wf_exec_id: str | None = Field(
        default=None,
        description="The execution ID of the workflow that triggered the event.",
    )


# Base Models (contains the tagged union data)
class CreatedEvent(CaseEventBase):
    type: Literal[CaseEventType.CASE_CREATED] = CaseEventType.CASE_CREATED


class StatusChangedEvent(CaseEventBase):
    type: Literal[CaseEventType.STATUS_CHANGED] = CaseEventType.STATUS_CHANGED
    old: CaseStatus
    new: CaseStatus


class PriorityChangedEvent(CaseEventBase):
    type: Literal[CaseEventType.PRIORITY_CHANGED] = CaseEventType.PRIORITY_CHANGED
    old: CasePriority
    new: CasePriority


class SeverityChangedEvent(CaseEventBase):
    type: Literal[CaseEventType.SEVERITY_CHANGED] = CaseEventType.SEVERITY_CHANGED
    old: CaseSeverity
    new: CaseSeverity


class ClosedEvent(CaseEventBase):
    type: Literal[CaseEventType.CASE_CLOSED] = CaseEventType.CASE_CLOSED
    old: CaseStatus
    new: CaseStatus


class ReopenedEvent(CaseEventBase):
    type: Literal[CaseEventType.CASE_REOPENED] = CaseEventType.CASE_REOPENED
    old: CaseStatus
    new: CaseStatus


class CaseViewedEvent(CaseEventBase):
    type: Literal[CaseEventType.CASE_VIEWED] = CaseEventType.CASE_VIEWED


class UpdatedEvent(CaseEventBase):
    type: Literal[CaseEventType.CASE_UPDATED] = CaseEventType.CASE_UPDATED
    field: Literal["summary"]
    old: str | None
    new: str | None


class FieldDiff(Schema):
    field: str
    old: Any
    new: Any


class FieldsChangedEvent(CaseEventBase):
    type: Literal[CaseEventType.FIELDS_CHANGED] = CaseEventType.FIELDS_CHANGED
    changes: list[FieldDiff]


class AssigneeChangedEvent(CaseEventBase):
    type: Literal[CaseEventType.ASSIGNEE_CHANGED] = CaseEventType.ASSIGNEE_CHANGED
    old: uuid.UUID | None
    new: uuid.UUID | None


class PayloadChangedEvent(CaseEventBase):
    type: Literal[CaseEventType.PAYLOAD_CHANGED] = CaseEventType.PAYLOAD_CHANGED


# Read Models (for API responses) - keep the original names for backward compatibility
class CreatedEventRead(CaseEventReadBase, CreatedEvent):
    """Event for when a case is created."""


class ClosedEventRead(CaseEventReadBase, ClosedEvent):
    """Event for when a case is closed."""


class ReopenedEventRead(CaseEventReadBase, ReopenedEvent):
    """Event for when a case is reopened."""


class CaseViewedEventRead(CaseEventReadBase, CaseViewedEvent):
    """Event for when a case is viewed."""


class UpdatedEventRead(CaseEventReadBase, UpdatedEvent):
    """Event for when a case is updated."""


class StatusChangedEventRead(CaseEventReadBase, StatusChangedEvent):
    """Event for when a case status is changed."""


class PriorityChangedEventRead(CaseEventReadBase, PriorityChangedEvent):
    """Event for when a case priority is changed."""


class SeverityChangedEventRead(CaseEventReadBase, SeverityChangedEvent):
    """Event for when a case severity is changed."""


class FieldChangedEventRead(CaseEventReadBase, FieldsChangedEvent):
    """Event for when a case field is changed."""


class AssigneeChangedEventRead(CaseEventReadBase, AssigneeChangedEvent):
    """Event for when a case assignee is changed."""


class PayloadChangedEventRead(CaseEventReadBase, PayloadChangedEvent):
    """Event for when a case payload is changed."""


class AttachmentCreatedEvent(CaseEventBase):
    type: Literal[CaseEventType.ATTACHMENT_CREATED] = CaseEventType.ATTACHMENT_CREATED
    attachment_id: uuid.UUID
    file_name: str
    content_type: str
    size: int


class AttachmentDeletedEvent(CaseEventBase):
    type: Literal[CaseEventType.ATTACHMENT_DELETED] = CaseEventType.ATTACHMENT_DELETED
    attachment_id: uuid.UUID
    file_name: str


class AttachmentCreatedEventRead(CaseEventReadBase, AttachmentCreatedEvent):
    """Event for when an attachment is created for a case."""


class AttachmentDeletedEventRead(CaseEventReadBase, AttachmentDeletedEvent):
    """Event for when an attachment is deleted from a case."""


class TagAddedEvent(CaseEventBase):
    type: Literal[CaseEventType.TAG_ADDED] = CaseEventType.TAG_ADDED
    tag_id: uuid.UUID
    tag_ref: str
    tag_name: str


class TagRemovedEvent(CaseEventBase):
    type: Literal[CaseEventType.TAG_REMOVED] = CaseEventType.TAG_REMOVED
    tag_id: uuid.UUID
    tag_ref: str
    tag_name: str


class TagAddedEventRead(CaseEventReadBase, TagAddedEvent):
    """Event for when a tag is added to a case."""


class TagRemovedEventRead(CaseEventReadBase, TagRemovedEvent):
    """Event for when a tag is removed from a case."""


# Dropdown Events


class DropdownValueChangedEvent(CaseEventBase):
    type: Literal[CaseEventType.DROPDOWN_VALUE_CHANGED] = (
        CaseEventType.DROPDOWN_VALUE_CHANGED
    )
    definition_id: str
    definition_ref: str
    definition_name: str
    old_option_id: str | None = None
    old_option_label: str | None = None
    new_option_id: str | None = None
    new_option_label: str | None = None


class DropdownValueChangedEventRead(CaseEventReadBase, DropdownValueChangedEvent):
    """Event for when a case dropdown value is changed."""


# Task Events


class TaskCreatedEvent(CaseEventBase):
    type: Literal[CaseEventType.TASK_CREATED] = CaseEventType.TASK_CREATED
    task_id: uuid.UUID
    title: str


class TaskDeletedEvent(CaseEventBase):
    type: Literal[CaseEventType.TASK_DELETED] = CaseEventType.TASK_DELETED
    task_id: uuid.UUID
    title: str | None = None


class TaskAssigneeChangedEvent(CaseEventBase):
    type: Literal[CaseEventType.TASK_ASSIGNEE_CHANGED] = (
        CaseEventType.TASK_ASSIGNEE_CHANGED
    )
    task_id: uuid.UUID
    title: str
    old: uuid.UUID | None
    new: uuid.UUID | None


class TaskStatusChangedEvent(CaseEventBase):
    type: Literal[CaseEventType.TASK_STATUS_CHANGED] = CaseEventType.TASK_STATUS_CHANGED
    task_id: uuid.UUID
    title: str
    old: CaseTaskStatus
    new: CaseTaskStatus


class TaskPriorityChangedEvent(CaseEventBase):
    type: Literal[CaseEventType.TASK_PRIORITY_CHANGED] = (
        CaseEventType.TASK_PRIORITY_CHANGED
    )
    task_id: uuid.UUID
    title: str
    old: CasePriority
    new: CasePriority


class TaskWorkflowChangedEvent(CaseEventBase):
    type: Literal[CaseEventType.TASK_WORKFLOW_CHANGED] = (
        CaseEventType.TASK_WORKFLOW_CHANGED
    )
    task_id: uuid.UUID
    title: str
    old: AnyWorkflowID | None
    new: AnyWorkflowID | None


class TaskCreatedEventRead(CaseEventReadBase, TaskCreatedEvent):
    """Event for when a task is created for a case."""


class TaskDeletedEventRead(CaseEventReadBase, TaskDeletedEvent):
    """Event for when a task is deleted for a case."""


class TaskAssigneeChangedEventRead(CaseEventReadBase, TaskAssigneeChangedEvent):
    """Event for when a task assignee is changed."""


class TaskStatusChangedEventRead(CaseEventReadBase, TaskStatusChangedEvent):
    """Event for when a task status is changed."""


class TaskPriorityChangedEventRead(CaseEventReadBase, TaskPriorityChangedEvent):
    """Event for when a task priority is changed."""


class TaskWorkflowChangedEventRead(CaseEventReadBase, TaskWorkflowChangedEvent):
    """Event for when a task workflow is changed."""


# Type unions
type CaseEventVariant = Annotated[
    CreatedEvent
    | ClosedEvent
    | ReopenedEvent
    | CaseViewedEvent
    | UpdatedEvent
    | StatusChangedEvent
    | PriorityChangedEvent
    | SeverityChangedEvent
    | FieldsChangedEvent
    | AssigneeChangedEvent
    | AttachmentCreatedEvent
    | AttachmentDeletedEvent
    | TagAddedEvent
    | TagRemovedEvent
    | PayloadChangedEvent
    | TaskCreatedEvent
    | TaskStatusChangedEvent
    | TaskDeletedEvent
    | TaskAssigneeChangedEvent
    | TaskPriorityChangedEvent
    | TaskWorkflowChangedEvent
    | DropdownValueChangedEvent,
    Field(discriminator="type"),
]


class CaseEventRead(RootModel):
    """Base read model for all event types."""

    model_config = ConfigDict(from_attributes=True)
    root: (
        CreatedEventRead
        | ClosedEventRead
        | ReopenedEventRead
        | CaseViewedEventRead
        | UpdatedEventRead
        | StatusChangedEventRead
        | PriorityChangedEventRead
        | SeverityChangedEventRead
        | FieldChangedEventRead
        | AssigneeChangedEventRead
        | AttachmentCreatedEventRead
        | AttachmentDeletedEventRead
        | TagAddedEventRead
        | TagRemovedEventRead
        | PayloadChangedEventRead
        | TaskCreatedEventRead
        | TaskStatusChangedEventRead
        | TaskPriorityChangedEventRead
        | TaskWorkflowChangedEventRead
        | TaskDeletedEventRead
        | TaskAssigneeChangedEventRead
        | DropdownValueChangedEventRead
    ) = Field(discriminator="type")


class Change[OldType: Any, NewType: Any](Schema):
    field: str
    old: OldType
    new: NewType


class CaseEventsWithUsers(Schema):
    events: list[CaseEventRead] = Field(..., description="The events for the case.")
    users: list[UserRead] = Field(..., description="The users for the case.")


# Internal


class InternalCaseData(Schema):
    """Case data matching the Case SQLAlchemy model's to_dict() output.

    This is the raw database representation used by UDFs for create/update operations.
    """

    id: uuid.UUID
    case_number: int
    summary: str
    description: str
    priority: CasePriority
    severity: CaseSeverity
    status: CaseStatus
    payload: dict[str, Any] | None
    assignee_id: uuid.UUID | None
    workspace_id: uuid.UUID
    created_at: datetime
    updated_at: datetime


class InternalCaseCommentData(Schema):
    """Comment data matching the CaseComment SQLAlchemy model's to_dict() output.

    This is the raw database representation used by UDFs for create/update operations.
    """

    id: uuid.UUID
    created_at: datetime
    updated_at: datetime
    content: str
    parent_id: uuid.UUID | None = None
    case_id: uuid.UUID
    workspace_id: uuid.UUID
    user_id: uuid.UUID | None = None
    last_edited_at: datetime | None = None
