from enum import StrEnum


class InvitationStatus(StrEnum):
    """Invitation lifecycle status."""

    PENDING = "pending"  # Awaiting response
    ACCEPTED = "accepted"  # User accepted, membership created
    REVOKED = "revoked"  # Manually cancelled by admin
