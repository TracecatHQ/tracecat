"""Pydantic models for approval management."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import UUID4, BaseModel, Field

from tracecat.agent.approvals.enums import (
    ApprovalRecommendationVerdict,
    ApprovalStatus,
)
from tracecat.agent.schemas import AgentOutput
from tracecat.auth.schemas import UserReadMinimal


class SessionHistoryItem(BaseModel):
    """Represents a single execution in the session history."""

    execution_id: str = Field(..., description="The workflow execution ID")
    result: AgentOutput = Field(..., description="The execution result")


class ApprovalRecommendation(BaseModel):
    verdict: ApprovalRecommendationVerdict | None = None
    reason: str | None = None
    generated_by: str | None = None
    tool_call_id: str | None = None


class ApprovalCreate(BaseModel):
    """Attributes required to create an approval record."""

    session_id: uuid.UUID
    tool_call_id: str
    tool_name: str
    tool_call_args: dict[str, Any] | None = None
    history: list[SessionHistoryItem]
    recommendation: ApprovalRecommendation | None = None


class ApprovalUpdate(BaseModel):
    """Attributes to update an approval record.

    All fields are optional to support partial updates.
    """

    tool_name: str | None = None
    status: ApprovalStatus | None = None
    reason: str | None = None
    tool_call_args: dict[str, Any] | None = None
    decision: bool | dict[str, Any] | None = None
    approved_by: UUID4 | None = None
    history: list[SessionHistoryItem] | None = (
        None  # Optional - set to None to skip updating
    )
    recommendation: ApprovalRecommendation | None = None


class ApprovalRead(BaseModel):
    """Serialized approval record."""

    id: uuid.UUID
    session_id: uuid.UUID
    tool_call_id: str
    tool_name: str
    status: ApprovalStatus
    reason: str | None
    tool_call_args: dict[str, Any] | None
    decision: bool | dict[str, Any] | None
    approved_by: UserReadMinimal | None = None
    history: list[SessionHistoryItem]
    recommendation: ApprovalRecommendation | None = None
    approved_at: datetime | None
    created_at: datetime
    updated_at: datetime
