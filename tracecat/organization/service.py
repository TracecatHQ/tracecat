from __future__ import annotations

import secrets
import uuid
from collections.abc import AsyncGenerator, Sequence
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, cast, select
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import contains_eager

from tracecat.audit.logger import audit_log
from tracecat.auth.schemas import SessionRead, UserUpdate
from tracecat.auth.types import AccessLevel
from tracecat.auth.users import (
    UserManager,
    get_user_db_context,
    get_user_manager_context,
)
from tracecat.authz.controls import require_access_level
from tracecat.authz.enums import OrgRole
from tracecat.db.models import (
    AccessToken,
    OrganizationInvitation,
    OrganizationMembership,
    User,
)
from tracecat.exceptions import (
    TracecatAuthorizationError,
    TracecatNotFoundError,
    TracecatValidationError,
)
from tracecat.identifiers import OrganizationID, SessionID, UserID
from tracecat.invitations.enums import InvitationStatus
from tracecat.service import BaseService


class OrgService(BaseService):
    """Manage the organization."""

    service_name = "org"

    @asynccontextmanager
    async def _manager(self) -> AsyncGenerator[UserManager, None]:
        async with get_user_db_context(self.session) as user_db:
            async with get_user_manager_context(user_db) as user_manager:
                yield user_manager

    # === Manage members ===

    @require_access_level(AccessLevel.ADMIN)
    async def list_members(self) -> Sequence[User]:
        """
        Retrieve a list of all members in the organization.

        This method queries the database to obtain all user records
        associated with the organization via OrganizationMembership.

        Returns:
            Sequence[User]: A sequence containing User objects of all
            members in the organization.
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

    @require_access_level(AccessLevel.ADMIN)
    async def get_member(self, user_id: UserID) -> User:
        """Retrieve a member of the organization by their user ID.

        Args:
            user_id (UserID): The unique identifier of the user.

        Returns:
            User: The user object representing the member of the organization.

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

    @audit_log(resource_type="organization_member", action="delete")
    @require_access_level(AccessLevel.ADMIN)
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

    @audit_log(resource_type="organization_member", action="update")
    @require_access_level(AccessLevel.ADMIN)
    async def update_member(self, user_id: UserID, params: UserUpdate) -> User:
        """
        Update a member of the organization.

        This method updates the details of a specified member within the organization.
        It checks if the member is a superuser and raises an authorization error if so.

        Args:
            user_id (UserID): The unique identifier of the user to be updated.
            params (UserUpdate): The parameters containing the updated user information.

        Returns:
            User: The updated user object representing the member of the organization.

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
        role: OrgRole = OrgRole.MEMBER,
    ) -> OrganizationMembership:
        """Add a user to an organization.

        This method creates an OrganizationMembership record linking a user
        to an organization. It is typically called from the invitation flow
        when a user accepts an invitation.

        Note: This method does not require access level checks as it is
        intended to be called by internal services (e.g., invitation service).

        Args:
            user_id: The unique identifier of the user to add.
            organization_id: The unique identifier of the organization.
            role: The role to assign to the user in the organization.
                Defaults to OrgRole.MEMBER.

        Returns:
            OrganizationMembership: The created membership record.
        """
        membership = OrganizationMembership(
            user_id=user_id,
            organization_id=organization_id,
            role=role,
        )
        self.session.add(membership)
        await self.session.commit()
        await self.session.refresh(membership)
        return membership

    # === Manage settings ===

    @require_access_level(AccessLevel.ADMIN)
    async def get_settings(self) -> dict[str, str]:
        """Get the organization settings."""
        raise NotImplementedError

    # === Manage sessions ===

    @require_access_level(AccessLevel.ADMIN)
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

    @audit_log(resource_type="organization_session", action="delete")
    @require_access_level(AccessLevel.ADMIN)
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

    @audit_log(resource_type="organization_invitation", action="create")
    @require_access_level(AccessLevel.ADMIN)
    async def create_invitation(
        self,
        *,
        email: str,
        role: OrgRole = OrgRole.MEMBER,
    ) -> OrganizationInvitation:
        """Create an invitation to join the organization.

        Args:
            email: Email address of the invitee.
            role: Role to grant upon acceptance. Defaults to MEMBER.

        Returns:
            OrganizationInvitation: The created invitation record.
        """
        if self.role is None or self.role.user_id is None:
            raise TracecatAuthorizationError(
                "User must be authenticated to create invitation"
            )

        # Check for existing invitation
        existing_stmt = select(OrganizationInvitation).where(
            OrganizationInvitation.organization_id == self.organization_id,
            OrganizationInvitation.email == email,
        )
        existing_result = await self.session.execute(existing_stmt)
        existing = existing_result.scalar_one_or_none()

        if existing:
            if existing.expires_at >= datetime.now(UTC):
                raise TracecatValidationError(
                    f"An invitation already exists for {email} in this organization"
                )
            # Expired - delete it
            await self.session.delete(existing)
            await self.session.flush()

        invitation = OrganizationInvitation(
            organization_id=self.organization_id,
            email=email,
            role=role,
            invited_by=self.role.user_id,
            token=secrets.token_urlsafe(32),
            expires_at=datetime.now(UTC) + timedelta(days=7),
            status=InvitationStatus.PENDING,
        )
        self.session.add(invitation)
        await self.session.commit()
        await self.session.refresh(invitation)
        return invitation

    @require_access_level(AccessLevel.ADMIN)
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

    @require_access_level(AccessLevel.ADMIN)
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

        This method does not require access level checks as it is used
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

    @audit_log(resource_type="organization_invitation", action="accept")
    async def accept_invitation(self, token: str) -> OrganizationMembership:
        """Accept an invitation and create organization membership.

        This method validates the invitation token, checks expiry and status,
        then creates the membership record.

        Args:
            token: The unique invitation token.

        Returns:
            OrganizationMembership: The created membership record.

        Raises:
            TracecatNotFoundError: If the invitation doesn't exist.
            TracecatAuthorizationError: If the invitation is expired, revoked,
                or already accepted, or if the user is not authenticated.
        """
        if self.role is None or self.role.user_id is None:
            raise TracecatAuthorizationError(
                "User must be authenticated to accept invitation"
            )

        invitation = await self.get_invitation_by_token(token)

        # Validate invitation state
        if invitation.status == InvitationStatus.ACCEPTED:
            raise TracecatAuthorizationError("Invitation has already been accepted")
        if invitation.status == InvitationStatus.REVOKED:
            raise TracecatAuthorizationError("Invitation has been revoked")
        if invitation.expires_at < datetime.now(UTC):
            raise TracecatAuthorizationError("Invitation has expired")

        # Create membership
        membership = await self.add_member(
            user_id=self.role.user_id,
            organization_id=invitation.organization_id,
            role=invitation.role,
        )

        # Mark invitation as accepted
        invitation.status = InvitationStatus.ACCEPTED
        invitation.accepted_at = datetime.now(UTC)
        await self.session.commit()

        return membership

    @audit_log(resource_type="organization_invitation", action="revoke")
    @require_access_level(AccessLevel.ADMIN)
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
