from enum import StrEnum


class ApprovalStatus(StrEnum):
    """Possible states for a deferred tool approval."""

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
