from __future__ import annotations

import secrets
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from tracecat.audit.enums import AuditEventStatus
from tracecat.audit.logger import audit_log
from tracecat.audit.service import AuditService
from tracecat.auth.types import Role
from tracecat.authz.controls import has_scope, require_scope
from tracecat.db.models import (
    Invitation,
    Membership,
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
from tracecat.identifiers import InvitationID, UserID, WorkspaceID
from tracecat.invitations.enums import InvitationStatus
from tracecat.service import BaseOrgService

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from tracecat.workspaces.schemas import (
        WorkspaceAddMemberRequest,
        WorkspaceAddMemberResponse,
        WorkspaceInvitationCreate,
    )


def _generate_invitation_token() -> str:
    """Generate a unique 64-character token for invitation magic links."""
    return secrets.token_urlsafe(48)[:64]


# === Standalone acceptance functions ===
# These don't require org context because the user may not belong to the org yet.


async def accept_org_invitation_for_user(
    session: AsyncSession,
    *,
    user_id: UserID,
    token: str,
) -> OrganizationMembership:
    """Accept an org-level invitation and create organization membership + RBAC assignment.

    Uses optimistic locking via conditional UPDATE to prevent TOCTOU race conditions.

    Raises:
        TracecatNotFoundError: If the invitation doesn't exist.
        TracecatAuthorizationError: If expired, revoked, already accepted,
            email mismatch, or wrong invitation type.
    """
    invitation_result = await session.execute(
        select(Invitation).where(Invitation.token == token)
    )
    invitation = invitation_result.scalar_one_or_none()
    if invitation is None:
        raise TracecatNotFoundError("Invitation not found")

    # Must be an org-level invitation (workspace_id IS NULL)
    if invitation.workspace_id is not None:
        raise TracecatAuthorizationError(
            "This is a workspace invitation, not an organization invitation"
        )

    user_result = await session.execute(
        select(User).where(User.id == user_id)  # pyright: ignore[reportArgumentType]
    )
    user = user_result.scalar_one_or_none()
    if user is None:
        raise TracecatAuthorizationError("User not found")

    if user.email.lower() != invitation.email.lower():
        raise TracecatAuthorizationError(
            "This invitation was sent to a different email address"
        )

    if invitation.expires_at < datetime.now(UTC):
        raise TracecatAuthorizationError("Invitation has expired")

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
        now = datetime.now(UTC)
        update_result = await session.execute(
            update(Invitation)
            .where(
                Invitation.id == invitation.id,
                Invitation.status == InvitationStatus.PENDING,
            )
            .values(status=InvitationStatus.ACCEPTED, accepted_at=now)
        )

        if update_result.rowcount == 0:  # pyright: ignore[reportAttributeAccessIssue]
            await session.refresh(invitation)
            if invitation.status == InvitationStatus.ACCEPTED:
                raise TracecatAuthorizationError("Invitation has already been accepted")
            if invitation.status == InvitationStatus.REVOKED:
                raise TracecatAuthorizationError("Invitation has been revoked")
            raise TracecatAuthorizationError("Invitation is no longer valid")

        membership = OrganizationMembership(
            user_id=user_id,
            organization_id=invitation.organization_id,
        )
        session.add(membership)

        assignment = UserRoleAssignment(
            organization_id=invitation.organization_id,
            user_id=user_id,
            workspace_id=None,
            role_id=invitation.role_id,
        )
        session.add(assignment)

        await session.commit()
        await session.refresh(membership)
    except TracecatAuthorizationError:
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

    return membership


async def accept_workspace_invitation_for_user(
    session: AsyncSession,
    *,
    user_id: UserID,
    token: str,
) -> Membership:
    """Accept a workspace invitation and create membership + RBAC assignment.

    Uses optimistic locking via conditional UPDATE to prevent TOCTOU race conditions.
    """
    invitation_result = await session.execute(
        select(Invitation)
        .where(Invitation.token == token)
        .options(
            selectinload(Invitation.workspace),
            selectinload(Invitation.inviter),
        )
    )
    invitation = invitation_result.scalar_one_or_none()
    if invitation is None:
        raise TracecatNotFoundError("Invitation not found")

    # Must be a workspace-level invitation (workspace_id IS NOT NULL)
    if invitation.workspace_id is None:
        raise TracecatValidationError(
            "This is an organization invitation, not a workspace invitation"
        )

    user_result = await session.execute(
        select(User).where(User.id == user_id)  # pyright: ignore[reportArgumentType]
    )
    user = user_result.scalar_one_or_none()
    if user is None:
        raise TracecatValidationError("User not found")
    if user.email.lower() != invitation.email.lower():
        raise TracecatValidationError(
            "This invitation was sent to a different email address"
        )

    if invitation.expires_at < datetime.now(UTC):
        raise TracecatValidationError("Invitation has expired")

    organization_id = invitation.organization_id

    audit_role = Role(
        type="user",
        user_id=user_id,
        organization_id=organization_id,
        service_id="tracecat-api",
    )

    async with AuditService.with_session(audit_role, session=session) as svc:
        await svc.create_event(
            resource_type="workspace_invitation",
            action="accept",
            resource_id=invitation.id,
            status=AuditEventStatus.ATTEMPT,
        )

    try:
        now = datetime.now(UTC)
        update_result = await session.execute(
            update(Invitation)
            .where(
                Invitation.id == invitation.id,
                Invitation.status == InvitationStatus.PENDING,
            )
            .values(status=InvitationStatus.ACCEPTED, accepted_at=now)
        )

        if update_result.rowcount == 0:  # pyright: ignore[reportAttributeAccessIssue]
            await session.refresh(invitation)
            if invitation.status == InvitationStatus.ACCEPTED:
                raise TracecatValidationError("Invitation has already been accepted")
            if invitation.status == InvitationStatus.REVOKED:
                raise TracecatValidationError("Invitation has been revoked")
            raise TracecatValidationError("Invitation is no longer valid")

        # Auto-create org membership if needed
        org_membership_stmt = select(OrganizationMembership).where(
            OrganizationMembership.user_id == user_id,
            OrganizationMembership.organization_id == organization_id,
        )
        result = await session.execute(org_membership_stmt)
        org_membership = result.scalar_one_or_none()

        created_org_membership = False
        if org_membership is None:
            org_membership = OrganizationMembership(
                user_id=user_id,
                organization_id=organization_id,
            )
            session.add(org_membership)
            created_org_membership = True
            await session.flush()

        # Check if already a workspace member
        ws_membership_stmt = select(Membership).where(
            Membership.user_id == user_id,
            Membership.workspace_id == invitation.workspace_id,
        )
        result = await session.execute(ws_membership_stmt)
        if result.scalar_one_or_none():
            raise TracecatValidationError("User is already a member of this workspace")

        membership = Membership(
            user_id=user_id,
            workspace_id=invitation.workspace_id,
        )
        session.add(membership)

        ws_assignment = UserRoleAssignment(
            organization_id=organization_id,
            user_id=user_id,
            workspace_id=invitation.workspace_id,
            role_id=invitation.role_id,
        )
        session.add(ws_assignment)

        # If we auto-created org membership, also assign org-member RBAC role
        if created_org_membership:
            org_member_role_result = await session.execute(
                select(DBRole).where(
                    DBRole.organization_id == organization_id,
                    DBRole.slug == "organization-member",
                )
            )
            org_member_role = org_member_role_result.scalar_one_or_none()
            if org_member_role is not None:
                org_assignment = UserRoleAssignment(
                    organization_id=organization_id,
                    user_id=user_id,
                    workspace_id=None,
                    role_id=org_member_role.id,
                )
                session.add(org_assignment)

        await session.commit()
        await session.refresh(membership)
    except TracecatValidationError:
        raise
    except Exception:
        async with AuditService.with_session(audit_role, session=session) as svc:
            await svc.create_event(
                resource_type="workspace_invitation",
                action="accept",
                resource_id=invitation.id,
                status=AuditEventStatus.FAILURE,
            )
        raise

    async with AuditService.with_session(audit_role, session=session) as svc:
        await svc.create_event(
            resource_type="workspace_invitation",
            action="accept",
            resource_id=invitation.id,
            status=AuditEventStatus.SUCCESS,
        )

    return membership


class InvitationService(BaseOrgService):
    """Consolidated invitation service for both org-level and workspace-level invitations."""

    service_name = "invitation"

    # === Organization-level invitations ===

    @require_scope("org:member:invite")
    @audit_log(resource_type="organization_invitation", action="create")
    async def create_org_invitation(
        self,
        *,
        email: str,
        role_id: uuid.UUID,
        workspace_assignments: list[tuple[uuid.UUID, uuid.UUID]] | None = None,
    ) -> Invitation:
        """Create an invitation to join the organization.

        Args:
            email: Email address of the invitee.
            role_id: RBAC role to assign upon acceptance.
            workspace_assignments: Optional list of (workspace_id, role_id) tuples
                to also create workspace invitations.

        Returns:
            The created invitation record.
        """
        if self.role is None or self.role.user_id is None:
            raise TracecatAuthorizationError(
                "User must be authenticated to create invitation"
            )

        email = email.lower()

        # Validate role_id exists and belongs to this organization
        role_result = await self.session.execute(
            select(DBRole).where(
                DBRole.id == role_id,
                DBRole.organization_id == self.organization_id,
            )
        )
        role_obj = role_result.scalar_one_or_none()
        if role_obj is None:
            raise TracecatValidationError("Invalid role ID for this organization")

        # Prevent privilege escalation
        if role_obj.slug == "organization-owner":
            if not self.role.is_superuser and not has_scope(
                self.role.scopes or frozenset(), "org:owner:assign"
            ):
                raise TracecatAuthorizationError(
                    "Only organization owners can create owner invitations"
                )

        # Check if user is already a member
        existing_member_stmt = (
            select(OrganizationMembership)
            .join(User, OrganizationMembership.user_id == User.id)
            .where(
                OrganizationMembership.organization_id == self.organization_id,
                func.lower(User.email) == email.lower(),
            )
        )
        existing_member_result = await self.session.execute(existing_member_stmt)
        if existing_member_result.scalar_one_or_none() is not None:
            raise TracecatValidationError(
                f"{email} is already a member of this organization"
            )

        # Check for existing invitation
        existing_stmt = select(Invitation).where(
            Invitation.organization_id == self.organization_id,
            Invitation.workspace_id.is_(None),
            func.lower(Invitation.email) == email.lower(),
        )
        existing_result = await self.session.execute(existing_stmt)
        existing = existing_result.scalar_one_or_none()

        if existing:
            if (
                existing.status == InvitationStatus.PENDING
                and existing.expires_at >= datetime.now(UTC)
            ):
                raise TracecatValidationError(
                    f"An invitation already exists for {email} in this organization"
                )
            await self.session.delete(existing)
            await self.session.flush()

        expires_at = datetime.now(UTC) + timedelta(days=7)
        invitation = Invitation(
            organization_id=self.organization_id,
            workspace_id=None,
            email=email,
            role_id=role_id,
            invited_by=self.role.user_id,
            token=_generate_invitation_token(),
            expires_at=expires_at,
            status=InvitationStatus.PENDING,
        )
        self.session.add(invitation)

        # Create workspace invitations if workspace_assignments provided
        if workspace_assignments:
            for ws_id, ws_role_id in workspace_assignments:
                ws_invitation = Invitation(
                    organization_id=self.organization_id,
                    workspace_id=ws_id,
                    email=email,
                    role_id=ws_role_id,
                    invited_by=self.role.user_id,
                    token=_generate_invitation_token(),
                    expires_at=expires_at,
                    status=InvitationStatus.PENDING,
                )
                self.session.add(ws_invitation)

        await self.session.commit()
        result = await self.session.execute(
            select(Invitation)
            .where(Invitation.id == invitation.id)
            .options(selectinload(Invitation.role_obj))
        )
        return result.scalar_one()

    async def list_org_invitations(
        self,
        *,
        status: InvitationStatus | None = None,
    ) -> Sequence[Invitation]:
        """List org-level invitations (workspace_id IS NULL)."""
        statement = select(Invitation).where(
            Invitation.organization_id == self.organization_id,
            Invitation.workspace_id.is_(None),
        )
        if status is not None:
            statement = statement.where(Invitation.status == status)
        statement = statement.options(selectinload(Invitation.role_obj))
        result = await self.session.execute(statement)
        return result.scalars().all()

    async def get_org_invitation(self, invitation_id: uuid.UUID) -> Invitation:
        """Get an org-level invitation by ID (must belong to this organization).

        Raises:
            NoResultFound: If the invitation doesn't exist or belongs to another org.
        """
        from sqlalchemy import and_

        statement = select(Invitation).where(
            and_(
                Invitation.id == invitation_id,
                Invitation.organization_id == self.organization_id,
                Invitation.workspace_id.is_(None),
            )
        )
        result = await self.session.execute(statement)
        return result.scalar_one()

    async def get_org_invitation_by_token(self, token: str) -> Invitation:
        """Get an org-level invitation by its unique token.

        Raises:
            TracecatNotFoundError: If no invitation with the token exists.
        """
        statement = select(Invitation).where(
            Invitation.token == token,
            Invitation.workspace_id.is_(None),
        )
        result = await self.session.execute(statement)
        invitation = result.scalar_one_or_none()
        if invitation is None:
            raise TracecatNotFoundError("Invitation not found")
        return invitation

    @require_scope("org:member:invite")
    @audit_log(resource_type="organization_invitation", action="revoke")
    async def revoke_org_invitation(self, invitation_id: uuid.UUID) -> Invitation:
        """Revoke a pending org-level invitation."""
        invitation = await self.get_org_invitation(invitation_id)

        if invitation.status != InvitationStatus.PENDING:
            raise TracecatAuthorizationError(
                f"Cannot revoke invitation with status '{invitation.status}'"
            )

        invitation.status = InvitationStatus.REVOKED
        await self.session.commit()
        await self.session.refresh(invitation)
        return invitation

    # === Workspace-level invitations ===

    @require_scope("workspace:member:invite")
    @audit_log(resource_type="workspace_invitation", action="create")
    async def create_workspace_invitation(
        self,
        workspace_id: WorkspaceID,
        params: WorkspaceAddMemberRequest,
    ) -> WorkspaceAddMemberResponse:
        """Create a workspace invitation (or direct membership).

        If the email belongs to an existing org member, creates a direct
        membership.  Otherwise, creates a token-based invitation.
        """
        from tracecat.authz.service import MembershipService
        from tracecat.workspaces.schemas import (
            WorkspaceAddMemberResponse,
            WorkspaceInvitationCreate,
            WorkspaceInvitationRead,
            WorkspaceMembershipCreate,
        )

        email = params.email.lower()

        # Resolve org_id from workspace
        ws_result = await self.session.execute(
            select(Workspace.organization_id).where(Workspace.id == workspace_id)
        )
        ws_org_id = ws_result.scalar_one_or_none()
        if ws_org_id is None:
            raise TracecatValidationError("Workspace not found")

        # Check if email belongs to an existing org member
        user_result = await self.session.execute(
            select(User)
            .join(
                OrganizationMembership,
                OrganizationMembership.user_id == User.id,  # pyright: ignore[reportArgumentType]
            )
            .where(
                func.lower(User.email) == email,
                OrganizationMembership.organization_id == ws_org_id,
            )
        )
        existing_user = user_result.scalar_one_or_none()

        if existing_user is not None:
            # Direct membership — user is already in the org
            membership_service = MembershipService(self.session, role=self.role)
            await membership_service.create_membership(
                workspace_id,
                params=WorkspaceMembershipCreate(
                    user_id=existing_user.id,
                    role_id=params.role_id,
                ),
            )
            return WorkspaceAddMemberResponse(outcome="membership_created")

        # External user — create email invitation
        invitation = await self.create_email_invitation(
            workspace_id,
            params=WorkspaceInvitationCreate(
                email=params.email,
                role_id=params.role_id,
            ),
        )
        return WorkspaceAddMemberResponse(
            outcome="invitation_created",
            invitation=WorkspaceInvitationRead(
                id=invitation.id,
                workspace_id=workspace_id,
                email=invitation.email,
                role_id=str(invitation.role_id),
                role_name=invitation.role_obj.name,
                role_slug=invitation.role_obj.slug,
                status=invitation.status,
                invited_by=invitation.invited_by,
                expires_at=invitation.expires_at,
                accepted_at=invitation.accepted_at,
                created_at=invitation.created_at,
                token=invitation.token,
            ),
        )

    async def create_email_invitation(
        self,
        workspace_id: WorkspaceID,
        params: WorkspaceInvitationCreate,
    ) -> Invitation:
        """Create a new workspace invitation."""
        email = params.email.lower()

        try:
            role_id = uuid.UUID(params.role_id)
        except ValueError as e:
            raise TracecatValidationError("Invalid role ID format") from e

        # Validate role_id exists and belongs to this organization
        role_result = await self.session.execute(
            select(DBRole).where(
                DBRole.id == role_id,
                DBRole.organization_id == self.organization_id,
            )
        )
        role_obj = role_result.scalar_one_or_none()
        if role_obj is None:
            raise TracecatValidationError("Invalid role ID for this organization")

        # Derive organization_id from workspace
        ws_result = await self.session.execute(
            select(Workspace.organization_id).where(Workspace.id == workspace_id)
        )
        ws_org_id = ws_result.scalar_one_or_none()
        if ws_org_id is None:
            raise TracecatValidationError("Workspace not found")

        # Check for existing pending invitation that hasn't expired
        now = datetime.now(UTC)
        existing_stmt = select(Invitation).where(
            Invitation.workspace_id == workspace_id,
            Invitation.email == email,
            Invitation.status == InvitationStatus.PENDING,
            Invitation.expires_at > now,
        )
        existing = await self.session.execute(existing_stmt)
        if existing.scalar_one_or_none():
            raise TracecatValidationError(
                f"A pending invitation already exists for {email}"
            )

        expires_at = datetime.now(UTC) + timedelta(days=7)

        invitation = Invitation(
            organization_id=ws_org_id,
            workspace_id=workspace_id,
            email=email,
            role_id=role_id,
            status=InvitationStatus.PENDING,
            invited_by=self.role.user_id if self.role else None,
            token=_generate_invitation_token(),
            expires_at=expires_at,
        )
        self.session.add(invitation)

        try:
            await self.session.flush()
        except IntegrityError as e:
            await self.session.rollback()
            if "uq_invitation_workspace_email" in str(e):
                existing_stmt = select(Invitation).where(
                    Invitation.workspace_id == workspace_id,
                    Invitation.email == params.email,
                )
                result = await self.session.execute(existing_stmt)
                existing_invitation = result.scalar_one_or_none()

                if existing_invitation and existing_invitation.expires_at < now:
                    await self.session.delete(existing_invitation)
                    await self.session.commit()
                    return await self.create_email_invitation(workspace_id, params)

                raise TracecatValidationError(
                    f"An invitation already exists for {email} in this workspace"
                ) from e
            raise

        await self.session.commit()
        result = await self.session.execute(
            select(Invitation)
            .where(Invitation.id == invitation.id)
            .options(selectinload(Invitation.role_obj))
        )
        return result.scalar_one()

    async def list_workspace_invitations(
        self,
        workspace_id: WorkspaceID,
        status: InvitationStatus | None = None,
    ) -> Sequence[Invitation]:
        """List invitations for a workspace."""
        statement = select(Invitation).where(Invitation.workspace_id == workspace_id)
        if status is not None:
            statement = statement.where(Invitation.status == status)
        statement = statement.options(selectinload(Invitation.role_obj)).order_by(
            Invitation.created_at.desc()
        )
        result = await self.session.execute(statement)
        return result.scalars().all()

    async def get_workspace_invitation_by_token(self, token: str) -> Invitation | None:
        """Retrieve a workspace invitation by its unique token."""
        statement = (
            select(Invitation)
            .where(
                Invitation.token == token,
                Invitation.workspace_id.is_not(None),
            )
            .options(
                selectinload(Invitation.workspace),
                selectinload(Invitation.inviter),
                selectinload(Invitation.role_obj),
            )
        )
        result = await self.session.execute(statement)
        return result.scalar_one_or_none()

    async def get_workspace_invitation(
        self,
        workspace_id: WorkspaceID,
        invitation_id: InvitationID,
    ) -> Invitation | None:
        """Fetch a single invitation by ID within a workspace."""
        statement = select(Invitation).where(
            Invitation.id == invitation_id,
            Invitation.workspace_id == workspace_id,
        )
        result = await self.session.execute(statement)
        return result.scalar_one_or_none()

    @require_scope("workspace:member:remove")
    @audit_log(resource_type="workspace_invitation", action="revoke")
    async def revoke_workspace_invitation(
        self,
        workspace_id: WorkspaceID,
        invitation_id: InvitationID,
    ) -> None:
        """Revoke a pending workspace invitation."""
        statement = select(Invitation).where(
            Invitation.id == invitation_id,
            Invitation.workspace_id == workspace_id,
        )
        result = await self.session.execute(statement)
        invitation = result.scalar_one_or_none()

        if invitation is None:
            raise TracecatNotFoundError("Invitation not found")

        if invitation.status != InvitationStatus.PENDING:
            raise TracecatValidationError(
                f"Cannot revoke invitation with status '{invitation.status}'"
            )

        invitation.status = InvitationStatus.REVOKED
        await self.session.commit()
