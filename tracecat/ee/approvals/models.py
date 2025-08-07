"""Models for the approval service."""

import uuid
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel
from pydantic_ai.messages import ToolCallPart

from tracecat.ee.enums import ApprovalAction, ApprovalStatus, ApprovalType
from tracecat.types.auth import Role


@dataclass
class ApprovalState:
    """Internal state for an approval in workflow context."""

    status: ApprovalStatus
    tool_call_part: ToolCallPart


class ApprovalRequest(BaseModel):
    """Request to approve or reject an approval."""

    call_id: str
    action: ApprovalAction


class ApprovalResponse(BaseModel):
    """Response from an approval action."""

    status: ApprovalStatus


# ──────────────────────────
# CRUD Models
# ──────────────────────────


class ApprovalCreate(BaseModel):
    """Parameters for creating an approval."""

    session_id: str
    type: ApprovalType
    status: ApprovalStatus = ApprovalStatus.PENDING
    data: dict[str, Any] | None = None
    actor: str | None = None


class ApprovalUpdate(BaseModel):
    """Parameters for updating an approval."""

    status: ApprovalStatus | None = None
    data: dict[str, Any] | None = None
    actor: str | None = None


class ApprovalRead(BaseModel):
    """Read model for approval."""

    id: uuid.UUID
    session_id: str
    type: ApprovalType
    status: ApprovalStatus
    data: dict[str, Any] | None = None
    actor: str | None = None
    owner_id: uuid.UUID


# ──────────────────────────
# Activity Input Models
# ──────────────────────────


class CreateApprovalActivityInputs(BaseModel):
    """Input for create_approval_activity."""

    role: Role
    params: ApprovalCreate


class UpdateApprovalActivityInputs(BaseModel):
    """Input for update_approval_activity."""

    role: Role
    approval_id: uuid.UUID
    params: ApprovalUpdate
