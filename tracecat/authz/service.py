from __future__ import annotations

from collections.abc import Sequence

from async_lru import alru_cache
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


# Simple cache for membership lookups - 1 hour TTL
@alru_cache(ttl=3600, maxsize=1024)
async def _get_membership_cached(
    workspace_id: str, user_id: str
) -> tuple[bool, WorkspaceRole | None]:
    """Cache membership lookups to avoid repeated database queries.

    Args:
        workspace_id: The workspace ID as a string
        user_id: The user ID as a string

    Returns:
        tuple: (exists, role) where exists is True if membership exists
    """
    # Import here to avoid circular dependency
    from tracecat.db.engine import get_async_session_context_manager

    async with get_async_session_context_manager() as session:
        statement = select(Membership).where(
            Membership.user_id == user_id, Membership.workspace_id == workspace_id
        )
        result = await session.exec(statement)
        membership = result.first()

        if membership:
            return True, membership.role
        return False, None


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

    async def get_membership_cached(
        self, workspace_id: WorkspaceID, user_id: UserID
    ) -> tuple[bool, WorkspaceRole | None]:
        """Get membership status with caching.

        Returns:
            tuple: (exists, role) where exists is True if membership exists
        """
        # Convert UUIDs to strings for cache key
        return await _get_membership_cached(str(workspace_id), str(user_id))

    @require_workspace_role(WorkspaceRole.ADMIN)
    async def create_membership(
        self,
        workspace_id: WorkspaceID,
        params: WorkspaceMembershipCreate,
    ) -> None:
        """Create a workspace membership."""
        membership = Membership(
            user_id=params.user_id,
            workspace_id=workspace_id,
            role=params.role,
        )
        self.session.add(membership)
        await self.session.commit()

        # Clear cache after creating membership
        _get_membership_cached.cache_clear()

    @require_workspace_role(WorkspaceRole.ADMIN)
    async def update_membership(
        self, membership: Membership, params: WorkspaceMembershipUpdate
    ) -> None:
        """Update a workspace membership."""
        for key, value in params.model_dump(exclude_unset=True).items():
            setattr(membership, key, value)
        self.session.add(membership)
        await self.session.commit()

        # Clear cache after updating membership
        _get_membership_cached.cache_clear()

    @require_workspace_role(WorkspaceRole.ADMIN)
    async def delete_membership(
        self, workspace_id: WorkspaceID, user_id: UserID
    ) -> None:
        """Delete a workspace membership."""
        if membership := await self.get_membership(workspace_id, user_id):
            await self.session.delete(membership)
            await self.session.commit()

            # Clear cache after deleting membership
            _get_membership_cached.cache_clear()
