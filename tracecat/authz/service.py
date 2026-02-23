from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import and_, delete, func, literal, select
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.auth.types import Role
from tracecat.authz.controls import require_scope
from tracecat.contexts import ctx_role
from tracecat.db.models import Membership, User, UserRoleAssignment, Workspace
from tracecat.db.models import Role as DBRole
from tracecat.exceptions import TracecatValidationError
from tracecat.identifiers import OrganizationID, UserID, WorkspaceID
from tracecat.service import BaseService
from tracecat.workspaces.schemas import (
    WorkspaceMember,
    WorkspaceMembershipCreate,
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
        statement = (
            select(
                User,
                func.coalesce(DBRole.name, literal("Workspace Editor")).label(
                    "role_name"
                ),
            )
            .select_from(Membership)
            .join(User, Membership.user_id == User.id)  # pyright: ignore[reportArgumentType]
            .join(Workspace, Workspace.id == Membership.workspace_id)
            .outerjoin(
                UserRoleAssignment,
                and_(
                    UserRoleAssignment.user_id == User.id,  # pyright: ignore[reportArgumentType]
                    UserRoleAssignment.workspace_id == Membership.workspace_id,
                    UserRoleAssignment.organization_id == Workspace.organization_id,
                ),
            )
            .outerjoin(DBRole, DBRole.id == UserRoleAssignment.role_id)
            .where(Membership.workspace_id == workspace_id)
        )
        rows = (await self.session.execute(statement)).all()
        return [
            WorkspaceMember(
                user_id=user.id,
                first_name=user.first_name,
                last_name=user.last_name,
                email=user.email,
                role_name=role_name,
            )
            for user, role_name in rows
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
        # Resolve workspace org + default role in one DB read.
        org_role_stmt = (
            select(Workspace.organization_id, DBRole.id)
            .join(
                DBRole,
                and_(
                    DBRole.organization_id == Workspace.organization_id,
                    DBRole.slug == "workspace-editor",
                ),
            )
            .where(Workspace.id == workspace_id)
        )
        org_role_row = (await self.session.execute(org_role_stmt)).first()
        if org_role_row is None:
            raise TracecatValidationError("Workspace or default role not found")
        organization_id, role_id = org_role_row

        # Heal stale direct assignments left behind by prior failed remove flows.
        await self.session.execute(
            delete(UserRoleAssignment).where(
                UserRoleAssignment.user_id == params.user_id,
                UserRoleAssignment.workspace_id == workspace_id,
            )
        )

        membership = Membership(
            user_id=params.user_id,
            workspace_id=workspace_id,
        )
        self.session.add(membership)
        self.session.add(
            UserRoleAssignment(
                organization_id=organization_id,
                user_id=params.user_id,
                workspace_id=workspace_id,
                role_id=role_id,
                assigned_by=self.role.user_id if self.role else None,
            )
        )
        await self.session.commit()

    @require_scope("workspace:member:remove")
    async def delete_membership(
        self, workspace_id: WorkspaceID, user_id: UserID
    ) -> None:
        """Delete a workspace membership.

        Note: The authorization cache is request-scoped, so changes will be
        reflected in subsequent requests automatically.
        """
        await self.session.execute(
            delete(Membership).where(
                Membership.workspace_id == workspace_id,
                Membership.user_id == user_id,
            )
        )
        await self.session.execute(
            delete(UserRoleAssignment).where(
                UserRoleAssignment.workspace_id == workspace_id,
                UserRoleAssignment.user_id == user_id,
            )
        )
        await self.session.commit()
