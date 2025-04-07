from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

import sqlalchemy as sa
from pydantic import BaseModel, Field, TypeAdapter

from tracecat.cases.constants import RESERVED_CASE_FIELDS
from tracecat.cases.enums import (
    CaseActivityType,
    CaseEventType,
    CasePriority,
    CaseSeverity,
    CaseStatus,
)
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


class CaseRead(BaseModel):
    id: uuid.UUID
    short_id: str
    created_at: datetime
    updated_at: datetime
    summary: str
    status: CaseStatus
    priority: CasePriority
    severity: CaseSeverity
    # Details
    description: str
    # Custom fields
    fields: list[CaseCustomFieldRead] | None = None


class CaseCreate(BaseModel):
    summary: str
    description: str
    status: CaseStatus
    priority: CasePriority
    severity: CaseSeverity
    fields: dict[str, Any] | None = None


class CaseUpdate(BaseModel):
    summary: str | None = None
    description: str | None = None
    status: CaseStatus | None = None
    priority: CasePriority | None = None
    severity: CaseSeverity | None = None
    fields: dict[str, Any] | None = None


# Case Category and Fields

# Case Activity


class BaseActivity(BaseModel):
    id: uuid.UUID
    created_at: datetime
    user_id: uuid.UUID


class CommentActivity(BaseActivity):
    type: Literal[CaseActivityType.COMMENT]
    updated_at: datetime
    parent_comment_id: uuid.UUID | None = None
    content: str


class EventActivity(BaseActivity):
    type: Literal[CaseActivityType.EVENT]
    event: CaseEvent


CaseActivity = Annotated[
    CommentActivity | EventActivity,
    Field(discriminator="type"),
]
CaseActivityValidator: TypeAdapter[CaseActivity] = TypeAdapter(CaseActivity)

# Case Comments


class CommentCreate(BaseModel):
    content: str


class CommentUpdate(BaseModel):
    content: str


# Events
# We'll use these models to enforce frontend types
# Events are system generated.


class CommentCreateEvent(BaseModel):
    """When a user creates a comment on a case."""

    type: Literal[CaseEventType.COMMENT_CREATE]
    content: str


class CommentUpdateEvent(BaseModel):
    """When a user updates a comment on a case."""

    type: Literal[CaseEventType.COMMENT_UPDATE]
    content: str


class CommentDeleteEvent(BaseModel):
    """When a user deletes a comment on a case."""

    type: Literal[CaseEventType.COMMENT_DELETE]


class StatusUpdateEvent(BaseModel):
    """When a user updates the status of a case."""

    type: Literal[CaseEventType.STATUS_UPDATE]
    status: CaseStatus


class PriorityUpdateEvent(BaseModel):
    """When a user updates the priority of a case."""

    type: Literal[CaseEventType.PRIORITY_UPDATE]
    priority: CasePriority


class SeverityUpdateEvent(BaseModel):
    """When a user updates the severity of a case."""

    type: Literal[CaseEventType.SEVERITY_UPDATE]
    severity: CaseSeverity


class FieldUpdateEvent(BaseModel):
    """When a user updates a field on a case."""

    type: Literal[CaseEventType.FIELD_UPDATE]
    field: str
    value: Any


type CaseEvent = Annotated[
    CommentCreateEvent
    | CommentUpdateEvent
    | CommentDeleteEvent
    | StatusUpdateEvent
    | PriorityUpdateEvent
    | SeverityUpdateEvent
    | FieldUpdateEvent,
    Field(discriminator="type"),
]
CaseEventValidator: TypeAdapter[EventActivity] = TypeAdapter(EventActivity)


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
