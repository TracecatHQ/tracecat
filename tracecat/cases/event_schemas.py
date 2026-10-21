"""Case activity event schemas: stored event variants and their read models."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import ConfigDict, Field, RootModel

from tracecat.auth.schemas import UserRead
from tracecat.cases.enums import (
    CaseEventType,
    CasePriority,
    CaseSeverity,
    CaseStatus,
    CaseTaskStatus,
)
from tracecat.cases.schemas import CaseCommentDeleteMode, CaseRef
from tracecat.core.schemas import Schema
from tracecat.identifiers.workflow import AnyWorkflowID


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


class ParentChangedEvent(CaseEventBase):
    type: Literal[CaseEventType.PARENT_CHANGED] = CaseEventType.PARENT_CHANGED
    old: CaseRef | None = None
    new: CaseRef | None = None


class SubCasesAddedEvent(CaseEventBase):
    type: Literal[CaseEventType.SUB_CASES_ADDED] = CaseEventType.SUB_CASES_ADDED
    sub_cases: list[CaseRef]


class SubCasesRemovedEvent(CaseEventBase):
    type: Literal[CaseEventType.SUB_CASES_REMOVED] = CaseEventType.SUB_CASES_REMOVED
    sub_cases: list[CaseRef]


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


class ParentChangedEventRead(CaseEventReadBase, ParentChangedEvent):
    """Event for when a case is grouped under, moved to, or removed from a parent."""


class SubCasesAddedEventRead(CaseEventReadBase, SubCasesAddedEvent):
    """Event for when cases are grouped under this case as sub-cases."""


class SubCasesRemovedEventRead(CaseEventReadBase, SubCasesRemovedEvent):
    """Event for when sub-cases are removed from this case."""


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
    | TableRowUnlinkedEvent
    | ParentChangedEvent
    | SubCasesAddedEvent
    | SubCasesRemovedEvent,
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
        | ParentChangedEventRead
        | SubCasesAddedEventRead
        | SubCasesRemovedEventRead
    ) = Field(discriminator="type")


class CaseEventsWithUsers(Schema):
    events: list[CaseEventRead] = Field(..., description="The events for the case.")
    users: list[UserRead] = Field(..., description="The users for the case.")
