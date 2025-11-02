from __future__ import annotations

from collections.abc import Sequence

from sqlmodel import and_, select

from tracecat.authz.controls import require_workspace_role
from tracecat.authz.enums import WorkspaceRole
from tracecat.db.models import Membership, User
from tracecat.identifiers import UserID, WorkspaceID
from tracecat.service import BaseService
from tracecat.workspaces.schemas import (
    WorkspaceMember,
    WorkspaceMembershipCreate,
    WorkspaceMembershipUpdate,
)


class MembershipService(BaseService):
    """Manage workspace memberships."""

    service_name = "membership"

    async def list_memberships(self, workspace_id: WorkspaceID) -> Sequence[Membership]:
        """List all workspace memberships."""
        statement = select(Membership).where(Membership.workspace_id == workspace_id)
        result = await self.session.exec(statement)
        return result.all()

    async def list_workspace_members(
        self, workspace_id: WorkspaceID
    ) -> list[WorkspaceMember]:
        """List all workspace members."""
        statement = select(User, Membership.role).where(
            and_(
                Membership.workspace_id == workspace_id,
                Membership.user_id == User.id,
            )
        )
        result = await self.session.exec(statement)
        return [
            WorkspaceMember(
                user_id=user.id,
                first_name=user.first_name,
                last_name=user.last_name,
                email=user.email,
                org_role=user.role,
                workspace_role=WorkspaceRole(ws_role),
            )
            for user, ws_role in result.all()
        ]

    async def get_membership(
        self, workspace_id: WorkspaceID, user_id: UserID
    ) -> Membership | None:
        """Get a workspace membership."""
        statement = select(Membership).where(
            Membership.user_id == user_id, Membership.workspace_id == workspace_id
        )
        result = await self.session.exec(statement)
        return result.first()

    async def list_user_memberships(self, user_id: UserID) -> Sequence[Membership]:
        """List all workspace memberships for a specific user.

        This is used by the authorization middleware to cache user permissions.
        """
        statement = select(Membership).where(Membership.user_id == user_id)
        result = await self.session.exec(statement)
        return result.all()

    @require_workspace_role(WorkspaceRole.ADMIN)
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
            role=params.role,
        )
        self.session.add(membership)
        await self.session.commit()

    @require_workspace_role(WorkspaceRole.ADMIN)
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

    @require_workspace_role(WorkspaceRole.ADMIN)
    async def delete_membership(
        self, workspace_id: WorkspaceID, user_id: UserID
    ) -> None:
        """Delete a workspace membership.

        Note: The authorization cache is request-scoped, so changes will be
        reflected in subsequent requests automatically.
        """
        if membership := await self.get_membership(workspace_id, user_id):
            await self.session.delete(membership)
            await self.session.commit()
