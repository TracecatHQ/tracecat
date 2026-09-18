"""Unified invitation service.

An invitation is anchored to an organization and carries a list of grants. Each
grant is one role at org scope (``workspace_id`` NULL) or on one workspace.
Accepting admits the user to the organization and inserts one
``user_role_assignment`` per grant.
"""

from __future__ import annotations

import secrets
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Final

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import NoResultFound
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from tracecat.audit.enums import AuditEventStatus
from tracecat.audit.logger import audit_log
from tracecat.audit.service import AuditService
from tracecat.auth.types import Role
from tracecat.authz.controls import ensure_can_grant_scopes, require_scope
from tracecat.authz.membership import ensure_member, lock_role_changes
from tracecat.authz.scopes import ORG_MEMBER_ROLE_SLUG
from tracecat.authz.service import resolve_granter_scopes
from tracecat.db.models import (
    Invitation,
    InvitationGrant,
    OrganizationMembership,
    User,
    UserRoleAssignment,
    Workspace,
)
from tracecat.db.models import Role as DBRole
from tracecat.email.transport import SMTPTransport
from tracecat.exceptions import (
    TracecatAuthorizationError,
    TracecatConflictError,
    TracecatNotFoundError,
    TracecatValidationError,
)
from tracecat.identifiers import OrganizationID, UserID
from tracecat.invitations.enums import InvitationStatus
from tracecat.invitations.schemas import InvitationCreate
from tracecat.service import BaseOrgService

INVITATION_TTL = timedelta(days=7)
# A manual resend clears the claim, so this bounds how often a user can
# re-enter a row into the outbox. Must exceed the SMTP send timeout: a claim
# younger than this may still be in flight, and resetting it would double-send.
RESEND_COOLDOWN: Final = timedelta(seconds=60)


def _generate_token() -> str:
    return secrets.token_urlsafe(48)[:64]


# --- Create


async def create_invitation_row(
    session: AsyncSession,
    *,
    organization_id: OrganizationID,
    params: InvitationCreate,
    invited_by: UserID | None,
    created_by_platform_admin: bool,
) -> Invitation:
    """Insert an invitation and its grants. Roles must already be validated.

    Does not commit.

    Raises:
        TracecatValidationError: If the email is already an org member or a
            pending unexpired invitation already exists.
    """
    email = params.email
    existing_member = (
        await session.execute(
            select(OrganizationMembership)
            .join(User, OrganizationMembership.user_id == User.id)
            .where(
                OrganizationMembership.organization_id == organization_id,
                func.lower(User.email) == email.lower(),
            )
        )
    ).scalar_one_or_none()
    if existing_member is not None:
        raise TracecatValidationError(
            f"{email} is already a member of this organization"
        )

    # The partial unique index covers pending rows alone, so at most one exists;
    # settled rows are history the admin listing exposes and are left untouched.
    pending = (
        await session.execute(
            select(Invitation)
            .where(
                Invitation.organization_id == organization_id,
                func.lower(Invitation.email) == email.lower(),
                Invitation.status == InvitationStatus.PENDING,
            )
            .options(selectinload(Invitation.grants))
        )
    ).scalar_one_or_none()
    now = datetime.now(UTC)
    if pending is not None:
        if pending.expires_at >= now and pending.grants:
            raise TracecatValidationError(
                f"An invitation already exists for {email} in this organization"
            )
        await session.delete(pending)
        await session.flush()

    invitation = Invitation(
        organization_id=organization_id,
        email=email,
        invited_by=invited_by,
        token=_generate_token(),
        expires_at=now + INVITATION_TTL,
        status=InvitationStatus.PENDING,
        created_by_platform_admin=created_by_platform_admin,
        grants=[
            InvitationGrant(
                organization_id=organization_id,
                workspace_id=grant.workspace_id,
                role_id=grant.role_id,
            )
            for grant in params.grants
        ],
    )
    session.add(invitation)
    await session.flush()
    return invitation


async def validate_grants(
    session: AsyncSession,
    role: Role,
    organization_id: OrganizationID,
    params: InvitationCreate,
) -> None:
    """Validate every grant's role and workspace."""
    grants = params.grants
    workspace_ids = {g.workspace_id for g in grants if g.workspace_id is not None}
    if workspace_ids:
        found = set(
            (
                await session.execute(
                    select(Workspace.id).where(
                        Workspace.id.in_(workspace_ids),
                        Workspace.organization_id == organization_id,
                    )
                )
            )
            .scalars()
            .all()
        )
        if missing := workspace_ids - found:
            raise TracecatValidationError(
                f"Workspace not found in this organization: {sorted(map(str, missing))}"
            )

    role_ids = {grant.role_id for grant in grants}
    granted_roles = (
        (
            await session.execute(
                select(DBRole)
                .where(
                    DBRole.organization_id == organization_id, DBRole.id.in_(role_ids)
                )
                .options(selectinload(DBRole.scopes))
            )
        )
        .scalars()
        .all()
    )
    if {granted_role.id for granted_role in granted_roles} != role_ids:
        raise TracecatValidationError("Invalid role ID for this organization")
    if any(granted_role.slug == ORG_MEMBER_ROLE_SLUG for granted_role in granted_roles):
        raise TracecatValidationError("organization-member is granted implicitly")

    if not role.is_platform_superuser:
        # Read live permissions once for this batch, never the cached Role.scopes.
        granter_scopes = await resolve_granter_scopes(session, role)
        ensure_can_grant_scopes(
            granter_scopes,
            (
                scope.name
                for granted_role in granted_roles
                for scope in granted_role.scopes
            ),
        )


# --- Accept


async def _apply_grants(
    session: AsyncSession,
    *,
    organization_id: OrganizationID,
    user_id: UserID,
    grants: Sequence[InvitationGrant],
) -> None:
    """Insert one assignment per grant.

    Existing assignments are never overwritten: a grant the user already holds
    at that scope is skipped.
    """
    await lock_role_changes(session, organization_id)

    # The membership row is the aggregate root; assignments hang off it.
    await ensure_member(session, organization_id, user_id)

    for grant in grants:
        stmt = pg_insert(UserRoleAssignment).values(
            organization_id=organization_id,
            user_id=user_id,
            workspace_id=grant.workspace_id,
            role_id=grant.role_id,
        )
        if grant.workspace_id is None:
            stmt = stmt.on_conflict_do_nothing(
                index_elements=[
                    UserRoleAssignment.organization_id,
                    UserRoleAssignment.user_id,
                ],
                index_where=UserRoleAssignment.workspace_id.is_(None),
            )
        else:
            stmt = stmt.on_conflict_do_nothing(
                index_elements=[
                    UserRoleAssignment.user_id,
                    UserRoleAssignment.workspace_id,
                ]
            )
        await session.execute(stmt)


async def _claim_pending(session: AsyncSession, invitation: Invitation) -> None:
    """Atomically move pending to accepted, or raise why it is no longer valid."""
    now = datetime.now(UTC)
    update_result = await session.execute(
        update(Invitation)
        .where(
            Invitation.id == invitation.id,
            Invitation.status == InvitationStatus.PENDING,
        )
        .values(status=InvitationStatus.ACCEPTED, accepted_at=now)
    )
    if update_result.rowcount != 0:  # pyright: ignore[reportAttributeAccessIssue]
        # The claim may have waited on a row lock past the initial expiry check.
        if invitation.expires_at < datetime.now(UTC):
            raise TracecatAuthorizationError("Invitation has expired")
        return

    # Status changed between fetch and update - re-fetch for an accurate error
    await session.refresh(invitation)
    if invitation.status == InvitationStatus.ACCEPTED:
        raise TracecatAuthorizationError("Invitation has already been accepted")
    if invitation.status == InvitationStatus.REVOKED:
        raise TracecatAuthorizationError("Invitation has been revoked")
    raise TracecatAuthorizationError("Invitation is no longer valid")


async def accept_invitation_for_user(
    session: AsyncSession,
    *,
    user_id: UserID,
    token: str,
) -> Invitation:
    """Accept an invitation and apply its grants.

    A standalone function because acceptance carries no organization context:
    the user may not belong to any organization yet.

    Raises:
        TracecatNotFoundError: If the invitation doesn't exist.
        TracecatAuthorizationError: If the invitation is expired, revoked, already
            accepted, or the user's email doesn't match the invitation email.
    """
    invitation = await find_invitation_by_token(session, token)
    if invitation is None:
        raise TracecatNotFoundError("Invitation not found")

    user = (
        await session.execute(select(User).where(User.id == user_id))  # pyright: ignore[reportArgumentType]
    ).scalar_one_or_none()
    if user is None:
        raise TracecatAuthorizationError("User not found")
    if user.email.lower() != invitation.email.lower():
        raise TracecatAuthorizationError(
            "This invitation was sent to a different email address"
        )
    if invitation.expires_at < datetime.now(UTC):
        raise TracecatAuthorizationError("Invitation has expired")
    # A grant whose workspace or role was deleted is gone by foreign key.
    if not invitation.grants:
        raise TracecatAuthorizationError("Invitation is no longer valid")

    audit_role = Role(
        type="user",
        user_id=user_id,
        organization_id=invitation.organization_id,
        service_id="tracecat-api",
    )
    async with AuditService.with_session(audit_role, session=session) as svc:
        await svc.create_event(
            resource_type="organization_invitation",
            action="accept",
            resource_id=invitation.id,
            status=AuditEventStatus.ATTEMPT,
        )

    try:
        await _claim_pending(session, invitation)
        await _apply_grants(
            session,
            organization_id=invitation.organization_id,
            user_id=user_id,
            grants=invitation.grants,
        )
        await session.commit()
    except TracecatAuthorizationError:
        # Expected user errors are not audit failures.
        raise
    except Exception:
        async with AuditService.with_session(audit_role, session=session) as svc:
            await svc.create_event(
                resource_type="organization_invitation",
                action="accept",
                resource_id=invitation.id,
                status=AuditEventStatus.FAILURE,
            )
        raise

    async with AuditService.with_session(audit_role, session=session) as svc:
        await svc.create_event(
            resource_type="organization_invitation",
            action="accept",
            resource_id=invitation.id,
            status=AuditEventStatus.SUCCESS,
        )
    return invitation


# --- Revoke


async def revoke_invitation_row(session: AsyncSession, invitation: Invitation) -> None:
    """Mark a pending invitation revoked. Does not commit.

    Raises:
        TracecatAuthorizationError: If the invitation is not pending.
    """
    result = await session.execute(
        update(Invitation)
        .where(
            Invitation.id == invitation.id,
            Invitation.status == InvitationStatus.PENDING,
        )
        .values(status=InvitationStatus.REVOKED)
        .returning(Invitation.id)
        .execution_options(synchronize_session=False)
    )
    # The row may have changed while this update waited for acceptance's lock.
    await session.refresh(invitation)
    if result.scalar_one_or_none() is None:
        raise TracecatAuthorizationError(
            f"Cannot revoke invitation with status '{invitation.status}'"
        )


# --- Email resend


async def reset_invitation_email(session: AsyncSession, invitation: Invitation) -> None:
    """Re-enter a pending invitation into the email outbox.

    The UPDATE takes the row lock itself, so it waits behind the poller's
    delivery lock and re-evaluates its WHERE clause on the fresh row.

    Args:
        session: The session holding the loaded invitation.
        invitation: The scoped invitation to reset.

    Raises:
        NoResultFound: If the row no longer exists.
        TracecatValidationError: If the row is not pending, has expired, or
            email delivery is not configured.
        TracecatConflictError: If the row is inside the resend cooldown.
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
        update(Invitation)
        .where(
            Invitation.id == invitation.id,
            Invitation.status == InvitationStatus.PENDING,
            Invitation.expires_at > now,
            or_(
                Invitation.email_claimed_at.is_(None),
                Invitation.email_claimed_at < cutoff,
            ),
            or_(
                Invitation.email_sent_at.is_(None),
                Invitation.email_sent_at < cutoff,
            ),
        )
        # Preserve the last successful delivery even if this attempt fails.
        .values(email_claimed_at=None, email_attempts=0)
    )
    if result.rowcount != 1:  # pyright: ignore[reportAttributeAccessIssue]
        # The WHERE clause also fails when another request changed the row, so
        # re-read it before attributing the miss to the cooldown.
        fresh = await session.get(Invitation, invitation.id, populate_existing=True)
        if fresh is None:
            raise NoResultFound("Invitation not found")
        if fresh.status != InvitationStatus.PENDING:
            raise TracecatValidationError(
                f"Cannot resend invitation with status '{fresh.status}'"
            )
        if fresh.expires_at <= now:
            raise TracecatValidationError("Cannot resend an expired invitation")
        raise TracecatConflictError("Invitation email was sent less than a minute ago")


# --- Lookups


async def find_invitation_by_token(
    session: AsyncSession, token: str
) -> Invitation | None:
    """Resolve an invitation by token, with grants loaded.

    Unauthenticated callers use this for the accept page.
    """
    result = await session.execute(
        select(Invitation)
        .where(Invitation.token == token)
        .options(selectinload(Invitation.organization), selectinload(Invitation.grants))
    )
    return result.scalar_one_or_none()


async def get_pending_invitation_for_email(
    session: AsyncSession,
    *,
    organization_id: OrganizationID,
    email: str,
) -> Invitation | None:
    """Return a pending, unexpired invitation for the email if one exists."""
    result = await session.execute(
        select(Invitation)
        .where(
            Invitation.organization_id == organization_id,
            func.lower(Invitation.email) == email.lower(),
            Invitation.status == InvitationStatus.PENDING,
            Invitation.expires_at > datetime.now(UTC),
            Invitation.grants.any(),
        )
        .order_by(Invitation.created_at.desc())
        .limit(1)
        .options(selectinload(Invitation.grants))
    )
    return result.scalar_one_or_none()


# --- Org-scoped service


class InvitationService(BaseOrgService):
    """Manage organization invitations and the grants they confer."""

    service_name = "invitation"

    @require_scope("org:member:invite")
    @audit_log(resource_type="organization_invitation", action="create")
    async def create_invitation(self, params: InvitationCreate) -> Invitation:
        """Create an invitation carrying one or more grants.

        Raises:
            TracecatAuthorizationError: If the caller is not an authenticated user.
            TracecatValidationError: If a grant is invalid, the email is already an
                org member, or a pending unexpired invitation already exists.
        """
        if self.role.user_id is None:
            raise TracecatAuthorizationError(
                "User must be authenticated to create invitation"
            )

        await validate_grants(self.session, self.role, self.organization_id, params)
        invitation = await create_invitation_row(
            self.session,
            organization_id=self.organization_id,
            params=params,
            invited_by=self.role.user_id,
            # Only the control-plane create path is platform-created; its listing
            # serializes one org-wide grant and this path allows any grant shape.
            created_by_platform_admin=False,
        )
        await self.session.commit()
        return invitation

    async def list_invitations(
        self, *, status: InvitationStatus | None = None
    ) -> Sequence[Invitation]:
        """List invitations for the organization, newest first."""
        # A pending row whose grants all cascaded away confers nothing; accepted
        # and revoked rows stay listed as history regardless.
        statement = select(Invitation).where(
            Invitation.organization_id == self.organization_id,
            or_(
                Invitation.status != InvitationStatus.PENDING,
                Invitation.grants.any(),
            ),
        )
        if status is not None:
            statement = statement.where(Invitation.status == status)
        statement = statement.order_by(Invitation.created_at.desc()).options(
            selectinload(Invitation.grants)
        )
        result = await self.session.execute(statement)
        return result.scalars().all()

    async def get_invitation(self, invitation_id: uuid.UUID) -> Invitation:
        """Get an invitation by ID; it must belong to this organization.

        Raises:
            NoResultFound: If the invitation doesn't exist or belongs to another org.
        """
        result = await self.session.execute(
            select(Invitation)
            .where(
                and_(
                    Invitation.id == invitation_id,
                    Invitation.organization_id == self.organization_id,
                )
            )
            .options(selectinload(Invitation.grants))
        )
        return result.scalar_one()

    @require_scope("org:member:invite")
    @audit_log(
        resource_type="organization_invitation",
        action="revoke",
        resource_id_attr="invitation_id",
    )
    async def revoke_invitation(self, invitation_id: uuid.UUID) -> Invitation:
        """Revoke a pending invitation.

        Raises:
            NoResultFound: If the invitation doesn't exist or belongs to another org.
            TracecatAuthorizationError: If the invitation is not pending.
        """
        invitation = await self.get_invitation(invitation_id)
        await revoke_invitation_row(self.session, invitation)
        await self.session.commit()
        return invitation

    @require_scope("org:member:invite")
    @audit_log(
        resource_type="organization_invitation",
        action="resend",
        resource_id_attr="invitation_id",
    )
    async def resend_invitation(self, invitation_id: uuid.UUID) -> Invitation:
        """Re-enter a pending invitation into the email outbox.

        Raises:
            NoResultFound: If the invitation doesn't exist or belongs to another org.
            TracecatValidationError: If the invitation is not pending, has expired,
                or email delivery is not configured.
            TracecatConflictError: If the invitation was claimed within the cooldown.
        """
        invitation = await self.get_invitation(invitation_id)
        await reset_invitation_email(self.session, invitation)
        await self.session.commit()
        return invitation
