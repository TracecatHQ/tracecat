"""Unified invitation service.

An invitation is anchored to an organization and carries a list of grants. Each
grant is one role at org scope (``workspace_id`` NULL) or on one workspace.
Accepting inserts one ``user_role_assignment`` per grant, plus an
organization-member assignment when the user holds no org-wide role yet.
"""

from __future__ import annotations

import secrets
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from tracecat.audit.enums import AuditEventStatus
from tracecat.audit.logger import audit_log
from tracecat.audit.service import AuditService
from tracecat.auth.types import Role
from tracecat.authz.controls import require_scope
from tracecat.authz.service import resolve_grantable_role
from tracecat.db.models import (
    Invitation,
    InvitationGrant,
    LegacyMembership,
    LegacyOrganizationInvitation,
    LegacyOrganizationMembership,
    OrganizationMembership,
    User,
    UserRoleAssignment,
    Workspace,
)
from tracecat.db.models import Role as DBRole
from tracecat.exceptions import (
    TracecatAuthorizationError,
    TracecatNotFoundError,
    TracecatValidationError,
)
from tracecat.identifiers import OrganizationID, UserID
from tracecat.invitations.enums import InvitationStatus
from tracecat.invitations.schemas import InvitationCreate, InvitationGrantCreate
from tracecat.service import BaseOrgService

INVITATION_TTL = timedelta(days=7)
ORG_MEMBER_ROLE_SLUG = "organization-member"

# Preset roles are scoped by slug; custom roles are org-scoped unless workspace-only.
WORKSPACE_PRESET_ROLE_SLUGS = frozenset(
    {"workspace-admin", "workspace-editor", "workspace-viewer"}
)
ORG_PRESET_ROLE_SLUGS = frozenset(
    {"organization-owner", "organization-admin", ORG_MEMBER_ROLE_SLUG}
)


def _generate_token() -> str:
    return secrets.token_urlsafe(48)[:64]


def _ensure_role_scope_matches(role: DBRole, workspace_id: uuid.UUID | None) -> None:
    """Reject a grant whose role scope does not match its target."""
    if role.slug in WORKSPACE_PRESET_ROLE_SLUGS and workspace_id is None:
        raise TracecatValidationError(
            f"Role '{role.name}' is workspace-scoped and requires a workspace"
        )
    if role.slug in ORG_PRESET_ROLE_SLUGS and workspace_id is not None:
        raise TracecatValidationError(
            f"Role '{role.name}' is organization-scoped and cannot target a workspace"
        )


def invitation_read_options():
    """Eager-load options for grant role and workspace names."""
    return (
        selectinload(Invitation.grants).options(
            selectinload(InvitationGrant.role_obj),
            selectinload(InvitationGrant.workspace),
        ),
    )


async def _fetch_invitation(
    session: AsyncSession, invitation_id: uuid.UUID
) -> Invitation:
    result = await session.execute(
        select(Invitation)
        .where(Invitation.id == invitation_id)
        .options(*invitation_read_options())
    )
    return result.scalar_one()


async def get_invitation_by_token(
    session: AsyncSession, token: str
) -> Invitation | None:
    """Resolve an invitation by token, with grants loaded.

    Unauthenticated callers use this for the accept page.
    """
    result = await session.execute(
        select(Invitation)
        .where(Invitation.token == token)
        .options(*invitation_read_options(), selectinload(Invitation.organization))
    )
    return result.scalar_one_or_none()


async def _org_member_role_id(
    session: AsyncSession, organization_id: OrganizationID
) -> uuid.UUID | None:
    return (
        await session.execute(
            select(DBRole.id).where(
                DBRole.organization_id == organization_id,
                DBRole.slug == ORG_MEMBER_ROLE_SLUG,
            )
        )
    ).scalar_one_or_none()


async def _apply_grants(
    session: AsyncSession,
    *,
    invitation: Invitation,
    user_id: UserID,
) -> None:
    """Insert one assignment per grant, then org presence if still absent.

    Existing assignments are never overwritten: a grant the user already holds
    at that scope is skipped.
    """
    organization_id = invitation.organization_id

    # Derived org presence covers group paths, so a group-only member keeps
    # their indirect grant instead of gaining a direct org role.
    needs_org_assignment = (
        await session.execute(
            select(OrganizationMembership.user_id).where(
                OrganizationMembership.user_id == user_id,
                OrganizationMembership.organization_id == organization_id,
            )
        )
    ).scalar_one_or_none() is None

    grants_confer_org_role = False
    for grant in invitation.grants:
        if grant.workspace_id is None:
            grants_confer_org_role = True
            await session.execute(
                pg_insert(UserRoleAssignment)
                .values(
                    organization_id=organization_id,
                    user_id=user_id,
                    workspace_id=None,
                    role_id=grant.role_id,
                )
                .on_conflict_do_nothing(
                    index_elements=[
                        UserRoleAssignment.organization_id,
                        UserRoleAssignment.user_id,
                    ],
                    index_where=UserRoleAssignment.workspace_id.is_(None),
                )
            )
            # Written for app versions that still read the legacy table.
            await session.execute(
                pg_insert(LegacyOrganizationMembership)
                .values(user_id=user_id, organization_id=organization_id)
                .on_conflict_do_nothing(
                    index_elements=[
                        LegacyOrganizationMembership.user_id,
                        LegacyOrganizationMembership.organization_id,
                    ]
                )
            )
            continue

        await session.execute(
            pg_insert(UserRoleAssignment)
            .values(
                organization_id=organization_id,
                user_id=user_id,
                workspace_id=grant.workspace_id,
                role_id=grant.role_id,
            )
            .on_conflict_do_nothing(
                index_elements=[
                    UserRoleAssignment.user_id,
                    UserRoleAssignment.workspace_id,
                ]
            )
        )
        # Written for app versions that still read the legacy table.
        await session.execute(
            pg_insert(LegacyMembership)
            .values(user_id=user_id, workspace_id=grant.workspace_id)
            .on_conflict_do_nothing(
                index_elements=[LegacyMembership.user_id, LegacyMembership.workspace_id]
            )
        )

    if not needs_org_assignment or grants_confer_org_role:
        return

    # A user invited only to workspaces may not be in the org yet.
    org_member_role_id = await _org_member_role_id(session, organization_id)
    if org_member_role_id is None:
        return
    await session.execute(
        pg_insert(UserRoleAssignment)
        .values(
            organization_id=organization_id,
            user_id=user_id,
            workspace_id=None,
            role_id=org_member_role_id,
        )
        .on_conflict_do_nothing(
            index_elements=[
                UserRoleAssignment.organization_id,
                UserRoleAssignment.user_id,
            ],
            index_where=UserRoleAssignment.workspace_id.is_(None),
        )
    )
    await session.execute(
        pg_insert(LegacyOrganizationMembership)
        .values(user_id=user_id, organization_id=organization_id)
        .on_conflict_do_nothing(
            index_elements=[
                LegacyOrganizationMembership.user_id,
                LegacyOrganizationMembership.organization_id,
            ]
        )
    )


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
        # Old pods read organization_invitation; keep the copied twin's status in step.
        await session.execute(
            update(LegacyOrganizationInvitation)
            .where(
                LegacyOrganizationInvitation.id == invitation.id,
                LegacyOrganizationInvitation.status == InvitationStatus.PENDING,
            )
            .values(status=InvitationStatus.ACCEPTED, accepted_at=now)
        )
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
    invitation = await get_invitation_by_token(session, token)
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
    if not invitation.grants:
        # No grants left to confer (workspace deleted, or written by an older version).
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
        await _apply_grants(session, invitation=invitation, user_id=user_id)
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


async def lock_invitation_email(
    session: AsyncSession, *, organization_id: OrganizationID, email: str
) -> None:
    """Serialize invitation creation per (org, email) for this transaction."""
    # No unique index because legacy rows may be duplicated.
    await session.execute(
        select(
            func.pg_advisory_xact_lock(
                func.hashtextextended(
                    f"invitation-create:{organization_id}:{email.lower()}", 0
                )
            )
        )
    )


async def create_invitation_row(
    session: AsyncSession,
    *,
    organization_id: OrganizationID,
    email: str,
    grants: Sequence[InvitationGrantCreate],
    invited_by: UserID | None,
    created_by_platform_admin: bool,
) -> Invitation:
    """Insert an invitation and its grants. Roles must already be validated.

    Does not commit.

    Raises:
        TracecatValidationError: If the email is already an org member or a
            pending unexpired invitation already exists.
    """
    await lock_invitation_email(session, organization_id=organization_id, email=email)

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

    existing = (
        (
            await session.execute(
                select(Invitation).where(
                    Invitation.organization_id == organization_id,
                    func.lower(Invitation.email) == email.lower(),
                )
            )
        )
        .scalars()
        .all()
    )
    now = datetime.now(UTC)
    for row in existing:
        if row.status == InvitationStatus.PENDING and row.expires_at >= now:
            raise TracecatValidationError(
                f"An invitation already exists for {email} in this organization"
            )
        # Expired, revoked or accepted rows are replaced.
        await session.delete(row)
    if existing:
        await session.flush()

    # workspace_id and role_id mirror the first grant so older app versions
    # can still accept; a workspace grant is preferred over the org grant.
    anchor = next((g for g in grants if g.workspace_id is not None), grants[0])
    invitation = Invitation(
        organization_id=organization_id,
        workspace_id=anchor.workspace_id,
        email=email,
        role_id=anchor.role_id,
        invited_by=invited_by,
        token=_generate_token(),
        expires_at=now + INVITATION_TTL,
        status=InvitationStatus.PENDING,
        created_by_platform_admin=created_by_platform_admin,
    )
    session.add(invitation)
    await session.flush()
    for grant in grants:
        session.add(
            InvitationGrant(
                organization_id=organization_id,
                invitation_id=invitation.id,
                workspace_id=grant.workspace_id,
                role_id=grant.role_id,
            )
        )
    await session.flush()
    return invitation


async def revoke_invitation_row(session: AsyncSession, invitation: Invitation) -> None:
    """Mark a pending invitation revoked, twin included. Does not commit.

    Raises:
        TracecatAuthorizationError: If the invitation is not pending.
    """
    if invitation.status != InvitationStatus.PENDING:
        raise TracecatAuthorizationError(
            f"Cannot revoke invitation with status '{invitation.status}'"
        )
    invitation.status = InvitationStatus.REVOKED
    # Old pods read organization_invitation; keep the copied twin's status in step.
    await session.execute(
        update(LegacyOrganizationInvitation)
        .where(
            LegacyOrganizationInvitation.id == invitation.id,
            LegacyOrganizationInvitation.status == InvitationStatus.PENDING,
        )
        .values(status=InvitationStatus.REVOKED)
    )


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
        )
        .order_by(Invitation.created_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


class InvitationService(BaseOrgService):
    """Manage organization invitations and the grants they confer."""

    service_name = "invitation"

    async def _resolve_grants(
        self, grants: Sequence[InvitationGrantCreate]
    ) -> list[tuple[InvitationGrantCreate, DBRole]]:
        """Validate every grant's role and workspace, preserving order."""
        workspace_ids = {g.workspace_id for g in grants if g.workspace_id is not None}
        if workspace_ids:
            found = set(
                (
                    await self.session.execute(
                        select(Workspace.id).where(
                            Workspace.id.in_(workspace_ids),
                            Workspace.organization_id == self.organization_id,
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

        resolved: list[tuple[InvitationGrantCreate, DBRole]] = []
        for grant in grants:
            try:
                role = await resolve_grantable_role(
                    self.session, self.role, self.organization_id, grant.role_id
                )
            except TracecatNotFoundError as e:
                raise TracecatValidationError(
                    "Invalid role ID for this organization"
                ) from e
            _ensure_role_scope_matches(role, grant.workspace_id)
            resolved.append((grant, role))
        return resolved

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

        await lock_invitation_email(
            self.session, organization_id=self.organization_id, email=params.email
        )
        await self._resolve_grants(params.grants)
        invitation = await create_invitation_row(
            self.session,
            organization_id=self.organization_id,
            email=params.email,
            grants=params.grants,
            invited_by=self.role.user_id,
            created_by_platform_admin=self.role.is_platform_superuser,
        )
        await self.session.commit()
        return await _fetch_invitation(self.session, invitation.id)

    async def list_invitations(
        self, *, status: InvitationStatus | None = None
    ) -> Sequence[Invitation]:
        """List invitations for the organization, newest first."""
        statement = select(Invitation).where(
            Invitation.organization_id == self.organization_id
        )
        if status is not None:
            statement = statement.where(Invitation.status == status)
        statement = statement.options(*invitation_read_options()).order_by(
            Invitation.created_at.desc()
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
            .options(*invitation_read_options())
        )
        return result.scalar_one()

    async def accept_invitation(self, token: str) -> Invitation:
        """Accept an invitation on behalf of the authenticated caller."""
        if self.role.user_id is None:
            raise TracecatAuthorizationError(
                "User must be authenticated to accept invitation"
            )
        return await accept_invitation_for_user(
            self.session, user_id=self.role.user_id, token=token
        )

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
        return await _fetch_invitation(self.session, invitation_id)
