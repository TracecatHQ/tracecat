"""Pydantic models for approval management."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import UUID4, BaseModel

from tracecat.agent.approvals.enums import ApprovalStatus
from tracecat.auth.models import UserReadMinimal


class ApprovalCreate(BaseModel):
    """Attributes required to create an approval record."""

    session_id: uuid.UUID
    tool_call_id: str
    tool_name: str
    tool_call_args: dict[str, Any] | None = None


class ApprovalUpdate(BaseModel):
    """Attributes to update an approval record.

    All fields are optional to support partial updates.
    """

    tool_name: str | None = None
    status: ApprovalStatus | None = None
    reason: str | None = None
    tool_call_args: dict[str, Any] | None = None
    decision: dict[str, Any] | None = None
    approved_by: UUID4 | None = None


class ApprovalRead(BaseModel):
    """Serialized approval record."""

    id: uuid.UUID
    session_id: uuid.UUID
    tool_call_id: str
    tool_name: str
    status: ApprovalStatus
    reason: str | None
    tool_call_args: dict[str, Any] | None
    decision: dict[str, Any] | None
    approved_by: UserReadMinimal | None = None
    approved_at: datetime | None
    created_at: datetime
    updated_at: datetime
