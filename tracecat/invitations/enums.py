from enum import StrEnum


class InvitationStatus(StrEnum):
    """Invitation lifecycle status."""

    PENDING = "pending"  # Awaiting response
    ACCEPTED = "accepted"  # User accepted, membership created
    EXPIRED = "expired"  # Past expires_at, no action taken
    REVOKED = "revoked"  # Manually cancelled by admin
