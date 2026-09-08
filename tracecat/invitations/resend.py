"""Shared eligibility rules for manually resending an invitation email."""

from __future__ import annotations

from datetime import UTC, datetime

from tracecat.db.models import OrganizationInvitation
from tracecat.email.transport import SMTPTransport
from tracecat.exceptions import TracecatConflictError, TracecatValidationError
from tracecat.invitations.consumer import RESEND_COOLDOWN
from tracecat.invitations.enums import InvitationStatus


def validate_invitation_resendable(invitation: OrganizationInvitation) -> None:
    """Raise unless the invitation may be re-entered into the email outbox.

    Args:
        invitation: The invitation to check.

    Raises:
        TracecatValidationError: If the invitation is not pending, has expired,
            or email delivery is not configured.
        TracecatConflictError: If the invitation was claimed within the cooldown.
    """
    if invitation.status != InvitationStatus.PENDING:
        raise TracecatValidationError(
            f"Cannot resend invitation with status '{invitation.status}'"
        )

    now = datetime.now(UTC)
    if invitation.expires_at <= now:
        raise TracecatValidationError("Cannot resend an expired invitation")

    if SMTPTransport.from_config() is None:
        raise TracecatValidationError("Email delivery is not configured")

    claimed_at = invitation.email_claimed_at
    if claimed_at is not None and now - claimed_at < RESEND_COOLDOWN:
        raise TracecatConflictError("Invitation email was sent less than a minute ago")
