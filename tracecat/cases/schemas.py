from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any, Literal

import sqlalchemy as sa
from pydantic import ConfigDict, Field, RootModel, field_validator, model_validator

from tracecat.auth.schemas import UserRead
from tracecat.cases.constants import RESERVED_CASE_FIELDS
from tracecat.cases.dropdowns.schemas import (
    CaseDropdownValueInput,
    CaseDropdownValueRead,
)
from tracecat.cases.enums import (
    CaseEventType,
    CaseFieldKind,
    CaseFieldReadType,
    CasePriority,
    CaseSeverity,
    CaseStatus,
    CaseTaskStatus,
)
from tracecat.cases.rows.schemas import CaseTableRowRead
from tracecat.cases.tags.schemas import CaseTagRead
from tracecat.core.schemas import Schema
from tracecat.custom_fields.schemas import (
    CustomFieldCreate,
    CustomFieldUpdate,
)
from tracecat.identifiers.workflow import (
    AnyWorkflowID,
    WorkflowIDShort,
    WorkflowUUID,
)
from tracecat.tables.common import parse_postgres_default
from tracecat.tables.enums import SqlType


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
    rows: list[CaseTableRowRead] = Field(default_factory=list)
    num_tasks_completed: int = Field(default=0)
    num_tasks_total: int = Field(default=0)


class CaseStatusGroupCounts(Schema):
    new: int = 0
    in_progress: int = 0
    on_hold: int = 0
    resolved: int = 0
    closed: int = 0
    unknown: int = 0
    other: int = 0


class CaseSearchAggregateRead(Schema):
    total: int
    status_groups: CaseStatusGroupCounts


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
    rows: list[CaseTableRowRead] = Field(default_factory=list)


class CaseCreate(Schema):
    summary: str
    description: str
    status: CaseStatus
    priority: CasePriority
    severity: CaseSeverity
    fields: dict[str, Any] | None = None
    # Write payload field for persisted per-case dropdown selections.
    # Search filters use the `dropdown` query parameter in routes.
    dropdown_values: list[CaseDropdownValueInput] | None = None
    assignee_id: uuid.UUID | None = None
    payload: dict[str, Any] | None = None


class CaseUpdate(Schema):
    summary: str | None = None
    description: str | None = None
    status: CaseStatus | None = None
    priority: CasePriority | None = None
    severity: CaseSeverity | None = None
    fields: dict[str, Any] | None = None
    # Same persisted write payload shape as create; values here set/clear
    # dropdown selections for this case.
    dropdown_values: list[CaseDropdownValueInput] | None = None
    assignee_id: uuid.UUID | None = None
    payload: dict[str, Any] | None = None


# Case Fields


def _normalize_case_field_read_type(raw_type: Any) -> CaseFieldReadType:
    if isinstance(raw_type, CaseFieldReadType):
        return raw_type
    if isinstance(raw_type, SqlType):
        return CaseFieldReadType(raw_type.value)

    type_str: str
    if isinstance(raw_type, str):
        type_str = raw_type.upper()
    else:
        type_str = str(raw_type).upper()
        if hasattr(raw_type, "timezone"):
            type_str = (
                "TIMESTAMP WITH TIME ZONE"
                if getattr(raw_type, "timezone", False)
                else "TIMESTAMP WITHOUT TIME ZONE"
            )

    if type_str == "BIGINT":
        return CaseFieldReadType.INTEGER
    if type_str == "TIMESTAMP WITH TIME ZONE":
        return CaseFieldReadType.TIMESTAMPTZ
    return CaseFieldReadType(type_str)


class CaseFieldReadMinimal(Schema):
    """Minimal read model for a case field."""

    id: str
    type: CaseFieldReadType
    description: str
    nullable: bool
    default: str | None
    reserved: bool
    options: list[str] | None = None
    kind: CaseFieldKind | None = Field(default=None)
    required_on_closure: bool = Field(default=False)

    @classmethod
    def from_sa(
        cls,
        column: sa.engine.interfaces.ReflectedColumn,
        *,
        field_schema: dict[str, Any] | None = None,
    ) -> CaseFieldReadMinimal:
        """Create a CaseFieldReadMinimal from a SQLAlchemy reflected column.

        Args:
            column: The reflected column metadata from SQLAlchemy.
            field_schema: Optional schema metadata for the field.

        Returns:
            A CaseFieldReadMinimal instance populated from the column data.
        """
        kind: CaseFieldKind | None = None
        required_on_closure = False
        options: list[str] | None = None
        if field_schema and (meta := field_schema.get(column["name"])):
            read_type = CaseFieldReadType(meta["type"])
            options = meta.get("options")
            if kind_str := meta.get("kind"):
                kind = CaseFieldKind(kind_str)
            if meta.get("required_on_closure"):
                required_on_closure = True
        else:
            read_type = _normalize_case_field_read_type(column["type"])
        return cls.model_validate(
            {
                "id": column["name"],
                "type": read_type,
                "description": column.get("comment") or "",
                "nullable": column["nullable"],
                "default": parse_postgres_default(column.get("default")),
                "reserved": column["name"] in RESERVED_CASE_FIELDS,
                "options": options,
                "kind": kind,
                "required_on_closure": required_on_closure,
            }
        )


class CaseFieldCreate(CustomFieldCreate):
    """Create a new case field."""

    kind: CaseFieldKind | None = Field(default=None)
    required_on_closure: bool = Field(default=False)

    @model_validator(mode="after")
    def validate_kind_type_pair(self) -> CaseFieldCreate:
        """Validate the semantic kind against the storage type."""
        if self.kind is None:
            return self

        if self.kind is CaseFieldKind.LONG_TEXT and self.type is not SqlType.TEXT:
            raise ValueError("Case field kind LONG_TEXT requires type TEXT")
        if self.kind is CaseFieldKind.URL and self.type is not SqlType.JSONB:
            raise ValueError("Case field kind URL requires type JSONB")
        return self


class CaseFieldUpdate(CustomFieldUpdate):
    """Update a case field."""

    required_on_closure: bool | None = Field(default=None)

    @model_validator(mode="before")
    @classmethod
    def reject_kind_updates(cls, data: Any) -> Any:
        """Reject create-only kind updates."""
        if isinstance(data, dict) and "kind" in data:
            raise ValueError("Case field kind can only be set when creating a field")
        return data


class CaseFieldRead(CaseFieldReadMinimal):
    """Read model for a case field."""

    value: Any


# Case Comments


class CaseCommentWorkflowStatus(StrEnum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class CaseCommentWorkflowRead(Schema):
    workflow_id: uuid.UUID | None = None
    title: str
    alias: str | None = None
    wf_exec_id: str | None = None
    status: CaseCommentWorkflowStatus


class CaseCommentRead(Schema):
    id: uuid.UUID
    created_at: datetime
    updated_at: datetime
    content: str
    parent_id: uuid.UUID | None = None
    workflow: CaseCommentWorkflowRead | None = None
    user: UserRead | None = None
    last_edited_at: datetime | None = None
    deleted_at: datetime | None = None
    is_deleted: bool = Field(default=False)


class CaseCommentThreadRead(Schema):
    comment: CaseCommentRead
    replies: list[CaseCommentRead] = Field(default_factory=list)
    reply_count: int = Field(default=0)
    last_activity_at: datetime


class CaseCommentCreate(Schema):
    content: str = Field(default=..., min_length=1, max_length=25_000)
    parent_id: uuid.UUID | None = Field(default=None)
    workflow_id: AnyWorkflowID | None = Field(default=None)

    @field_validator("content")
    @classmethod
    def validate_content(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("Comment content cannot be blank")
        return stripped


class CaseCommentUpdate(Schema):
    content: str | None = Field(default=None, min_length=1, max_length=25_000)
    parent_id: uuid.UUID | None = Field(default=None)

    @field_validator("content")
    @classmethod
    def validate_content(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        if not stripped:
            raise ValueError("Comment content cannot be blank")
        return stripped


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


type CaseCommentDeleteMode = Literal["soft", "hard"]


class CaseCommentEventBase(CaseEventBase):
    comment_id: uuid.UUID
    parent_id: uuid.UUID | None = None
    thread_root_id: uuid.UUID


class CommentCreatedEvent(CaseCommentEventBase):
    type: Literal[CaseEventType.COMMENT_CREATED] = CaseEventType.COMMENT_CREATED


class CommentUpdatedEvent(CaseCommentEventBase):
    type: Literal[CaseEventType.COMMENT_UPDATED] = CaseEventType.COMMENT_UPDATED


class CommentDeletedEvent(CaseCommentEventBase):
    type: Literal[CaseEventType.COMMENT_DELETED] = CaseEventType.COMMENT_DELETED
    delete_mode: CaseCommentDeleteMode


class CommentReplyCreatedEvent(CaseCommentEventBase):
    type: Literal[CaseEventType.COMMENT_REPLY_CREATED] = (
        CaseEventType.COMMENT_REPLY_CREATED
    )


class CommentReplyUpdatedEvent(CaseCommentEventBase):
    type: Literal[CaseEventType.COMMENT_REPLY_UPDATED] = (
        CaseEventType.COMMENT_REPLY_UPDATED
    )


class CommentReplyDeletedEvent(CaseCommentEventBase):
    type: Literal[CaseEventType.COMMENT_REPLY_DELETED] = (
        CaseEventType.COMMENT_REPLY_DELETED
    )
    delete_mode: CaseCommentDeleteMode


class TableRowLinkedEvent(CaseEventBase):
    type: Literal[CaseEventType.TABLE_ROW_LINKED] = CaseEventType.TABLE_ROW_LINKED
    table_id: uuid.UUID
    table_name: str | None = None
    row_id: uuid.UUID


class TableRowUnlinkedEvent(CaseEventBase):
    type: Literal[CaseEventType.TABLE_ROW_UNLINKED] = CaseEventType.TABLE_ROW_UNLINKED
    table_id: uuid.UUID
    table_name: str | None = None
    row_id: uuid.UUID


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


class CommentCreatedEventRead(CaseEventReadBase, CommentCreatedEvent):
    """Event for when a top-level comment is created."""


class CommentUpdatedEventRead(CaseEventReadBase, CommentUpdatedEvent):
    """Event for when a top-level comment is updated."""


class CommentDeletedEventRead(CaseEventReadBase, CommentDeletedEvent):
    """Event for when a top-level comment is deleted."""


class CommentReplyCreatedEventRead(CaseEventReadBase, CommentReplyCreatedEvent):
    """Event for when a reply is created."""


class CommentReplyUpdatedEventRead(CaseEventReadBase, CommentReplyUpdatedEvent):
    """Event for when a reply is updated."""


class CommentReplyDeletedEventRead(CaseEventReadBase, CommentReplyDeletedEvent):
    """Event for when a reply is deleted."""


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


class TableRowLinkedEventRead(CaseEventReadBase, TableRowLinkedEvent):
    """Event for when a table row is linked to a case."""


class TableRowUnlinkedEventRead(CaseEventReadBase, TableRowUnlinkedEvent):
    """Event for when a table row is unlinked from a case."""


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
    | CommentCreatedEvent
    | CommentUpdatedEvent
    | CommentDeletedEvent
    | CommentReplyCreatedEvent
    | CommentReplyUpdatedEvent
    | CommentReplyDeletedEvent
    | TaskCreatedEvent
    | TaskStatusChangedEvent
    | TaskDeletedEvent
    | TaskAssigneeChangedEvent
    | TaskPriorityChangedEvent
    | TaskWorkflowChangedEvent
    | DropdownValueChangedEvent
    | TableRowLinkedEvent
    | TableRowUnlinkedEvent,
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
        | CommentCreatedEventRead
        | CommentUpdatedEventRead
        | CommentDeletedEventRead
        | CommentReplyCreatedEventRead
        | CommentReplyUpdatedEventRead
        | CommentReplyDeletedEventRead
        | TaskCreatedEventRead
        | TaskStatusChangedEventRead
        | TaskPriorityChangedEventRead
        | TaskWorkflowChangedEventRead
        | TaskDeletedEventRead
        | TaskAssigneeChangedEventRead
        | DropdownValueChangedEventRead
        | TableRowLinkedEventRead
        | TableRowUnlinkedEventRead
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
    workflow_id: uuid.UUID | None = None
    workflow_title: str | None = None
    workflow_alias: str | None = None
    workflow_wf_exec_id: str | None = None
    workflow_status: CaseCommentWorkflowStatus | None = None
    case_id: uuid.UUID
    workspace_id: uuid.UUID
    user_id: uuid.UUID | None = None
    last_edited_at: datetime | None = None
    deleted_at: datetime | None = None
