from __future__ import annotations

from collections.abc import AsyncGenerator, Sequence
from contextlib import asynccontextmanager
from typing import Any
from typing import cast as type_cast

from sqlalchemy import Select, String, and_, cast, delete, literal, select
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import contains_eager

from tracecat.audit.logger import audit_log
from tracecat.auth.schemas import SessionRead, UserUpdate
from tracecat.auth.users import (
    UserManager,
    get_user_db_context,
    get_user_manager_context,
)
from tracecat.authz.controls import has_scope, require_scope
from tracecat.authz.membership import (
    drop_workspace_membership_mirror,
    lock_role_changes,
)
from tracecat.db.models import (
    AccessToken,
    ExternalGroup,
    ExternalGroupMapping,
    ExternalGroupMember,
    ExternalUser,
    Group,
    GroupMember,
    GroupRoleAssignment,
    Organization,
    OrganizationMembership,
    User,
    UserRoleAssignment,
    Workspace,
)
from tracecat.db.models import Role as DBRole
from tracecat.exceptions import (
    TracecatAuthorizationError,
    TracecatConflictError,
    TracecatNotFoundError,
)
from tracecat.identifiers import SessionID, UserID
from tracecat.organization.management import (
    delete_organization_with_cleanup,
    validate_organization_delete_confirmation,
)
from tracecat.organization.schemas import (
    MemberAccessExplain,
    MemberAccessPath,
    PathSource,
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
    async def delete_member(
        self,
        user_id: UserID,
        *,
        allow_idp_managed: bool = False,
        member: User | None = None,
        commit: bool = True,
    ) -> None:
        """
        Remove a member of the organization.

        This method removes a specified member from the current organization
        without deleting the global user record, so memberships in other
        organizations are preserved. It revokes global app sessions; deleting
        the membership row cascades the user's role paths and fires the trigger
        that revokes their organization-scoped MCP tokens. It raises an
        authorization error for superusers, as superusers cannot be removed.

        The identity provider is the source of truth for the users it manages,
        so an actively linked member cannot be removed here: the next sync would
        re-provision them and the removal would silently revert. The linkage is
        per-tenant, so another organization's admin is unaffected.

        Args:
            user_id (UserID): The unique identifier of the user to be removed.
            allow_idp_managed (bool): Bypass the guard. Reserved for the SCIM
                deprovisioning path, which removes the member precisely because
                the provider has already deprovisioned them.
            member (User | None): An already-resolved member, for a caller whose
                own preceding writes remove the last row this lookup joins on.
            commit (bool): Commit on success. The SCIM path passes ``False`` so
                deactivation and removal land in one transaction.

        Raises:
            TracecatAuthorizationError: If the user is a superuser and cannot be deleted.
            TracecatConflictError: If the user is managed by the identity
                provider and ``allow_idp_managed`` is not set.
        """
        await lock_role_changes(self.session, self.organization_id)
        user = member if member is not None else await self.get_member(user_id)
        # Checked before the provider guard: a superuser is never removable
        # here, whichever directory happens to manage them.
        if user.is_superuser:
            raise TracecatAuthorizationError("Cannot delete superuser")

        idp_managed = await self.session.scalar(
            select(
                select(ExternalUser.id)
                .where(
                    ExternalUser.user_id == user_id,
                    ExternalUser.organization_id == self.organization_id,
                    ExternalUser.active,
                )
                .exists()
            )
        )
        if idp_managed and not allow_idp_managed:
            raise TracecatConflictError(
                "Member is managed by the identity provider; deprovision them there."
            )

        await self.session.execute(
            delete(AccessToken).where(type_cast(Any, AccessToken.user_id) == user.id)
        )
        workspace_ids = select(Workspace.id).where(
            Workspace.organization_id == self.organization_id
        )
        await drop_workspace_membership_mirror(
            self.session, user_id=user.id, workspace_ids=workspace_ids
        )
        # Rows written by older app versions carry no organization_id, so the
        # composite-FK cascade below cannot reach them.
        group_ids = select(Group.id).where(
            Group.organization_id == self.organization_id
        )
        await self.session.execute(
            delete(GroupMember).where(
                GroupMember.user_id == user.id,
                GroupMember.group_id.in_(group_ids),
            )
        )
        # Deleting the aggregate root cascades assignments and group members.
        await self.session.execute(
            delete(OrganizationMembership).where(
                OrganizationMembership.user_id == user.id,
                OrganizationMembership.organization_id == self.organization_id,
            )
        )
        if commit:
            await self.session.commit()

    @require_scope("org:member:read")
    async def explain_member_access(self, user_id: UserID) -> MemberAccessExplain:
        """List every path by which a member holds a role.

        One query per role-path arm, mirroring the union in ``_role_paths``:
        direct assignments, manual group membership, and IdP group membership
        through a mapping.

        Args:
            user_id: The member whose access is being explained.

        Returns:
            The member and one entry per path, with the rows behind it.
        """
        direct = (
            select(
                UserRoleAssignment.workspace_id,
                DBRole.id,
                DBRole.name,
                literal(None, type_=UUID).label("group_id"),
                literal(None, type_=String).label("group_name"),
                literal(None, type_=UUID).label("external_group_id"),
                literal(None, type_=String).label("external_group_display_name"),
            )
            .join(DBRole, DBRole.id == UserRoleAssignment.role_id)
            .where(
                UserRoleAssignment.user_id == user_id,
                UserRoleAssignment.organization_id == self.organization_id,
            )
        )
        via_group = (
            select(
                GroupRoleAssignment.workspace_id,
                DBRole.id,
                DBRole.name,
                Group.id.label("group_id"),
                Group.name.label("group_name"),
                literal(None, type_=UUID).label("external_group_id"),
                literal(None, type_=String).label("external_group_display_name"),
            )
            .join(DBRole, DBRole.id == GroupRoleAssignment.role_id)
            .join(Group, Group.id == GroupRoleAssignment.group_id)
            .join(GroupMember, GroupMember.group_id == GroupRoleAssignment.group_id)
            .where(
                GroupMember.user_id == user_id,
                GroupRoleAssignment.organization_id == self.organization_id,
            )
        )
        via_idp = (
            select(
                GroupRoleAssignment.workspace_id,
                DBRole.id,
                DBRole.name,
                Group.id.label("group_id"),
                Group.name.label("group_name"),
                ExternalGroup.id.label("external_group_id"),
                ExternalGroup.display_name.label("external_group_display_name"),
            )
            .join(DBRole, DBRole.id == GroupRoleAssignment.role_id)
            .join(Group, Group.id == GroupRoleAssignment.group_id)
            .join(
                ExternalGroupMapping,
                ExternalGroupMapping.group_id == GroupRoleAssignment.group_id,
            )
            .join(
                ExternalGroup,
                ExternalGroup.id == ExternalGroupMapping.external_group_id,
            )
            .join(
                ExternalGroupMember,
                ExternalGroupMember.external_group_id == ExternalGroup.id,
            )
            .join(ExternalUser, ExternalUser.id == ExternalGroupMember.external_user_id)
            .where(
                ExternalUser.user_id == user_id,
                ExternalUser.active,
                GroupRoleAssignment.organization_id == self.organization_id,
            )
        )
        arms: tuple[tuple[PathSource, Select[Any]], ...] = (
            ("direct", direct),
            ("group", via_group),
            ("idp_group", via_idp),
        )
        paths: list[MemberAccessPath] = []
        for source, stmt in arms:
            rows = (await self.session.execute(stmt)).tuples().all()
            paths.extend(
                MemberAccessPath(
                    source=source,
                    workspace_id=workspace_id,
                    role_id=role_id,
                    role_name=role_name,
                    group_id=group_id,
                    group_name=group_name,
                    external_group_id=external_group_id,
                    external_group_display_name=external_group_display_name,
                )
                for (
                    workspace_id,
                    role_id,
                    role_name,
                    group_id,
                    group_name,
                    external_group_id,
                    external_group_display_name,
                ) in rows
            )
        return MemberAccessExplain(user_id=user_id, paths=paths)

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
        """List all sessions for users in this organization.

        Client metadata (IP address, user agent, last seen) is only returned
        for callers who can manage sessions (``org:member:remove``); other
        members see it for their own sessions only.
        """
        can_view_metadata = has_scope(
            self.role.scopes or frozenset(), "org:member:remove"
        )
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
        sessions: list[SessionRead] = []
        for s in result.scalars().all():
            reveal = can_view_metadata or s.user.id == self.role.user_id
            sessions.append(
                SessionRead(
                    id=s.id,
                    created_at=s.created_at,
                    user_id=s.user.id,
                    user_email=s.user.email,
                    ip_address=s.ip_address if reveal else None,
                    user_agent=s.user_agent if reveal else None,
                    last_seen_at=s.last_seen_at if reveal else None,
                )
            )
        return sessions

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
