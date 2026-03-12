from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator, Sequence
from contextlib import asynccontextmanager
from datetime import UTC, datetime

from sqlalchemy import and_, cast, select
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.exc import NoResultFound
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import contains_eager, selectinload

from tracecat.audit.logger import audit_log
from tracecat.auth.schemas import SessionRead, UserUpdate
from tracecat.auth.users import (
    UserManager,
    get_user_db_context,
    get_user_manager_context,
)
from tracecat.authz.controls import has_scope, require_scope
from tracecat.db.models import (
    AccessToken,
    Invitation,
    Organization,
    OrganizationMembership,
    User,
)
from tracecat.db.models import Role as DBRole
from tracecat.exceptions import (
    TracecatAuthorizationError,
    TracecatNotFoundError,
    TracecatValidationError,
)
from tracecat.identifiers import OrganizationID, SessionID, UserID
from tracecat.invitations.enums import InvitationStatus
from tracecat.invitations.schemas import InvitationCreate
from tracecat.invitations.service import InvitationService
from tracecat.invitations.service import (
    accept_invitation_for_user as accept_unified_invitation_for_user,
)
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
    """Backward-compatible wrapper around the unified invitation acceptance flow."""
    membership = await accept_unified_invitation_for_user(
        session,
        user_id=user_id,
        token=token,
    )
    if not isinstance(membership, OrganizationMembership):
        raise TracecatValidationError(
            "Invitation token does not target an organization"
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
    ) -> Invitation:
        """Backward-compatible org invite creation via the unified invitation service."""
        role_result = await self.session.execute(
            select(DBRole).where(
                DBRole.id == role_id,
                DBRole.organization_id == self.organization_id,
            )
        )
        role_obj = role_result.scalar_one_or_none()
        if role_obj is None:
            raise TracecatValidationError("Invalid role ID for this organization")
        if role_obj.slug == "organization-owner" and not (
            self.role.is_superuser
            or has_scope(self.role.scopes or frozenset(), "org:owner:assign")
        ):
            raise TracecatAuthorizationError(
                "Only organization owners can create owner invitations"
            )

        service = InvitationService(self.session, role=self.role)
        invitation = await service.create_invitation(
            params=InvitationCreate(
                email=email,
                role_id=role_id,
                workspace_id=None,
            )
        )
        if invitation is None:
            raise TracecatValidationError(
                f"{email} is already a member of this organization"
            )
        return invitation

    async def list_invitations(
        self,
        *,
        status: InvitationStatus | None = None,
    ) -> Sequence[Invitation]:
        """List org-scoped invitation rows for the organization."""
        service = InvitationService(self.session, role=self.role)
        invitations = await service.list_invitations(status=status)
        return [
            invitation for invitation in invitations if invitation.workspace_id is None
        ]

    async def get_invitation(self, invitation_id: uuid.UUID) -> Invitation:
        """Get an org-scoped invitation by ID."""
        service = InvitationService(self.session, role=self.role)
        try:
            invitation = await service.get_invitation(invitation_id)
        except TracecatNotFoundError as e:
            raise NoResultFound from e
        if invitation.workspace_id is not None:
            raise NoResultFound
        return invitation

    async def get_invitation_by_token(self, token: str) -> Invitation:
        """Get an org-scoped invitation by token."""
        result = await self.session.execute(
            select(Invitation)
            .where(
                Invitation.token == token,
                Invitation.workspace_id.is_(None),
            )
            .options(selectinload(Invitation.role_obj))
        )
        invitation = result.scalar_one_or_none()
        if invitation is None:
            raise TracecatNotFoundError("Invitation not found")
        return invitation

    async def accept_invitation(self, token: str) -> OrganizationMembership:
        if self.role is None or self.role.user_id is None:
            raise TracecatAuthorizationError(
                "User must be authenticated to accept invitation"
            )
        invitation = await self.get_invitation_by_token(token)
        if invitation.status == InvitationStatus.ACCEPTED:
            raise TracecatAuthorizationError("Invitation has already been accepted")
        if invitation.status == InvitationStatus.REVOKED:
            raise TracecatAuthorizationError("Invitation has been revoked")
        if invitation.expires_at < datetime.now(UTC):
            raise TracecatAuthorizationError("Invitation has expired")

        membership = await accept_unified_invitation_for_user(
            self.session,
            user_id=self.role.user_id,
            token=token,
        )
        if not isinstance(membership, OrganizationMembership):
            raise TracecatValidationError(
                "Invitation token does not target an organization"
            )
        return membership

    @require_scope("org:member:invite")
    @audit_log(resource_type="organization_invitation", action="revoke")
    async def revoke_invitation(self, invitation_id: uuid.UUID) -> Invitation:
        """Revoke an org-scoped invitation through the unified invitation service."""
        service = InvitationService(self.session, role=self.role)
        try:
            invitation = await service.get_invitation(invitation_id)
        except TracecatNotFoundError as e:
            raise NoResultFound from e
        if invitation.workspace_id is not None:
            raise NoResultFound
        if invitation.status != InvitationStatus.PENDING:
            raise TracecatAuthorizationError(
                f"Cannot revoke invitation with status '{invitation.status}'"
            )
        return await service.revoke_invitation(invitation_id)
