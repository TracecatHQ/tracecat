"""Invitation email delivery poller. The invitation row is its own outbox."""

from __future__ import annotations

import asyncio
import uuid
from typing import Final

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.db.engine import get_async_session_bypass_rls_context_manager
from tracecat.db.models import Organization, OrganizationInvitation
from tracecat.email.transport import EmailDeliveryError, SMTPTransport
from tracecat.invitations.email import invitation_email
from tracecat.invitations.enums import InvitationStatus
from tracecat.logger import logger

POLL_INTERVAL_SECONDS: Final = 2.0
CLAIM_BATCH_SIZE: Final = 20
MAX_EMAIL_ATTEMPTS: Final = 3


async def claim_invitation_batch(
    session: AsyncSession,
    *,
    limit: int = CLAIM_BATCH_SIZE,
) -> list[OrganizationInvitation]:
    """Claim up to `limit` unsent invitations, incrementing their attempt count.

    Claiming before sending makes delivery at-most-once: a crashed pod leaves
    the row claimed and unsent rather than risking a duplicate email.
    """
    eligible = (
        select(OrganizationInvitation.id)
        .where(
            OrganizationInvitation.email_claimed_at.is_(None),
            OrganizationInvitation.status == InvitationStatus.PENDING,
            OrganizationInvitation.expires_at > func.now(),
            OrganizationInvitation.email_attempts < MAX_EMAIL_ATTEMPTS,
        )
        .order_by(OrganizationInvitation.created_at)
        .limit(limit)
        .with_for_update(skip_locked=True)
    )
    claimed = await session.execute(
        update(OrganizationInvitation)
        .where(OrganizationInvitation.id.in_(eligible))
        .values(
            email_claimed_at=func.now(),
            email_attempts=OrganizationInvitation.email_attempts + 1,
        )
        .returning(OrganizationInvitation)
    )
    rows = list(claimed.scalars().all())
    await session.commit()
    return rows


async def deliver_invitation(
    session: AsyncSession,
    invitation: OrganizationInvitation,
    transport: SMTPTransport,
) -> None:
    """Send one claimed invitation and record its outcome.

    A retryable failure releases the claim so a later tick retries; any other
    failure leaves the row claimed and unsent, and the error surfaces.
    """
    # Read every field up front: the commits below expire the instance.
    invitation_id = invitation.id
    attempts = invitation.email_attempts
    organization_name = await session.scalar(
        select(Organization.name).where(Organization.id == invitation.organization_id)
    )
    if organization_name is None:
        raise RuntimeError(
            f"Invitation {invitation_id} references a missing organization"
        )

    message = invitation_email(
        to=invitation.email,
        organization_name=organization_name,
        token=invitation.token,
    )
    try:
        await transport.send(message)
    except EmailDeliveryError as error:
        if error.retryable:
            await _set_claim(session, invitation_id, claimed=False)
            logger.warning(
                "Invitation email delivery deferred",
                invitation_id=str(invitation_id),
                attempts=attempts,
            )
            return
        raise

    await session.execute(
        update(OrganizationInvitation)
        .where(OrganizationInvitation.id == invitation_id)
        .values(email_sent_at=func.now())
    )
    await session.commit()


async def _set_claim(
    session: AsyncSession, invitation_id: uuid.UUID, *, claimed: bool
) -> None:
    await session.execute(
        update(OrganizationInvitation)
        .where(OrganizationInvitation.id == invitation_id)
        .values(email_claimed_at=func.now() if claimed else None)
    )
    await session.commit()


async def run_invitation_email_tick(session: AsyncSession) -> int:
    """Claim and deliver one batch. Returns the number of rows claimed.

    An unconfigured relay claims nothing, so rows wait until SMTP is set up.
    """
    transport = SMTPTransport.from_config()
    if transport is None:
        return 0

    invitations = await claim_invitation_batch(session)
    for invitation in invitations:
        # Read the id up front: a rollback below expires the instance.
        invitation_id = invitation.id
        # Each row commits its own outcome so one failure never blocks the batch.
        try:
            await deliver_invitation(session, invitation, transport)
        except Exception:
            # The claim stands, so the row is not retried; surface it for Sentry.
            await session.rollback()
            logger.exception(
                "Invitation email delivery failed",
                invitation_id=str(invitation_id),
            )
    return len(invitations)


async def start_invitation_email_consumer(
    stop_event: asyncio.Event | None = None,
) -> None:
    """Poll the invitation outbox until cancelled or stopped."""
    stop_event = stop_event or asyncio.Event()
    logger.info("Starting invitation email consumer")
    while not stop_event.is_set():
        try:
            async with get_async_session_bypass_rls_context_manager() as session:
                await run_invitation_email_tick(session)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Invitation email tick failed")
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=POLL_INTERVAL_SECONDS)
        except TimeoutError:
            continue
    logger.info("Invitation email consumer stopped")
