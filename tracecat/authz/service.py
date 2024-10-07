from __future__ import annotations

from collections.abc import AsyncGenerator, Sequence
from contextlib import asynccontextmanager

from sqlalchemy import func
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from tracecat.authz.controls import require_access_level
from tracecat.authz.models import OwnerType
from tracecat.contexts import ctx_role
from tracecat.db.engine import get_async_session_context_manager
from tracecat.db.schemas import Membership, Ownership, User
from tracecat.identifiers import OwnerID, UserID, WorkspaceID
from tracecat.logger import logger
from tracecat.types.auth import AccessLevel, Role


class AuthorizationService:
    """Manage simple authorization operations."""

    def __init__(self, session: AsyncSession, role: Role | None = None):
        self.session = session
        self.role = role or ctx_role.get()
        self.logger = logger.bind(service="authorization")
        self._membership_service = MembershipService(session)

    @asynccontextmanager
    @staticmethod
    async def with_session(
        role: Role | None = None,
    ) -> AsyncGenerator[AuthorizationService, None]:
        async with get_async_session_context_manager() as session:
            yield AuthorizationService(session, role=role)

    async def add_resource_owner(
        self, resource_id: str, owner_id: OwnerID, resource_type: str, owner_type: str
    ) -> None:
        """Add an owner to a resource."""
        new_ownership = Ownership(
            resource_id=resource_id,
            resource_type=resource_type,
            owner_id=owner_id,
            owner_type=owner_type,
        )
        self.session.add(new_ownership)
        await self.session.commit()

    async def get_resource_owner(self, resource_id: str) -> Ownership | None:
        """Get the owner of a resource."""
        statement = select(Ownership).where(Ownership.resource_id == resource_id)
        result = await self.session.exec(statement)
        return result.one_or_none()

    async def user_is_organization_member(self, user_id: UserID) -> bool:
        """Check if a user is a member of a specific workspace."""
        statement = select(User).where(User.id == user_id)
        result = await self.session.exec(statement)
        return result.first() is not None

    async def user_is_workspace_member(
        self, user_id: UserID, workspace_id: WorkspaceID
    ) -> bool:
        """Check if a user is a member of a specific workspace."""
        statement = select(Membership).where(
            Membership.user_id == user_id, Membership.workspace_id == workspace_id
        )
        result = await self.session.exec(statement)
        membership = result.first()
        return membership is not None

    async def user_can_access_resource(self, user_id: UserID, resource_id: str) -> bool:
        """Check if a user can access a resource."""
        # 1. Find the owner
        ownership = await self.get_resource_owner(resource_id=resource_id)
        if not ownership:
            self.logger.warning(f"Resource {resource_id} has no owner")
            return False

        # 2. Check the user's relationship to the owner depending on the owner type
        if ownership.owner_type == OwnerType.WORKSPACE:
            # Check if the user is a member of the workspace
            return await self.user_is_workspace_member(
                user_id=user_id, workspace_id=ownership.owner_id
            )
        elif ownership.owner_type == OwnerType.ORGANIZATION:
            # Assuming all users have access to organization-owned resources
            # You might want to implement more specific logic here
            return True
        raise ValueError(f"Unsupported owner type: {ownership.owner_type}")

    async def assert_privilege_level(self):
        pass


class MembershipService:
    """Manage workspace memberships."""

    def __init__(self, session: AsyncSession, role: Role | None = None):
        self.session = session
        self.role = role or ctx_role.get()
        self.logger = logger.bind(service="membership")

    @asynccontextmanager
    @staticmethod
    async def with_session(
        role: Role | None = None,
    ) -> AsyncGenerator[MembershipService, None]:
        async with get_async_session_context_manager() as session:
            yield MembershipService(session, role=role)

    async def list_memberships(self, workspace_id: WorkspaceID) -> Sequence[Membership]:
        """List all workspace memberships."""
        statement = select(Membership).where(Membership.workspace_id == workspace_id)
        result = await self.session.exec(statement)
        return result.all()

    async def n_memberships(self, workspace_id: WorkspaceID) -> int:
        statement = select(func.count(Membership.user_id)).where(  # type: ignore
            Membership.workspace_id == workspace_id
        )
        result = await self.session.exec(statement)
        return result.one()

    async def get_membership(
        self, workspace_id: WorkspaceID, user_id: UserID
    ) -> Membership | None:
        """Get a workspace membership."""
        statement = select(Membership).where(
            Membership.user_id == user_id, Membership.workspace_id == workspace_id
        )
        result = await self.session.exec(statement)
        return result.one_or_none()

    @require_access_level(AccessLevel.ADMIN)
    async def create_membership(
        self, workspace_id: WorkspaceID, user_id: UserID
    ) -> None:
        """Create a workspace membership."""
        membership = Membership(user_id=user_id, workspace_id=workspace_id)
        self.session.add(membership)
        await self.session.commit()

    @require_access_level(AccessLevel.ADMIN)
    async def delete_membership(
        self, workspace_id: WorkspaceID, user_id: UserID
    ) -> None:
        """Delete a workspace membership."""
        if membership := await self.get_membership(workspace_id, user_id):
            await self.session.delete(membership)
            await self.session.commit()
