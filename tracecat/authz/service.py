from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.auth.types import Role
from tracecat.authz.controls import require_workspace_role
from tracecat.authz.enums import WorkspaceRole
from tracecat.contexts import ctx_role
from tracecat.db.models import Membership, User, UserRoleAssignment, Workspace
from tracecat.db.models import Role as RoleModel
from tracecat.identifiers import OrganizationID, UserID, WorkspaceID
from tracecat.service import BaseService
from tracecat.workspaces.schemas import (
    WorkspaceMember,
    WorkspaceMembershipCreate,
    WorkspaceMembershipUpdate,
)

# Mapping from system role slugs to WorkspaceRole enum
SLUG_TO_WORKSPACE_ROLE: dict[str, WorkspaceRole] = {
    "admin": WorkspaceRole.ADMIN,
    "editor": WorkspaceRole.EDITOR,
    "viewer": WorkspaceRole.VIEWER,
}


def _slug_to_workspace_role(slug: str | None) -> WorkspaceRole | None:
    """Convert a role slug to a WorkspaceRole enum value."""
    if slug is None:
        return None
    return SLUG_TO_WORKSPACE_ROLE.get(slug)


@dataclass
class MembershipWithOrg:
    """Membership with organization ID."""

    membership: Membership
    org_id: OrganizationID


class MembershipService(BaseService):
    """Manage workspace memberships.

    This service optionally accepts a role for authorization-controlled methods
    (like add/update/delete membership). Methods used during the auth flow
    (like get_membership, list_user_memberships) don't require a role.
    """

    service_name = "membership"

    def __init__(self, session: AsyncSession, role: Role | None = None):
        super().__init__(session)
        self.role = role or ctx_role.get()

    async def list_memberships(self, workspace_id: WorkspaceID) -> Sequence[Membership]:
        """List all workspace memberships."""
        statement = select(Membership).where(Membership.workspace_id == workspace_id)
        result = await self.session.execute(statement)
        return result.scalars().all()

    async def list_workspace_members(
        self, workspace_id: WorkspaceID
    ) -> list[WorkspaceMember]:
        """List all workspace members with their workspace roles.

        Roles are looked up from UserRoleAssignment table.
        """
        # Get workspace to determine organization_id
        workspace = await self.session.get(Workspace, workspace_id)
        if workspace is None:
            return []
        organization_id = workspace.organization_id

        # Get all members of the workspace
        members_stmt = (
            select(User)
            .join(Membership, Membership.user_id == User.id)
            .where(Membership.workspace_id == workspace_id)
        )
        members_result = await self.session.execute(members_stmt)
        users = members_result.scalars().all()

        if not users:
            return []

        # Get role assignments for these users in this workspace
        # Include both workspace-specific assignments and org-wide assignments (workspace_id IS NULL)
        # Filter by organization_id to ensure we only get roles from this org
        user_ids = [u.id for u in users]
        role_stmt = (
            select(
                UserRoleAssignment.user_id,
                UserRoleAssignment.workspace_id,
                RoleModel.slug,
            )
            .join(RoleModel, UserRoleAssignment.role_id == RoleModel.id)
            .where(
                UserRoleAssignment.user_id.in_(user_ids),
                UserRoleAssignment.organization_id == organization_id,
                or_(
                    UserRoleAssignment.workspace_id == workspace_id,
                    UserRoleAssignment.workspace_id.is_(None),
                ),
            )
        )
        role_result = await self.session.execute(role_stmt)

        # Build map preferring workspace-specific assignments over org-wide
        user_role_map: dict[UserID, str] = {}
        for uid, ws_id, slug in role_result.tuples().all():
            if slug is None:
                # Skip assignments without a slug (custom roles)
                continue
            if ws_id is not None:
                # Workspace-specific assignment takes precedence
                user_role_map[uid] = slug
            elif uid not in user_role_map:
                # Org-wide assignment as fallback
                user_role_map[uid] = slug

        return [
            WorkspaceMember(
                user_id=user.id,
                first_name=user.first_name,
                last_name=user.last_name,
                email=user.email,
                workspace_role=_slug_to_workspace_role(user_role_map.get(user.id))
                or WorkspaceRole.VIEWER,
            )
            for user in users
        ]

    async def get_membership(
        self, workspace_id: WorkspaceID, user_id: UserID
    ) -> MembershipWithOrg | None:
        """Get a workspace membership with organization ID."""
        statement = (
            select(Membership, Workspace.organization_id)
            .join(Workspace, Membership.workspace_id == Workspace.id)
            .where(
                Membership.user_id == user_id,
                Membership.workspace_id == workspace_id,
            )
        )
        result = await self.session.execute(statement)
        row = result.first()
        if row is None:
            return None
        membership, org_id = row
        return MembershipWithOrg(membership=membership, org_id=org_id)

    async def list_user_memberships(self, user_id: UserID) -> Sequence[Membership]:
        """List all workspace memberships for a specific user.

        This is used by the authorization middleware to cache user permissions.
        """
        statement = select(Membership).where(Membership.user_id == user_id)
        result = await self.session.execute(statement)
        return result.scalars().all()

    async def list_user_memberships_with_org(
        self, user_id: UserID
    ) -> Sequence[MembershipWithOrg]:
        """List all workspace memberships for a user with organization IDs."""
        statement = (
            select(Membership, Workspace.organization_id)
            .join(Workspace, Membership.workspace_id == Workspace.id)
            .where(Membership.user_id == user_id)
        )
        result = await self.session.execute(statement)
        return [
            MembershipWithOrg(membership=membership, org_id=org_id)
            for membership, org_id in result.all()
        ]

    @require_workspace_role(WorkspaceRole.ADMIN)
    async def create_membership(
        self,
        workspace_id: WorkspaceID,
        params: WorkspaceMembershipCreate,
    ) -> None:
        """Create a workspace membership and role assignment.

        Creates both the Membership record and a UserRoleAssignment for the role.
        The role is looked up by its slug (admin, editor, viewer).

        Note: The authorization cache is request-scoped, so changes will be
        reflected in subsequent requests automatically.
        """
        # Get the workspace to find the organization_id
        workspace = await self.session.get(Workspace, workspace_id)
        if workspace is None:
            raise ValueError(f"Workspace {workspace_id} not found")

        # Look up the role by slug
        role_slug = params.role.value.lower()  # e.g., "EDITOR" -> "editor"
        role_stmt = select(RoleModel).where(
            RoleModel.organization_id == workspace.organization_id,
            RoleModel.slug == role_slug,
        )
        role_result = await self.session.execute(role_stmt)
        db_role = role_result.scalar_one_or_none()
        if db_role is None:
            raise ValueError(f"Role with slug '{role_slug}' not found")

        # Create membership
        membership = Membership(
            user_id=params.user_id,
            workspace_id=workspace_id,
        )
        self.session.add(membership)

        # Create role assignment
        role_assignment = UserRoleAssignment(
            organization_id=workspace.organization_id,
            user_id=params.user_id,
            workspace_id=workspace_id,
            role_id=db_role.id,
        )
        self.session.add(role_assignment)
        await self.session.commit()

    @require_workspace_role(WorkspaceRole.ADMIN)
    async def update_membership(
        self, membership: Membership, params: WorkspaceMembershipUpdate
    ) -> None:
        """Update a workspace membership role.

        Updates the UserRoleAssignment for the user in this workspace.

        Note: The authorization cache is request-scoped, so changes will be
        reflected in subsequent requests automatically.
        """
        if params.role is None:
            return

        # Get the workspace to find the organization_id
        workspace = await self.session.get(Workspace, membership.workspace_id)
        if workspace is None:
            raise ValueError(f"Workspace {membership.workspace_id} not found")

        # Look up the new role by slug
        role_slug = params.role.value.lower()
        role_stmt = select(RoleModel).where(
            RoleModel.organization_id == workspace.organization_id,
            RoleModel.slug == role_slug,
        )
        role_result = await self.session.execute(role_stmt)
        db_role = role_result.scalar_one_or_none()
        if db_role is None:
            raise ValueError(f"Role with slug '{role_slug}' not found")

        # Update the role assignment
        assignment_stmt = select(UserRoleAssignment).where(
            UserRoleAssignment.user_id == membership.user_id,
            UserRoleAssignment.workspace_id == membership.workspace_id,
        )
        assignment_result = await self.session.execute(assignment_stmt)
        assignment = assignment_result.scalar_one_or_none()

        if assignment:
            assignment.role_id = db_role.id
            self.session.add(assignment)
        else:
            # Create new assignment if it doesn't exist
            new_assignment = UserRoleAssignment(
                organization_id=workspace.organization_id,
                user_id=membership.user_id,
                workspace_id=membership.workspace_id,
                role_id=db_role.id,
            )
            self.session.add(new_assignment)

        await self.session.commit()

    @require_workspace_role(WorkspaceRole.ADMIN)
    async def delete_membership(
        self, workspace_id: WorkspaceID, user_id: UserID
    ) -> None:
        """Delete a workspace membership and its role assignment.

        Note: The authorization cache is request-scoped, so changes will be
        reflected in subsequent requests automatically.
        """
        if membership_with_org := await self.get_membership(workspace_id, user_id):
            # Delete the role assignment (if exists)
            assignment_stmt = select(UserRoleAssignment).where(
                UserRoleAssignment.user_id == user_id,
                UserRoleAssignment.workspace_id == workspace_id,
            )
            assignment_result = await self.session.execute(assignment_stmt)
            if assignment := assignment_result.scalar_one_or_none():
                await self.session.delete(assignment)

            # Delete the membership
            await self.session.delete(membership_with_org.membership)
            await self.session.commit()
