from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

import sqlalchemy as sa
from pydantic import BaseModel, Field, RootModel

from tracecat import config
from tracecat.auth.models import UserRead
from tracecat.cases.constants import RESERVED_CASE_FIELDS
from tracecat.cases.enums import CaseEventType, CasePriority, CaseSeverity, CaseStatus
from tracecat.tables.enums import SqlType
from tracecat.tables.models import TableColumnCreate, TableColumnUpdate

# Case Management


class CaseReadMinimal(BaseModel):
    id: uuid.UUID
    short_id: str
    created_at: datetime
    updated_at: datetime
    summary: str
    status: CaseStatus
    priority: CasePriority
    severity: CaseSeverity
    assignee: UserRead | None = None


class CaseRead(BaseModel):
    id: uuid.UUID
    short_id: str
    created_at: datetime
    updated_at: datetime
    summary: str
    status: CaseStatus
    priority: CasePriority
    severity: CaseSeverity
    description: str
    fields: list[CaseCustomFieldRead]
    assignee: UserRead | None = None
    payload: dict[str, Any] | None


class CaseCreate(BaseModel):
    summary: str
    description: str
    status: CaseStatus
    priority: CasePriority
    severity: CaseSeverity
    fields: dict[str, Any] | None = None
    assignee_id: uuid.UUID | None = None
    payload: dict[str, Any] | None = None


class CaseUpdate(BaseModel):
    summary: str | None = None
    description: str | None = None
    status: CaseStatus | None = None
    priority: CasePriority | None = None
    severity: CaseSeverity | None = None
    fields: dict[str, Any] | None = None
    assignee_id: uuid.UUID | None = None
    payload: dict[str, Any] | None = None


# Case Fields


class CaseFieldRead(BaseModel):
    """Read model for a case field."""

    id: str
    type: SqlType
    description: str
    nullable: bool
    default: str | None
    reserved: bool

    @staticmethod
    def from_sa(
        column: sa.engine.interfaces.ReflectedColumn,
    ) -> CaseFieldRead:
        return CaseFieldRead(
            id=column["name"],
            type=SqlType(str(column["type"])),
            description=column.get("comment") or "",
            nullable=column["nullable"],
            default=column.get("default"),
            reserved=column["name"] in RESERVED_CASE_FIELDS,
        )


class CaseFieldCreate(TableColumnCreate):
    """Create a new case field."""


class CaseFieldUpdate(TableColumnUpdate):
    """Update a case field."""


class CaseCustomFieldRead(CaseFieldRead):
    value: Any


# Case Comments


class CaseCommentRead(BaseModel):
    id: uuid.UUID
    created_at: datetime
    updated_at: datetime
    content: str
    parent_id: uuid.UUID | None = None
    user: UserRead | None = None
    last_edited_at: datetime | None = None


class CaseCommentCreate(BaseModel):
    content: str = Field(..., min_length=1, max_length=5_000)
    parent_id: uuid.UUID | None = Field(default=None)


class CaseCommentUpdate(BaseModel):
    content: str | None = Field(default=None, min_length=1, max_length=5_000)
    parent_id: uuid.UUID | None = Field(default=None)


# Case Events


class CaseEventReadBase(BaseModel):
    """Base for reading events - rich user data."""

    user_id: uuid.UUID | None = Field(
        default=None, description="The user who performed the action."
    )
    created_at: datetime = Field(..., description="The timestamp of the event.")


class CaseEventBase(BaseModel):
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


class UpdatedEvent(CaseEventBase):
    type: Literal[CaseEventType.CASE_UPDATED] = CaseEventType.CASE_UPDATED
    field: Literal["summary"]
    old: str | None
    new: str | None


class FieldDiff(BaseModel):
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


class PayloadChangedEventRead(CaseEventReadBase, PayloadChangedEvent):
    """Event for when a case payload is changed."""


# Type unions
type CaseEventVariant = Annotated[
    CreatedEvent
    | ClosedEvent
    | ReopenedEvent
    | UpdatedEvent
    | StatusChangedEvent
    | PriorityChangedEvent
    | SeverityChangedEvent
    | FieldsChangedEvent
    | AssigneeChangedEvent
    | AttachmentCreatedEvent
    | AttachmentDeletedEvent
    | PayloadChangedEvent,
    Field(discriminator="type"),
]


class CaseEventRead(RootModel):
    """Base read model for all event types."""

    root: (
        CreatedEventRead
        | ClosedEventRead
        | ReopenedEventRead
        | UpdatedEventRead
        | StatusChangedEventRead
        | PriorityChangedEventRead
        | SeverityChangedEventRead
        | FieldChangedEventRead
        | AssigneeChangedEventRead
        | AttachmentCreatedEventRead
        | AttachmentDeletedEventRead
        | PayloadChangedEventRead
    ) = Field(discriminator="type")


class Change[OldType: Any, NewType: Any](BaseModel):
    field: str
    old: OldType
    new: NewType


class CaseEventsWithUsers(BaseModel):
    events: list[CaseEventRead] = Field(..., description="The events for the case.")
    users: list[UserRead] = Field(..., description="The users for the case.")


class CaseAttachmentCreate(BaseModel):
    """Model for creating a case attachment."""

    file_name: str = Field(
        ...,
        max_length=config.TRACECAT__MAX_ATTACHMENT_FILENAME_LENGTH,
        description="Original filename",
    )
    content_type: str = Field(..., max_length=100, description="MIME type of the file")
    size: int = Field(
        ...,
        gt=0,
        le=config.TRACECAT__MAX_ATTACHMENT_SIZE_BYTES,
        description="File size in bytes",
    )
    content: bytes = Field(..., description="File content")


class CaseAttachmentRead(BaseModel):
    """Model for reading a case attachment."""

    id: uuid.UUID
    case_id: uuid.UUID
    file_id: uuid.UUID
    file_name: str
    content_type: str
    size: int
    sha256: str
    created_at: datetime
    updated_at: datetime
    creator_id: uuid.UUID | None = None
    is_deleted: bool = False


class CaseAttachmentDownloadResponse(BaseModel):
    """Model for attachment download URL response."""

    download_url: str = Field(..., description="Pre-signed download URL")
    file_name: str = Field(..., description="Original filename")
    content_type: str = Field(..., description="MIME type of the file")


class CaseAttachmentDownloadData(BaseModel):
    file_name: str
    content_type: str
    content_base64: str


class FileRead(BaseModel):
    """Model for reading file metadata."""

    id: uuid.UUID
    sha256: str
    name: str
    content_type: str
    size: int
    creator_id: uuid.UUID | None
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None = None
    is_deleted: bool
