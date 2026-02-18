from __future__ import annotations

import secrets
import uuid
from collections.abc import AsyncGenerator, Sequence
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, cast, func, select, update
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import contains_eager

from tracecat.audit.enums import AuditEventStatus
from tracecat.audit.logger import audit_log
from tracecat.audit.service import AuditService
from tracecat.auth.schemas import SessionRead, UserUpdate
from tracecat.auth.types import Role
from tracecat.auth.users import (
    UserManager,
    get_user_db_context,
    get_user_manager_context,
)
from tracecat.authz.controls import has_scope, require_scope
from tracecat.db.models import (
    AccessToken,
    Organization,
    OrganizationInvitation,
    OrganizationMembership,
    User,
    UserRoleAssignment,
)
from tracecat.db.models import (
    Role as RoleModel,
)
from tracecat.exceptions import (
    TracecatAuthorizationError,
    TracecatNotFoundError,
    TracecatValidationError,
)
from tracecat.identifiers import OrganizationID, SessionID, UserID
from tracecat.invitations.enums import InvitationStatus
from tracecat.organization.management import (
    delete_organization_with_cleanup,
    validate_organization_delete_confirmation,
)
from tracecat.service import BaseOrgService


async def accept_invitation_for_user(
    session: AsyncSession,
    *,
    user_id: UserID,
    token: str,
) -> OrganizationMembership:
    """Accept an invitation and create organization membership + RBAC assignment.

    This is a standalone function (not a method) because invitation acceptance
    doesn't require organization context - the user may not belong to any
    organization yet.

    Uses optimistic locking via conditional UPDATE to prevent TOCTOU race
    conditions - the status check and update happen atomically in a single
    database operation.

    Args:
        session: Database session.
        user_id: The ID of the user accepting the invitation.
        token: The unique invitation token.

    Returns:
        OrganizationMembership: The created membership record.

    Raises:
        TracecatNotFoundError: If the invitation doesn't exist.
        TracecatAuthorizationError: If the invitation is expired, revoked,
            or already accepted, or if the user's email doesn't match
            the invitation email.
    """
    # Fetch invitation by token
    invitation_result = await session.execute(
        select(OrganizationInvitation).where(OrganizationInvitation.token == token)
    )
    invitation = invitation_result.scalar_one_or_none()
    if invitation is None:
        raise TracecatNotFoundError("Invitation not found")

    # Fetch user to validate email
    user_result = await session.execute(
        select(User).where(User.id == user_id)  # pyright: ignore[reportArgumentType]
    )
    user = user_result.scalar_one_or_none()
    if user is None:
        raise TracecatAuthorizationError("User not found")

    # Verify email match (case-insensitive)
    if user.email.lower() != invitation.email.lower():
        raise TracecatAuthorizationError(
            "This invitation was sent to a different email address"
        )

    # Check expiry before attempting atomic update
    if invitation.expires_at < datetime.now(UTC):
        raise TracecatAuthorizationError("Invitation has expired")

    # Create role scoped to invitation's organization for audit logging
    audit_role = Role(
        type="user",
        user_id=user_id,
        organization_id=invitation.organization_id,
        service_id="tracecat-api",
    )

    # Log audit attempt
    async with AuditService.with_session(audit_role, session=session) as svc:
        await svc.create_event(
            resource_type="organization_invitation",
            action="accept",
            resource_id=invitation.id,
            status=AuditEventStatus.ATTEMPT,
        )

    try:
        # Atomically update invitation status only if still PENDING.
        # This prevents TOCTOU race conditions where an admin might revoke
        # the invitation between our check and commit.
        now = datetime.now(UTC)
        update_result = await session.execute(
            update(OrganizationInvitation)
            .where(
                OrganizationInvitation.id == invitation.id,
                OrganizationInvitation.status == InvitationStatus.PENDING,
            )
            .values(status=InvitationStatus.ACCEPTED, accepted_at=now)
        )

        if update_result.rowcount == 0:  # pyright: ignore[reportAttributeAccessIssue]
            # Status changed between fetch and update - re-fetch for accurate error
            await session.refresh(invitation)
            if invitation.status == InvitationStatus.ACCEPTED:
                raise TracecatAuthorizationError("Invitation has already been accepted")
            if invitation.status == InvitationStatus.REVOKED:
                raise TracecatAuthorizationError("Invitation has been revoked")
            # Shouldn't reach here, but handle gracefully
            raise TracecatAuthorizationError("Invitation is no longer valid")

        # Create membership (still needed for org membership existence checks)
        membership = OrganizationMembership(
            user_id=user_id,
            organization_id=invitation.organization_id,
        )
        session.add(membership)

        # Create RBAC role assignment from invitation's role_id
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
        # Re-raise auth errors without logging as failure (expected user errors)
        raise
    except Exception:
        # Log audit failure
        async with AuditService.with_session(audit_role, session=session) as svc:
            await svc.create_event(
                resource_type="organization_invitation",
                action="accept",
                resource_id=invitation.id,
                status=AuditEventStatus.FAILURE,
            )
        raise

    # Log audit success
    async with AuditService.with_session(audit_role, session=session) as svc:
        await svc.create_event(
            resource_type="organization_invitation",
            action="accept",
            resource_id=invitation.id,
            status=AuditEventStatus.SUCCESS,
        )

    return membership


class OrgService(BaseOrgService):
    """Manage the organization."""

    service_name = "org"

    @asynccontextmanager
    async def _manager(self) -> AsyncGenerator[UserManager, None]:
        async with get_user_db_context(self.session) as user_db:
            async with get_user_manager_context(user_db) as user_manager:
                yield user_manager

    # === Manage members ===
    @require_scope("org:member:read")
    async def list_members(self) -> Sequence[User]:
        """
        Retrieve a list of all members in the organization.

        This method queries the database to obtain all user records
        associated with the organization via OrganizationMembership.

        Returns:
            Sequence[User]: A sequence of User objects.
        """
        statement = select(User).join(
            OrganizationMembership,
            and_(
                OrganizationMembership.user_id == User.id,
                OrganizationMembership.organization_id == self.organization_id,
            ),
        )
        result = await self.session.execute(statement)
        return result.scalars().all()

    async def get_member(self, user_id: UserID) -> User:
        """Retrieve a member of the organization by their user ID.

        Args:
            user_id (UserID): The unique identifier of the user.

        Returns:
            User: The user object.

        Raises:
            NoResultFound: If no user with the given ID exists in this organization.
        """
        statement = (
            select(User)
            .join(
                OrganizationMembership,
                and_(
                    OrganizationMembership.user_id == User.id,
                    OrganizationMembership.organization_id == self.organization_id,
                ),
            )
            .where(cast(User.id, UUID) == user_id)
        )
        result = await self.session.execute(statement)
        return result.scalar_one()

    @require_scope("org:member:remove")
    @audit_log(resource_type="organization_member", action="delete")
    async def delete_member(self, user_id: UserID) -> None:
        """
        Remove a member of the organization.

        This method deletes a specified member from the organization.
        It first checks if the member is a superuser and raises an
        authorization error if so, as superusers cannot be deleted.

        Args:
            user_id (UserID): The unique identifier of the user to be removed.

        Raises:
            TracecatAuthorizationError: If the user is a superuser and cannot be deleted.
        """
        user = await self.get_member(user_id)
        if user.is_superuser:
            raise TracecatAuthorizationError("Cannot delete superuser")
        async with self._manager() as user_manager:
            await user_manager.delete(user)

    @require_scope("org:member:update")
    @audit_log(resource_type="organization_member", action="update")
    async def update_member(self, user_id: UserID, params: UserUpdate) -> User:
        """
        Update a member of the organization.

        This method updates the details of a specified member within the organization.
        It checks if the member is a superuser and raises an authorization error if so.

        Args:
            user_id (UserID): The unique identifier of the user to be updated.
            params (UserUpdate): The parameters containing the updated user information.

        Returns:
            User: The updated user object.

        Raises:
            TracecatAuthorizationError: If the user is a superuser and cannot be updated.
        """
        user = await self.get_member(user_id)
        if user.is_superuser:
            raise TracecatAuthorizationError("Cannot update superuser")
        async with self._manager() as user_manager:
            updated_user = await user_manager.update(
                user_update=params, user=user, safe=True
            )
        return updated_user

    @audit_log(resource_type="organization_member", action="create")
    async def add_member(
        self,
        *,
        user_id: UserID,
        organization_id: OrganizationID,
    ) -> OrganizationMembership:
        """Add a user to an organization.

        This method creates an OrganizationMembership record linking a user
        to an organization. It is typically called from the invitation flow
        when a user accepts an invitation.

        Note: This method does not require scope checks as it is
        intended to be called by internal services (e.g., invitation service).
        RBAC role assignment is handled separately.

        Args:
            user_id: The unique identifier of the user to add.
            organization_id: The unique identifier of the organization.

        Returns:
            OrganizationMembership: The created membership record.
        """
        membership = OrganizationMembership(
            user_id=user_id,
            organization_id=organization_id,
        )
        self.session.add(membership)
        await self.session.commit()
        await self.session.refresh(membership)
        return membership

    @audit_log(resource_type="organization", action="delete")
    @require_scope("org:delete")
    async def delete_organization(self, *, confirmation: str | None) -> None:
        """Delete the current organization and all associated resources."""
        statement = select(Organization).where(Organization.id == self.organization_id)
        result = await self.session.execute(statement)
        organization = result.scalar_one_or_none()
        if organization is None:
            raise TracecatNotFoundError("Organization not found")

        validate_organization_delete_confirmation(
            organization, confirmation=confirmation
        )
        await delete_organization_with_cleanup(
            self.session,
            organization=organization,
            operator_user_id=self.role.user_id,
        )
        await self.session.commit()

    # === Manage settings ===
    async def get_settings(self) -> dict[str, str]:
        """Get the organization settings."""
        raise NotImplementedError

    # === Manage sessions ===
    async def list_sessions(self) -> list[SessionRead]:
        """List all sessions for users in this organization."""
        statement = (
            select(AccessToken)
            .join(User, cast(AccessToken.user_id, UUID) == User.id)
            .join(
                OrganizationMembership,
                and_(
                    OrganizationMembership.user_id == User.id,
                    OrganizationMembership.organization_id == self.organization_id,
                ),
            )
            .options(contains_eager(AccessToken.user))
        )
        result = await self.session.execute(statement)
        return [
            SessionRead(
                id=s.id,
                created_at=s.created_at,
                user_id=s.user.id,
                user_email=s.user.email,
            )
            for s in result.scalars().all()
        ]

    @require_scope("org:member:remove")
    @audit_log(resource_type="organization_session", action="delete")
    async def delete_session(self, session_id: SessionID) -> None:
        """Delete a session by its ID (must belong to a user in this organization)."""
        statement = (
            select(AccessToken)
            .join(User, cast(AccessToken.user_id, UUID) == User.id)
            .join(
                OrganizationMembership,
                and_(
                    OrganizationMembership.user_id == User.id,
                    OrganizationMembership.organization_id == self.organization_id,
                ),
            )
            .where(AccessToken.id == session_id)
        )
        result = await self.session.execute(statement)
        db_token = result.scalar_one()
        await self.session.delete(db_token)
        await self.session.commit()

    # === Manage invitations ===

    @require_scope("org:member:invite")
    @audit_log(resource_type="organization_invitation", action="create")
    async def create_invitation(
        self,
        *,
        email: str,
        role_id: uuid.UUID,
    ) -> OrganizationInvitation:
        """Create an invitation to join the organization.

        Args:
            email: Email address of the invitee.
            role_id: RBAC role to assign upon acceptance.

        Returns:
            OrganizationInvitation: The created invitation record.
        """
        if self.role is None or self.role.user_id is None:
            raise TracecatAuthorizationError(
                "User must be authenticated to create invitation"
            )

        # Validate role_id exists and belongs to this organization
        role_result = await self.session.execute(
            select(RoleModel).where(
                RoleModel.id == role_id,
                RoleModel.organization_id == self.organization_id,
            )
        )
        role_obj = role_result.scalar_one_or_none()
        if role_obj is None:
            raise TracecatValidationError("Invalid role ID for this organization")

        # Prevent privilege escalation: only owners (via scope) or superusers can assign owner role
        if role_obj.slug == "organization-owner":
            if not self.role.is_superuser and not has_scope(
                self.role.scopes or frozenset(), "org:owner:assign"
            ):
                raise TracecatAuthorizationError(
                    "Only organization owners can create owner invitations"
                )

        # Check if user with this email is already a member (case-insensitive)
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

        # Check for existing invitation (case-insensitive)
        existing_stmt = select(OrganizationInvitation).where(
            OrganizationInvitation.organization_id == self.organization_id,
            func.lower(OrganizationInvitation.email) == email.lower(),
        )
        existing_result = await self.session.execute(existing_stmt)
        existing = existing_result.scalar_one_or_none()

        if existing:
            # Only block if invitation is pending and not expired
            if (
                existing.status == InvitationStatus.PENDING
                and existing.expires_at >= datetime.now(UTC)
            ):
                raise TracecatValidationError(
                    f"An invitation already exists for {email} in this organization"
                )
            # Expired or revoked/accepted - delete it to allow new invitation
            await self.session.delete(existing)
            await self.session.flush()

        invitation = OrganizationInvitation(
            organization_id=self.organization_id,
            email=email,
            role_id=role_id,
            invited_by=self.role.user_id,
            token=secrets.token_urlsafe(32),
            expires_at=datetime.now(UTC) + timedelta(days=7),
            status=InvitationStatus.PENDING,
        )
        self.session.add(invitation)
        await self.session.commit()
        await self.session.refresh(invitation)
        return invitation

    async def list_invitations(
        self,
        *,
        status: InvitationStatus | None = None,
    ) -> Sequence[OrganizationInvitation]:
        """List invitations for the organization.

        Args:
            status: Optional filter by invitation status.

        Returns:
            Sequence[OrganizationInvitation]: List of invitations.
        """
        statement = select(OrganizationInvitation).where(
            OrganizationInvitation.organization_id == self.organization_id
        )
        if status is not None:
            statement = statement.where(OrganizationInvitation.status == status)
        result = await self.session.execute(statement)
        return result.scalars().all()

    async def get_invitation(self, invitation_id: uuid.UUID) -> OrganizationInvitation:
        """Get an invitation by ID (must belong to this organization).

        Args:
            invitation_id: The invitation UUID.

        Returns:
            OrganizationInvitation: The invitation record.

        Raises:
            NoResultFound: If the invitation doesn't exist or belongs to another org.
        """
        statement = select(OrganizationInvitation).where(
            and_(
                OrganizationInvitation.id == invitation_id,
                OrganizationInvitation.organization_id == self.organization_id,
            )
        )
        result = await self.session.execute(statement)
        return result.scalar_one()

    async def get_invitation_by_token(self, token: str) -> OrganizationInvitation:
        """Get an invitation by its unique token.

        This method does not require scope checks as it is used
        during the public invitation acceptance flow.

        Args:
            token: The unique invitation token.

        Returns:
            OrganizationInvitation: The invitation record.

        Raises:
            TracecatNotFoundError: If no invitation with the token exists.
        """
        statement = select(OrganizationInvitation).where(
            OrganizationInvitation.token == token
        )
        result = await self.session.execute(statement)
        invitation = result.scalar_one_or_none()
        if invitation is None:
            raise TracecatNotFoundError("Invitation not found")
        return invitation

    async def accept_invitation(self, token: str) -> OrganizationMembership:
        """Accept an invitation and create organization membership + RBAC assignment.

        This method validates the invitation token, checks expiry and status,
        then creates the membership record and RBAC role assignment.
        Audit events are logged to the invitation's target organization,
        not the user's current organization.

        Uses optimistic locking via conditional UPDATE to prevent TOCTOU race
        conditions - the status check and update happen atomically in a single
        database operation.

        Args:
            token: The unique invitation token.

        Returns:
            OrganizationMembership: The created membership record.

        Raises:
            TracecatNotFoundError: If the invitation doesn't exist.
            TracecatAuthorizationError: If the invitation is expired, revoked,
                or already accepted, if the user is not authenticated, or if
                the user's email doesn't match the invitation email.
        """
        if self.role is None or self.role.user_id is None:
            raise TracecatAuthorizationError(
                "User must be authenticated to accept invitation"
            )

        # First fetch to validate email match and get invitation details
        invitation = await self.get_invitation_by_token(token)

        # Verify user's email matches invitation email (case-insensitive)
        user_result = await self.session.execute(
            select(User).where(User.id == self.role.user_id)  # pyright: ignore[reportArgumentType]
        )
        user = user_result.scalar_one_or_none()
        if user is None:
            raise TracecatAuthorizationError("User not found")
        if user.email.lower() != invitation.email.lower():
            raise TracecatAuthorizationError(
                "This invitation was sent to a different email address"
            )

        # Check expiry before attempting atomic update
        if invitation.expires_at < datetime.now(UTC):
            raise TracecatAuthorizationError("Invitation has expired")

        # Create role scoped to invitation's organization for audit logging
        audit_role = self.role.model_copy(
            update={"organization_id": invitation.organization_id}
        )

        # Log audit attempt to the invitation's organization
        async with AuditService.with_session(audit_role, session=self.session) as svc:
            await svc.create_event(
                resource_type="organization_invitation",
                action="accept",
                resource_id=invitation.id,
                status=AuditEventStatus.ATTEMPT,
            )

        try:
            # Atomically update invitation status only if still PENDING.
            # This prevents TOCTOU race conditions where an admin might revoke
            # the invitation between our check and commit.
            now = datetime.now(UTC)
            update_result = await self.session.execute(
                update(OrganizationInvitation)
                .where(
                    OrganizationInvitation.id == invitation.id,
                    OrganizationInvitation.status == InvitationStatus.PENDING,
                )
                .values(status=InvitationStatus.ACCEPTED, accepted_at=now)
            )

            if update_result.rowcount == 0:  # pyright: ignore[reportAttributeAccessIssue]
                # Status changed between fetch and update - re-fetch for accurate error
                await self.session.refresh(invitation)
                if invitation.status == InvitationStatus.ACCEPTED:
                    raise TracecatAuthorizationError(
                        "Invitation has already been accepted"
                    )
                if invitation.status == InvitationStatus.REVOKED:
                    raise TracecatAuthorizationError("Invitation has been revoked")
                # Shouldn't reach here, but handle gracefully
                raise TracecatAuthorizationError("Invitation is no longer valid")

            # Create membership (still needed for org membership existence checks)
            membership = OrganizationMembership(
                user_id=self.role.user_id,
                organization_id=invitation.organization_id,
            )
            self.session.add(membership)

            # Create RBAC role assignment from invitation's role_id
            assignment = UserRoleAssignment(
                organization_id=invitation.organization_id,
                user_id=self.role.user_id,
                workspace_id=None,
                role_id=invitation.role_id,
            )
            self.session.add(assignment)

            await self.session.commit()
            await self.session.refresh(membership)
        except TracecatAuthorizationError:
            # Re-raise auth errors without logging as failure (expected user errors)
            raise
        except Exception:
            # Log audit failure to the invitation's organization
            async with AuditService.with_session(
                audit_role, session=self.session
            ) as svc:
                await svc.create_event(
                    resource_type="organization_invitation",
                    action="accept",
                    resource_id=invitation.id,
                    status=AuditEventStatus.FAILURE,
                )
            raise

        # Log audit success outside the try-except to avoid logging FAILURE
        # if only audit logging fails after a successful commit
        async with AuditService.with_session(audit_role, session=self.session) as svc:
            await svc.create_event(
                resource_type="organization_invitation",
                action="accept",
                resource_id=invitation.id,
                status=AuditEventStatus.SUCCESS,
            )

        return membership

    @require_scope("org:member:invite")
    @audit_log(resource_type="organization_invitation", action="revoke")
    async def revoke_invitation(
        self, invitation_id: uuid.UUID
    ) -> OrganizationInvitation:
        """Revoke a pending invitation.

        Args:
            invitation_id: The invitation UUID.

        Returns:
            OrganizationInvitation: The updated invitation record.

        Raises:
            NoResultFound: If the invitation doesn't exist or belongs to another org.
            TracecatAuthorizationError: If the invitation is not in pending status.
        """
        invitation = await self.get_invitation(invitation_id)

        if invitation.status != InvitationStatus.PENDING:
            raise TracecatAuthorizationError(
                f"Cannot revoke invitation with status '{invitation.status}'"
            )

        invitation.status = InvitationStatus.REVOKED
        await self.session.commit()
        await self.session.refresh(invitation)
        return invitation
