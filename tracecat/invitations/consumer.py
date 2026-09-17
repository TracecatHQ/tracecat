"""Invitation email delivery poller. The invitation row is its own outbox."""

from __future__ import annotations

import asyncio
import uuid
from typing import Final

from sqlalchemy import bindparam, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.db.engine import get_async_session_bypass_rls_context_manager
from tracecat.db.models import (
    Invitation,
    InvitationGrant,
    Organization,
    Role,
    Workspace,
)
from tracecat.email.transport import EmailDeliveryError, SMTPTransport
from tracecat.invitations.email import InvitationGrantLine, invitation_email
from tracecat.invitations.enums import InvitationStatus
from tracecat.logger import logger

POLL_INTERVAL_SECONDS: Final = 2.0
CLAIM_BATCH_SIZE: Final = 20
MAX_EMAIL_ATTEMPTS: Final = 3


async def load_invitation_grants(
    session: AsyncSession, invitation_id: uuid.UUID
) -> list[InvitationGrantLine]:
    """Resolve one invitation's grants into workspace and role display names.

    Args:
        session: The session to query.
        invitation_id: The invitation whose grants to load.

    Returns:
        One line per grant, org-wide grants first, then workspaces by name.
    """
    result = await session.execute(
        select(Workspace.name, Role.name)
        .select_from(InvitationGrant)
        .join(Role, Role.id == InvitationGrant.role_id)
        .outerjoin(Workspace, Workspace.id == InvitationGrant.workspace_id)
        .where(InvitationGrant.invitation_id == invitation_id)
        .order_by(Workspace.name.nulls_first())
    )
    return [
        InvitationGrantLine(workspace_name=workspace_name, role_name=role_name)
        for workspace_name, role_name in result.tuples()
    ]


async def deliver_next_invitation(
    session: AsyncSession, transport: SMTPTransport
) -> bool:
    """Claim the oldest unsent invitation and send it.

    Returns False when nothing is left to claim or the relay asked for a retry.
    One row is claimed per send, so a crash strands at most that row, never a batch.
    """
    next_id = (
        select(Invitation.id)
        .where(
            Invitation.email_claimed_at.is_(None),
            # Rendered inline so a generic plan can still prove the partial index.
            Invitation.status
            == bindparam("pending", InvitationStatus.PENDING, literal_execute=True),
            Invitation.expires_at > func.now(),
            Invitation.email_attempts
            < bindparam("attempt_cap", MAX_EMAIL_ATTEMPTS, literal_execute=True),
        )
        .order_by(Invitation.created_at)
        .limit(1)
        .with_for_update(skip_locked=True)
        .scalar_subquery()
    )
    claimed = await session.execute(
        update(Invitation)
        .execution_options(synchronize_session=False)
        .where(
            Invitation.id == next_id,
            Organization.id == Invitation.organization_id,
        )
        .values(
            email_claimed_at=func.now(),
            email_attempts=Invitation.email_attempts + 1,
        )
        .returning(
            Invitation.id,
            Invitation.email,
            Invitation.token,
            Invitation.email_attempts,
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
    grants = await load_invitation_grants(session, invitation_id)
    # The grants read opened a transaction; close it so SMTP starts with none.
    await session.commit()
    message = invitation_email(
        to=email,
        organization_name=organization_name,
        token=token,
        grants=grants,
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
            update(Invitation)
            .where(Invitation.id == invitation_id)
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
        update(Invitation)
        .where(Invitation.id == invitation_id)
        # Cooldown starts when sending finishes, not when the transaction began.
        .values(email_sent_at=func.clock_timestamp())
    )
    await session.commit()
    return True


async def run_invitation_email_tick(
    stop_event: asyncio.Event | None = None,
) -> int:
    """Deliver up to one batch. Returns the number of rows sent or given up on.

    An unconfigured relay claims nothing, so rows wait until SMTP is set up.
    A set stop event ends the batch before the next claim, so a drain-deadline
    cancel cannot strand a row claimed after the stop was signalled.
    """
    transport = SMTPTransport.from_config()
    if transport is None:
        return 0
    delivered = 0
    async with get_async_session_bypass_rls_context_manager() as session:
        while delivered < CLAIM_BATCH_SIZE:
            if stop_event is not None and stop_event.is_set():
                break
            if not await deliver_next_invitation(session, transport):
                break
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
            await run_invitation_email_tick(stop_event)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Invitation email tick failed")
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=POLL_INTERVAL_SECONDS)
        except TimeoutError:
            continue
    logger.info("Invitation email consumer stopped")
