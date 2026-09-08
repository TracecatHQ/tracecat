"""Shared rules for manually re-entering an invitation into the email outbox."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Final

from sqlalchemy import or_, update
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.db.models import OrganizationInvitation
from tracecat.email.transport import SMTPTransport
from tracecat.exceptions import TracecatConflictError, TracecatValidationError
from tracecat.invitations.enums import InvitationStatus

# A manual resend clears the claim, so this bounds how often a user can
# re-enter a row into the outbox. Must exceed the SMTP send timeout: a claim
# younger than this may still be in flight, and resetting it would double-send.
RESEND_COOLDOWN: Final = timedelta(seconds=60)


async def reset_invitation_email(
    session: AsyncSession, invitation: OrganizationInvitation
) -> None:
    """Re-enter a pending invitation into the email outbox.

    The UPDATE takes the row lock itself, so it waits behind the poller's
    delivery lock and re-evaluates its WHERE clause on the fresh row.

    Args:
        session: The session holding the loaded invitation.
        invitation: The scoped invitation to reset.

    Raises:
        TracecatValidationError: If the loaded row is not pending, has expired,
            or email delivery is not configured.
        TracecatConflictError: If the row changed or is inside the cooldown.
    """
    now = datetime.now(UTC)
    if invitation.status != InvitationStatus.PENDING:
        raise TracecatValidationError(
            f"Cannot resend invitation with status '{invitation.status}'"
        )
    if invitation.expires_at <= now:
        raise TracecatValidationError("Cannot resend an expired invitation")
    if SMTPTransport.from_config() is None:
        raise TracecatValidationError("Email delivery is not configured")

    cutoff = now - RESEND_COOLDOWN
    result = await session.execute(
        update(OrganizationInvitation)
        .where(
            OrganizationInvitation.id == invitation.id,
            OrganizationInvitation.status == InvitationStatus.PENDING,
            OrganizationInvitation.expires_at > now,
            or_(
                OrganizationInvitation.email_claimed_at.is_(None),
                OrganizationInvitation.email_claimed_at < cutoff,
            ),
            or_(
                OrganizationInvitation.email_sent_at.is_(None),
                OrganizationInvitation.email_sent_at < cutoff,
            ),
        )
        .values(email_claimed_at=None, email_sent_at=None, email_attempts=0)
    )
    if result.rowcount != 1:  # pyright: ignore[reportAttributeAccessIssue]
        raise TracecatConflictError("Invitation email was sent less than a minute ago")
