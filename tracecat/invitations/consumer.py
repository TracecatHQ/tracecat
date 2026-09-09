"""Invitation email delivery poller. The invitation row is its own outbox."""

from __future__ import annotations

import asyncio
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


async def deliver_next_invitation(
    session: AsyncSession, transport: SMTPTransport
) -> bool:
    """Claim the oldest unsent invitation and send it.

    Returns False when nothing is left to claim or the relay asked for a retry.
    One row is claimed per send, so a crash strands at most that row, never a batch.
    """
    next_id = (
        select(OrganizationInvitation.id)
        .where(
            OrganizationInvitation.email_claimed_at.is_(None),
            OrganizationInvitation.status == InvitationStatus.PENDING,
            OrganizationInvitation.expires_at > func.now(),
            OrganizationInvitation.email_attempts < MAX_EMAIL_ATTEMPTS,
        )
        .order_by(OrganizationInvitation.created_at)
        .limit(1)
        .with_for_update(skip_locked=True)
        .scalar_subquery()
    )
    claimed = await session.execute(
        update(OrganizationInvitation)
        .execution_options(synchronize_session=False)
        .where(
            OrganizationInvitation.id == next_id,
            Organization.id == OrganizationInvitation.organization_id,
        )
        .values(
            email_claimed_at=func.now(),
            email_attempts=OrganizationInvitation.email_attempts + 1,
        )
        .returning(
            OrganizationInvitation.id,
            OrganizationInvitation.email,
            OrganizationInvitation.token,
            OrganizationInvitation.email_attempts,
            Organization.name,
        )
    )
    row = claimed.one_or_none()
    # Commit the claim before SMTP: a crash mid-send leaves the row claimed
    # and unsent rather than risking a duplicate email.
    await session.commit()
    if row is None:
        return False

    invitation_id, email, token, attempts, organization_name = row.tuple()
    message = invitation_email(
        to=email, organization_name=organization_name, token=token
    )
    try:
        await transport.send(message)
    except EmailDeliveryError as error:
        if not error.retryable:
            logger.exception(
                "Invitation email delivery failed", invitation_id=str(invitation_id)
            )
            return True
        await session.execute(
            update(OrganizationInvitation)
            .where(OrganizationInvitation.id == invitation_id)
            .values(email_claimed_at=None)
        )
        await session.commit()
        logger.warning(
            "Invitation email delivery deferred",
            invitation_id=str(invitation_id),
            attempts=attempts,
        )
        # Ending the tick gives the relay POLL_INTERVAL_SECONDS before the retry.
        return False

    await session.execute(
        update(OrganizationInvitation)
        .where(OrganizationInvitation.id == invitation_id)
        .values(email_sent_at=func.now())
    )
    await session.commit()
    return True


async def run_invitation_email_tick() -> int:
    """Deliver up to one batch. Returns the number of rows sent or given up on.

    An unconfigured relay claims nothing, so rows wait until SMTP is set up.
    """
    transport = SMTPTransport.from_config()
    if transport is None:
        return 0
    delivered = 0
    async with get_async_session_bypass_rls_context_manager() as session:
        while delivered < CLAIM_BATCH_SIZE and await deliver_next_invitation(
            session, transport
        ):
            delivered += 1
    return delivered


async def start_invitation_email_consumer(
    stop_event: asyncio.Event | None = None,
) -> None:
    """Poll the invitation outbox until cancelled or stopped."""
    stop_event = stop_event or asyncio.Event()
    logger.info("Starting invitation email consumer")
    while not stop_event.is_set():
        try:
            await run_invitation_email_tick()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Invitation email tick failed")
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=POLL_INTERVAL_SECONDS)
        except TimeoutError:
            continue
    logger.info("Invitation email consumer stopped")
