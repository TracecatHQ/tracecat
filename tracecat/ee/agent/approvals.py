from dataclasses import dataclass
from enum import StrEnum

from pydantic import BaseModel
from pydantic_ai.messages import (
    ToolCallPart,
)

from tracecat.service import BaseWorkspaceService


class ApprovalStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


@dataclass
class ApprovalState:
    status: ApprovalStatus
    tool_call_part: ToolCallPart


class ApprovalAction(StrEnum):
    APPROVE = "approve"
    REJECT = "reject"


class ApprovalRequest(BaseModel):
    call_id: str
    action: ApprovalAction


class ApprovalResponse(BaseModel):
    status: ApprovalStatus


class ApprovalsService(BaseWorkspaceService):
    """Service for managing approval states for tool calls."""

    service_name = "approvals"
