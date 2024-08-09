from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from pydantic import UUID4
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from tracecat import config
from tracecat.authz.models import OwnerType
from tracecat.authz.service import AuthorizationService
from tracecat.db.engine import get_async_session_context_manager
from tracecat.db.schemas import Membership, Ownership, User, Workspace
from tracecat.logging import logger
from tracecat.workspaces.models import UpdateWorkspaceParams


class WorkspaceService:
    """Manage workspaces."""

    def __init__(self, session: AsyncSession):
        self.session = session
        self.logger = logger.bind(service="workspace")

    @asynccontextmanager
    @staticmethod
    async def with_session() -> AsyncGenerator[WorkspaceService, None]:
        async with get_async_session_context_manager() as session:
            yield WorkspaceService(session)

    async def create_workspace(
        self,
        name: str,
        *,
        owner_id: UUID4 = config.TRACECAT__DEFAULT_ORG_ID,
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

        # # Add owner as a member
        # membership = Membership(user_id=owner_id, workspace_id=workspace_id)
        # self.session.add(membership)

        await self.session.commit()
        await self.session.refresh(workspace)
        return workspace

    async def get_workspace(self, workspace_id: UUID4) -> Workspace | None:
        """Retrieve a workspace by ID."""
        statement = select(Workspace).where(Workspace.id == workspace_id)
        result = await self.session.exec(statement)
        return result.one_or_none()

    async def update_workspace(
        self, workspace_id: UUID4, params: UpdateWorkspaceParams
    ) -> Workspace:
        """Update a workspace."""
        workspace = await self.get_workspace(workspace_id)
        if not workspace:
            raise ValueError(f"Workspace {workspace_id} not found for update")
        set_fields = params.model_dump(exclude_unset=True)
        for field, value in set_fields.items():
            setattr(workspace, field, value)
        self.session.add(workspace)
        await self.session.commit()
        await self.session.refresh(workspace)
        return workspace

    async def delete_workspace(self, workspace_id: UUID4) -> bool:
        """Delete a workspace."""
        workspace = await self.get_workspace(workspace_id)
        if workspace:
            await self.session.delete(workspace)
            await self.session.commit()
            self.logger.info(f"Deleted workspace {workspace_id}")
            return True
        self.logger.info(f"Workspace {workspace_id} not found for deletion")
        return False

    async def add_user_to_workspace(self, workspace_id: UUID4, user_id: UUID4) -> None:
        """Add a user to a workspace."""
        authz_service = AuthorizationService(self.session)
        is_member = await authz_service.user_is_workspace_member(
            user_id=user_id, workspace_id=workspace_id
        )
        if not is_member:
            new_membership = Membership(user_id=user_id, workspace_id=workspace_id)
            self.session.add(new_membership)
            await self.session.commit()

    async def remove_user_from_workspace(
        self, workspace_id: UUID4, user_id: UUID4
    ) -> None:
        """Remove a user from a workspace."""
        if membership := await self.get_membership(user_id, workspace_id):
            await self.session.delete(membership)
            await self.session.commit()

    async def get_workspace_members(self, workspace_id: UUID4) -> list[UUID4]:
        """Get all members of a workspace."""
        statement = select(Membership.user_id).where(
            Membership.workspace_id == workspace_id
        )
        result = await self.session.exec(statement)
        members = result.all()
        return members

    async def get_membership(
        self, workspace_id: UUID4, user_id: UUID4
    ) -> Membership | None:
        """Get a user's membership in  workspace."""
        statement = select(Membership).where(
            Membership.user_id == user_id, Membership.workspace_id == workspace_id
        )
        result = await self.session.exec(statement)
        return result.one_or_none()
