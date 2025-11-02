from __future__ import annotations

from collections.abc import Sequence

from pydantic import UUID4
from sqlalchemy.orm import load_only, noload
from sqlmodel import select

from tracecat import config
from tracecat.auth.types import AccessLevel
from tracecat.authz.controls import require_access_level
from tracecat.authz.enums import OwnerType
from tracecat.db.models import Membership, Ownership, User, Workspace
from tracecat.exceptions import TracecatException, TracecatManagementError
from tracecat.identifiers import OwnerID, UserID, WorkspaceID
from tracecat.service import BaseService
from tracecat.workspaces.schemas import WorkspaceSearch, WorkspaceUpdate


class WorkspaceService(BaseService):
    """Manage workspaces."""

    service_name = "workspace"
    _load_only = ["id", "name"]

    @require_access_level(AccessLevel.ADMIN)
    async def admin_list_workspaces(
        self, limit: int | None = None
    ) -> Sequence[Workspace]:
        """List all workspaces in the organization."""
        statement = select(Workspace).options(
            load_only(
                *(getattr(Workspace, f) for f in self._load_only)
            ),  # only what the route returns
            noload("*"),  # disable all relationship loaders
        )
        if limit is not None:
            if limit <= 0:
                raise TracecatException("List workspace limit must be greater than 0")
            statement = statement.limit(limit)
        result = await self.session.exec(statement)
        return result.all()

    async def list_workspaces(
        self, user_id: UserID, limit: int | None = None
    ) -> Sequence[Workspace]:
        """List all workspaces that a user is a member of."""
        statement = (
            select(Workspace)
            .where(
                Workspace.id == Membership.workspace_id, Membership.user_id == user_id
            )
            .options(
                load_only(*(getattr(Workspace, f) for f in self._load_only)),
                noload("*"),
            )
        )
        if limit is not None:
            if limit <= 0:
                raise TracecatException("List workspace limit must be greater than 0")
            statement = statement.limit(limit)
        result = await self.session.exec(statement)
        return result.all()

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
        self, workspace: Workspace, params: WorkspaceUpdate
    ) -> Workspace:
        """Update a workspace."""
        set_fields = params.model_dump(exclude_unset=True)
        self.logger.info("Updating workspace", set_fields=set_fields)
        for field, value in set_fields.items():
            setattr(workspace, field, value)
        self.session.add(workspace)
        await self.session.commit()
        await self.session.refresh(workspace)
        return workspace

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

    async def search_workspaces(self, params: WorkspaceSearch) -> Sequence[Workspace]:
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
