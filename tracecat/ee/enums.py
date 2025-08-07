from enum import StrEnum


class PlatformAction(StrEnum):
    INTERACT_RESPONSE = "core.interact.response"


class ApprovalType(StrEnum):
    TOOL_CALL = "tool_call"


class ApprovalStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class ApprovalAction(StrEnum):
    APPROVE = "approve"
    REJECT = "reject"
