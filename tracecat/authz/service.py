from __future__ import annotations

from collections.abc import Sequence

from sqlmodel import select

from tracecat.authz.controls import require_workspace_role
from tracecat.authz.models import WorkspaceRole
from tracecat.db.schemas import Membership, User
from tracecat.identifiers import UserID, WorkspaceID
from tracecat.service import BaseService
from tracecat.workspaces.models import (
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

    async def list_memberships_with_users(
        self, workspace_id: WorkspaceID
    ) -> Sequence[tuple[Membership, User]]:
        """List all workspace memberships with user details."""
        statement = select(Membership, User).where(
            Membership.workspace_id == workspace_id, Membership.user_id == User.id
        )
        result = await self.session.exec(statement)
        return result.all()

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
