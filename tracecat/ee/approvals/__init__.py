"""Approvals service for managing GraphAgent human-in-the-loop workflows."""

from tracecat.ee.approvals.models import (
    ApprovalCreate,
    ApprovalRead,
    ApprovalRequest,
    ApprovalResponse,
    ApprovalState,
    ApprovalUpdate,
    CreateApprovalActivityInputs,
    UpdateApprovalActivityInputs,
)
from tracecat.ee.approvals.service import ApprovalsService

__all__ = [
    # Service
    "ApprovalsService",
    # Models
    "ApprovalState",
    "ApprovalRequest",
    "ApprovalResponse",
    "ApprovalCreate",
    "ApprovalUpdate",
    "ApprovalRead",
    # Activity inputs
    "CreateApprovalActivityInputs",
    "UpdateApprovalActivityInputs",
]
