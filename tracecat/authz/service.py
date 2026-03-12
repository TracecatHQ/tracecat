from __future__ import annotations

import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from sqlalchemy import and_, delete, exists, func, literal, select
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.auth.types import Role
from tracecat.authz.controls import require_scope
from tracecat.contexts import ctx_role
from tracecat.db.models import (
    GroupMember,
    GroupRoleAssignment,
    Membership,
    OrganizationMembership,
    User,
    UserRoleAssignment,
    Workspace,
)
from tracecat.db.models import Role as DBRole
from tracecat.exceptions import TracecatValidationError
from tracecat.identifiers import OrganizationID, UserID, WorkspaceID
from tracecat.service import BaseService
from tracecat.workspaces.schemas import (
    WorkspaceMember,
    WorkspaceMembershipCreate,
)

_clear_effective_scopes_cache: Callable[[], None] | None = None


def register_effective_scopes_cache_clearer(clearer: Callable[[], None]) -> None:
    """Register a callback used to clear effective scope caches."""
    global _clear_effective_scopes_cache
    _clear_effective_scopes_cache = clearer


def invalidate_authz_caches() -> None:
    """Clear in-process authorization caches after RBAC/membership mutations."""
    if _clear_effective_scopes_cache is not None:
        _clear_effective_scopes_cache()


@dataclass
class MembershipWithOrg:
    """Membership with organization ID."""

    membership: Membership
    org_id: OrganizationID


@dataclass(frozen=True)
class WorkspaceMembershipTarget:
    organization_id: OrganizationID
    role_id: uuid.UUID


async def _resolve_workspace_membership_target(
    session: AsyncSession,
    *,
    workspace_id: WorkspaceID,
    role_id: uuid.UUID | None = None,
) -> WorkspaceMembershipTarget:
    """Resolve the org and role used for a workspace membership mutation."""
    ws_result = await session.execute(
        select(Workspace.organization_id).where(Workspace.id == workspace_id)
    )
    organization_id = ws_result.scalar_one_or_none()
    if organization_id is None:
        raise TracecatValidationError("Workspace not found")

    if role_id is None:
        default_role_result = await session.execute(
            select(DBRole.id).where(
                DBRole.organization_id == organization_id,
                DBRole.slug == "workspace-editor",
            )
        )
        resolved_role_id = default_role_result.scalar_one_or_none()
        if resolved_role_id is None:
            raise TracecatValidationError("Default workspace role not found")
        return WorkspaceMembershipTarget(
            organization_id=organization_id,
            role_id=resolved_role_id,
        )

    role_result = await session.execute(
        select(DBRole.id, DBRole.slug).where(
            DBRole.id == role_id,
            DBRole.organization_id == organization_id,
        )
    )
    row = role_result.first()
    if row is None:
        raise TracecatValidationError("Invalid role ID for this organization")
    resolved_role_id, slug = row
    if slug is not None and slug.startswith("organization-"):
        raise TracecatValidationError(
            "Workspace membership requires a workspace-scoped role"
        )
    return WorkspaceMembershipTarget(
        organization_id=organization_id,
        role_id=resolved_role_id,
    )


async def _ensure_org_membership(
    session: AsyncSession,
    *,
    organization_id: OrganizationID,
    user_id: UserID,
) -> None:
    """Ensure org membership exists before granting workspace access."""
    org_membership_stmt = select(
        exists().where(
            OrganizationMembership.organization_id == organization_id,
            OrganizationMembership.user_id == user_id,
        )
    )
    if (await session.execute(org_membership_stmt)).scalar():
        return
    session.add(
        OrganizationMembership(
            organization_id=organization_id,
            user_id=user_id,
        )
    )


async def _upsert_workspace_assignment(
    session: AsyncSession,
    *,
    organization_id: OrganizationID,
    workspace_id: WorkspaceID,
    user_id: UserID,
    role_id: uuid.UUID,
    assigned_by: uuid.UUID | None,
) -> None:
    """Create or update the direct workspace assignment for a user."""
    assignment_result = await session.execute(
        select(UserRoleAssignment).where(
            UserRoleAssignment.organization_id == organization_id,
            UserRoleAssignment.workspace_id == workspace_id,
            UserRoleAssignment.user_id == user_id,
        )
    )
    assignment = assignment_result.scalar_one_or_none()
    if assignment is None:
        session.add(
            UserRoleAssignment(
                organization_id=organization_id,
                user_id=user_id,
                workspace_id=workspace_id,
                role_id=role_id,
                assigned_by=assigned_by,
            )
        )
        return
    assignment.role_id = role_id
    assignment.assigned_by = assigned_by


async def _partition_workspace_groups_for_removal(
    session: AsyncSession,
    *,
    organization_id: OrganizationID,
    workspace_id: WorkspaceID,
    user_id: UserID,
) -> tuple[set[uuid.UUID], set[uuid.UUID]]:
    """Split target-workspace groups into removable vs blocking sets."""
    group_result = await session.execute(
        select(GroupMember.group_id)
        .join(
            GroupRoleAssignment,
            GroupRoleAssignment.group_id == GroupMember.group_id,
        )
        .where(
            GroupMember.user_id == user_id,
            GroupRoleAssignment.organization_id == organization_id,
            GroupRoleAssignment.workspace_id == workspace_id,
        )
    )
    group_ids = set(group_result.scalars().all())
    if not group_ids:
        return set(), set()

    assignments_result = await session.execute(
        select(GroupRoleAssignment.group_id, GroupRoleAssignment.workspace_id).where(
            GroupRoleAssignment.organization_id == organization_id,
            GroupRoleAssignment.group_id.in_(group_ids),
        )
    )
    assignments_by_group: dict[uuid.UUID, set[uuid.UUID | None]] = {
        group_id: set() for group_id in group_ids
    }
    for group_id, assignment_workspace_id in assignments_result.tuples().all():
        assignments_by_group[group_id].add(assignment_workspace_id)

    removable: set[uuid.UUID] = set()
    blocking: set[uuid.UUID] = set()
    for group_id, scopes in assignments_by_group.items():
        if scopes == {workspace_id}:
            removable.add(group_id)
        else:
            blocking.add(group_id)
    return removable, blocking


async def _remove_workspace_only_group_memberships(
    session: AsyncSession,
    *,
    organization_id: OrganizationID,
    workspace_id: WorkspaceID,
    user_id: UserID,
) -> None:
    """Drop user-group links that only grant the target workspace."""
    (
        removable_group_ids,
        blocking_group_ids,
    ) = await _partition_workspace_groups_for_removal(
        session,
        organization_id=organization_id,
        workspace_id=workspace_id,
        user_id=user_id,
    )
    if blocking_group_ids:
        raise TracecatValidationError(
            "User still inherits workspace access from shared groups. "
            "Remove the user from those groups first."
        )
    if not removable_group_ids:
        return
    await session.execute(
        delete(GroupMember).where(
            GroupMember.user_id == user_id,
            GroupMember.group_id.in_(removable_group_ids),
        )
    )


async def _has_any_direct_access_in_org(
    session: AsyncSession,
    *,
    organization_id: OrganizationID,
    user_id: UserID,
) -> bool:
    """Check whether the user still has any direct assignments in the org."""
    stmt = select(
        exists().where(
            UserRoleAssignment.organization_id == organization_id,
            UserRoleAssignment.user_id == user_id,
        )
    )
    return bool((await session.execute(stmt)).scalar())


async def _has_any_group_access_in_org(
    session: AsyncSession,
    *,
    organization_id: OrganizationID,
    user_id: UserID,
) -> bool:
    """Check whether the user still has any group-derived access in the org."""
    stmt = select(
        exists()
        .where(GroupMember.user_id == user_id)
        .where(GroupRoleAssignment.group_id == GroupMember.group_id)
        .where(GroupRoleAssignment.organization_id == organization_id)
    )
    return bool((await session.execute(stmt)).scalar())


async def _cleanup_org_membership_if_unassigned(
    session: AsyncSession,
    *,
    organization_id: OrganizationID,
    user_id: UserID,
) -> None:
    """Remove org/workspace memberships when the user has no access left."""
    if await _has_any_direct_access_in_org(
        session,
        organization_id=organization_id,
        user_id=user_id,
    ):
        return
    if await _has_any_group_access_in_org(
        session,
        organization_id=organization_id,
        user_id=user_id,
    ):
        return

    workspace_ids_subquery = select(Workspace.id).where(
        Workspace.organization_id == organization_id
    )
    await session.execute(
        delete(Membership).where(
            Membership.user_id == user_id,
            Membership.workspace_id.in_(workspace_ids_subquery),
        )
    )
    await session.execute(
        delete(OrganizationMembership).where(
            OrganizationMembership.organization_id == organization_id,
            OrganizationMembership.user_id == user_id,
        )
    )


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
        target = await _resolve_workspace_membership_target(
            self.session,
            workspace_id=workspace_id,
        )
        existing_membership_result = await self.session.execute(
            select(Membership).where(
                Membership.workspace_id == workspace_id,
                Membership.user_id == params.user_id,
            )
        )
        if existing_membership_result.scalar_one_or_none() is not None:
            raise TracecatValidationError("User is already a member of workspace.")

        await _ensure_org_membership(
            self.session,
            organization_id=target.organization_id,
            user_id=params.user_id,
        )
        self.session.add(
            Membership(
                user_id=params.user_id,
                workspace_id=workspace_id,
            )
        )
        await _upsert_workspace_assignment(
            self.session,
            organization_id=target.organization_id,
            workspace_id=workspace_id,
            user_id=params.user_id,
            role_id=target.role_id,
            assigned_by=self.role.user_id if self.role else None,
        )
        await self.session.commit()
        invalidate_authz_caches()

    @require_scope("workspace:member:remove")
    async def delete_membership(
        self, workspace_id: WorkspaceID, user_id: UserID
    ) -> None:
        """Delete a workspace membership.

        Note: The authorization cache is request-scoped, so changes will be
        reflected in subsequent requests automatically.
        """
        target = await _resolve_workspace_membership_target(
            self.session,
            workspace_id=workspace_id,
        )
        await _remove_workspace_only_group_memberships(
            self.session,
            organization_id=target.organization_id,
            workspace_id=workspace_id,
            user_id=user_id,
        )
        await self.session.execute(
            delete(UserRoleAssignment).where(
                UserRoleAssignment.workspace_id == workspace_id,
                UserRoleAssignment.user_id == user_id,
            )
        )
        await self.session.execute(
            delete(Membership).where(
                Membership.workspace_id == workspace_id,
                Membership.user_id == user_id,
            )
        )
        await _cleanup_org_membership_if_unassigned(
            self.session,
            organization_id=target.organization_id,
            user_id=user_id,
        )
        await self.session.commit()
        invalidate_authz_caches()
