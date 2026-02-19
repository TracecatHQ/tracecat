from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.auth.types import Role
from tracecat.authz.controls import require_scope
from tracecat.contexts import ctx_role
from tracecat.db.models import Membership, User, UserRoleAssignment, Workspace
from tracecat.db.models import Role as DBRole
from tracecat.identifiers import OrganizationID, UserID, WorkspaceID
from tracecat.service import BaseService
from tracecat.workspaces.schemas import (
    WorkspaceMember,
    WorkspaceMembershipCreate,
    WorkspaceMembershipUpdate,
)


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
        """List all workspace members with their workspace roles from RBAC."""
        # Get workspace org_id for RBAC lookup
        ws_stmt = select(Workspace.organization_id).where(Workspace.id == workspace_id)
        ws_result = await self.session.execute(ws_stmt)
        org_id = ws_result.scalar_one_or_none()
        if org_id is None:
            return []

        # Get members
        statement = (
            select(User)
            .join(Membership, Membership.user_id == User.id)
            .where(Membership.workspace_id == workspace_id)
        )
        result = await self.session.execute(statement)
        users = result.scalars().all()

        # Build RBAC role map for workspace-scoped assignments
        user_ids = [u.id for u in users]
        rbac_stmt = (
            select(UserRoleAssignment.user_id, DBRole.name)
            .join(DBRole, DBRole.id == UserRoleAssignment.role_id)
            .where(
                UserRoleAssignment.workspace_id == workspace_id,
                UserRoleAssignment.organization_id == org_id,
                UserRoleAssignment.user_id.in_(user_ids),  # pyright: ignore[reportAttributeAccessIssue]
            )
        )
        rbac_result = await self.session.execute(rbac_stmt)
        rbac_map: dict[str, str] = {str(row[0]): row[1] for row in rbac_result.all()}

        return [
            WorkspaceMember(
                user_id=user.id,
                first_name=user.first_name,
                last_name=user.last_name,
                email=user.email,
                role_name=rbac_map.get(str(user.id), "Editor"),
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

    @require_scope("workspace:member:invite")
    async def create_membership(
        self,
        workspace_id: WorkspaceID,
        params: WorkspaceMembershipCreate,
    ) -> None:
        """Create a workspace membership.

        Note: The authorization cache is request-scoped, so changes will be
        reflected in subsequent requests automatically.
        """
        membership = Membership(
            user_id=params.user_id,
            workspace_id=workspace_id,
        )
        self.session.add(membership)
        await self.session.commit()

    @require_scope("workspace:member:update")
    async def update_membership(
        self, membership: Membership, params: WorkspaceMembershipUpdate
    ) -> None:
        """Update a workspace membership.

        Note: The authorization cache is request-scoped, so changes will be
        reflected in subsequent requests automatically.
        """
        for key, value in params.model_dump(exclude_unset=True).items():
            setattr(membership, key, value)
        self.session.add(membership)
        await self.session.commit()

    @require_scope("workspace:member:remove")
    async def delete_membership(
        self, workspace_id: WorkspaceID, user_id: UserID
    ) -> None:
        """Delete a workspace membership.

        Note: The authorization cache is request-scoped, so changes will be
        reflected in subsequent requests automatically.
        """
        if membership_with_org := await self.get_membership(workspace_id, user_id):
            await self.session.delete(membership_with_org.membership)
            await self.session.commit()
