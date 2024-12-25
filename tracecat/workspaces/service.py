from __future__ import annotations

from pydantic import UUID4
from sqlalchemy import func
from sqlmodel import select

from tracecat import config
from tracecat.authz.controls import require_access_level
from tracecat.authz.models import OwnerType
from tracecat.db.schemas import Membership, Ownership, User, Workspace
from tracecat.identifiers import OwnerID, UserID, WorkspaceID
from tracecat.service import BaseService
from tracecat.types.auth import AccessLevel
from tracecat.types.exceptions import TracecatException, TracecatManagementError
from tracecat.workspaces.models import SearchWorkspacesParams, UpdateWorkspaceParams


class WorkspaceService(BaseService):
    """Manage workspaces."""

    service_name = "workspace"

    @require_access_level(AccessLevel.ADMIN)
    async def admin_list_workspaces(self, limit: int | None = None) -> list[Workspace]:
        """List all workspaces in the organization."""
        statement = select(Workspace)
        if limit is not None:
            if limit <= 0:
                raise TracecatException("List workspace limit must be greater than 0")
            statement = statement.limit(limit)
        result = await self.session.exec(statement)
        return result.all()

    async def list_workspaces(
        self, user_id: UserID, limit: int | None = None
    ) -> list[Workspace]:
        """List all workspaces that a user is a member of.

        If user_id is provided, list only workspaces where user is a member.
        if user_id is None, list all workspaces.
        """
        # List workspaces where user is a member
        statement = select(Workspace).where(
            Workspace.id == Membership.workspace_id,
            Membership.user_id == user_id,
        )
        if limit is not None:
            if limit <= 0:
                raise TracecatException("List workspace limit must be greater than 0")
            statement = statement.limit(limit)
        result = await self.session.exec(statement)
        return result.all()

    async def n_workspaces(self, user_id: UserID) -> int:
        statement = select(func.count(Workspace.id)).where(
            Workspace.id == Membership.workspace_id,
            Membership.user_id == user_id,
        )
        result = await self.session.exec(statement)
        return result.one()

    @require_access_level(AccessLevel.ADMIN)
    async def create_workspace(
        self,
        name: str,
        *,
        owner_id: OwnerID = config.TRACECAT__DEFAULT_ORG_ID,
        override_id: UUID4 | None = None,
        users: list[User] | None = None,
    ) -> Workspace:
        """Create a new workspace."""
        kwargs = {
            "name": name,
            "owner_id": owner_id,
            "users": users or [],
        }
        if override_id:
            kwargs["id"] = override_id
        workspace = Workspace(**kwargs)
        self.session.add(workspace)

        # Create ownership record
        ownership = Ownership(
            resource_id=str(workspace.id),
            resource_type="workspace",
            owner_id=owner_id,
            owner_type=OwnerType.USER.value,
        )
        self.session.add(ownership)

        await self.session.commit()
        await self.session.refresh(workspace)
        return workspace

    async def get_workspace(self, workspace_id: WorkspaceID) -> Workspace | None:
        """Retrieve a workspace by ID."""
        statement = select(Workspace).where(Workspace.id == workspace_id)
        result = await self.session.exec(statement)
        return result.one_or_none()

    @require_access_level(AccessLevel.ADMIN)
    async def update_workspace(
        self, workspace_id: WorkspaceID, params: UpdateWorkspaceParams
    ) -> None:
        """Update a workspace."""
        statement = select(Workspace).where(Workspace.id == workspace_id)
        result = await self.session.exec(statement)
        workspace = result.one()
        set_fields = params.model_dump(exclude_unset=True)
        for field, value in set_fields.items():
            setattr(workspace, field, value)
        self.session.add(workspace)
        await self.session.commit()
        await self.session.refresh(workspace)

    @require_access_level(AccessLevel.ADMIN)
    async def delete_workspace(self, workspace_id: WorkspaceID) -> None:
        """Delete a workspace."""
        all_workspaces = await self.admin_list_workspaces()
        if len(all_workspaces) == 1:
            raise TracecatManagementError(
                "There must be at least one workspace in the organization."
            )
        statement = select(Workspace).where(Workspace.id == workspace_id)
        result = await self.session.exec(statement)
        workspace = result.one()
        await self.session.delete(workspace)
        await self.session.commit()

    async def search_workspaces(
        self, params: SearchWorkspacesParams
    ) -> list[Workspace]:
        """Retrieve a workspace by ID."""
        statement = select(Workspace)
        if self.role.access_level < AccessLevel.ADMIN:
            # Only list workspaces where user is a member
            statement = statement.where(
                Workspace.id == Membership.workspace_id,
                Membership.user_id == self.role.user_id,
            )
        if params.name:
            statement = statement.where(Workspace.name == params.name)
        result = await self.session.exec(statement)
        return result.all()
