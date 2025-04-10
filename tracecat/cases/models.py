from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from pydantic import BaseModel, Field

from tracecat.auth.models import UserRead
from tracecat.cases.constants import RESERVED_CASE_FIELDS
from tracecat.cases.enums import CasePriority, CaseSeverity, CaseStatus
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
    fields: list[CaseCustomFieldRead]


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
