from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator, Sequence
from contextlib import asynccontextmanager

from sqlalchemy import and_, cast, select
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import contains_eager

from tracecat.audit.logger import audit_log
from tracecat.auth.schemas import SessionRead, UserUpdate
from tracecat.auth.users import (
    UserManager,
    get_user_db_context,
    get_user_manager_context,
)
from tracecat.authz.controls import require_scope
from tracecat.db.models import (
    AccessToken,
    Organization,
    OrganizationMembership,
    User,
    UserRoleAssignment,
    Workspace,
)
from tracecat.db.models import Role as DBRole
from tracecat.exceptions import (
    TracecatAuthorizationError,
    TracecatNotFoundError,
)
from tracecat.identifiers import OrganizationID, SessionID, UserID
from tracecat.organization.management import (
    delete_organization_with_cleanup,
    validate_organization_delete_confirmation,
)
from tracecat.service import BaseOrgService


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

    @require_scope("org:member:read")
    async def list_member_workspace_memberships(
        self,
        user_id: UserID,
    ) -> list[tuple[uuid.UUID, str, str]]:
        """List workspace memberships for an org member.

        Returns a list of (workspace_id, workspace_name, role_name) tuples.
        """
        from tracecat.db.models import Membership

        statement = (
            select(Workspace.id, Workspace.name, DBRole.name)
            .join(Membership, Membership.workspace_id == Workspace.id)
            .outerjoin(
                UserRoleAssignment,
                and_(
                    UserRoleAssignment.user_id == user_id,
                    UserRoleAssignment.workspace_id == Workspace.id,
                    UserRoleAssignment.organization_id == self.organization_id,
                ),
            )
            .outerjoin(DBRole, DBRole.id == UserRoleAssignment.role_id)
            .where(
                Membership.user_id == user_id,
                Workspace.organization_id == self.organization_id,
            )
        )
        result = await self.session.execute(statement)
        return list(result.tuples().all())

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
