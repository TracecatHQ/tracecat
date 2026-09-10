from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import delete, literal, or_, select, union_all
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlalchemy.sql.elements import ColumnElement

from tracecat.auth.types import Role
from tracecat.authz.controls import ensure_can_grant_scopes, require_scope
from tracecat.contexts import ctx_role
from tracecat.db.engine import SupportsExecute
from tracecat.db.models import (
    Group,
    GroupMember,
    GroupRoleAssignment,
    LegacyMembership,
    Membership,
    OrganizationMembership,
    RoleScope,
    Scope,
    User,
    UserRoleAssignment,
    Workspace,
)
from tracecat.db.models import Role as DBRole
from tracecat.exceptions import (
    TracecatAuthorizationError,
    TracecatConflictError,
    TracecatNotFoundError,
    TracecatValidationError,
)
from tracecat.identifiers import OrganizationID, UserID, WorkspaceID
from tracecat.service import BaseService
from tracecat.workspaces.schemas import (
    WorkspaceMember,
    WorkspaceMembershipCreate,
)


async def query_effective_scopes(
    session: SupportsExecute,
    user_id: UserID,
    organization_id: OrganizationID,
    workspace_id: WorkspaceID | None,
) -> frozenset[str]:
    """Read a user's effective scopes from live database state.

    Uncached. Use for authorization decisions that must not act on a stale
    snapshot, such as scope-ceiling checks on role grants.
    """
    user_workspace_condition = (
        or_(
            UserRoleAssignment.workspace_id.is_(None),
            UserRoleAssignment.workspace_id == workspace_id,
        )
        if workspace_id is not None
        else UserRoleAssignment.workspace_id.is_(None)
    )

    group_workspace_condition = (
        or_(
            GroupRoleAssignment.workspace_id.is_(None),
            GroupRoleAssignment.workspace_id == workspace_id,
        )
        if workspace_id is not None
        else GroupRoleAssignment.workspace_id.is_(None)
    )
    # Direct user role assignments → Role → RoleScope → Scope
    user_scopes = (
        select(Scope.name)
        .join(RoleScope, RoleScope.scope_id == Scope.id)
        .join(DBRole, DBRole.id == RoleScope.role_id)
        .join(UserRoleAssignment, UserRoleAssignment.role_id == DBRole.id)
        .where(
            UserRoleAssignment.user_id == user_id,
            UserRoleAssignment.organization_id == organization_id,
            user_workspace_condition,
        )
    )

    # Group role assignments → GroupMember → GroupRoleAssignment → Role → RoleScope → Scope
    group_scopes = (
        select(Scope.name)
        .join(RoleScope, RoleScope.scope_id == Scope.id)
        .join(DBRole, DBRole.id == RoleScope.role_id)
        .join(GroupRoleAssignment, GroupRoleAssignment.role_id == DBRole.id)
        .join(GroupMember, GroupMember.group_id == GroupRoleAssignment.group_id)
        .where(
            GroupMember.user_id == user_id,
            GroupRoleAssignment.organization_id == organization_id,
            group_workspace_condition,
        )
    )

    # Single atomic query: union both assignment paths
    combined = user_scopes.union(group_scopes)
    result = await session.execute(combined)
    return frozenset(result.scalars().all())


async def resolve_granter_scopes(
    session: AsyncSession, granter: Role
) -> frozenset[str]:
    """Read the granter's scopes from live state for a grant decision.

    ``Role.scopes`` is a per-request snapshot backed by a TTL cache, so a
    just-demoted user can still carry privileged scopes there. Grant ceilings
    must not be decided from that snapshot.
    """
    if granter.user_id is None or granter.organization_id is None:
        return granter.scopes or frozenset()
    return await query_effective_scopes(
        session, granter.user_id, granter.organization_id, granter.workspace_id
    )


async def resolve_grantable_role(
    session: AsyncSession,
    granter: Role,
    organization_id: OrganizationID,
    role_id: UUID,
) -> DBRole:
    """Resolve a role for a durable grant, enforcing the scope ceiling.

    The single entry point for role grants. Validates existence and
    organization ownership, loads the role's scopes, and rejects grants
    exceeding the granter's own scopes.

    Raises:
        TracecatNotFoundError: If the role is missing or owned by another org.
        TracecatAuthorizationError: If the role carries scopes the granter lacks.
    """
    return await _resolve_grantable(
        session, granter, organization_id, DBRole.id == role_id, "Role not found"
    )


async def resolve_grantable_role_by_slug(
    session: AsyncSession,
    granter: Role,
    organization_id: OrganizationID,
    slug: str,
) -> DBRole:
    """Resolve a preset role by slug for a durable grant, enforcing the ceiling."""
    return await _resolve_grantable(
        session,
        granter,
        organization_id,
        DBRole.slug == slug,
        f"Role not found: {slug}",
    )


async def _resolve_grantable(
    session: AsyncSession,
    granter: Role,
    organization_id: OrganizationID,
    selector: ColumnElement[bool],
    not_found_message: str,
) -> DBRole:
    """Load a role with its scopes in one query, then apply the ceiling."""
    stmt = (
        select(DBRole)
        .where(DBRole.organization_id == organization_id, selector)
        .options(selectinload(DBRole.scopes))
    )
    role = (await session.execute(stmt)).scalar_one_or_none()
    if role is None:
        raise TracecatNotFoundError(not_found_message)

    if not granter.is_platform_superuser:
        granter_scopes = await resolve_granter_scopes(session, granter)
        ensure_can_grant_scopes(granter_scopes, [scope.name for scope in role.scopes])
    return role


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
        """List workspace members with the role each holds there."""
        # Membership is the role paths, so read them directly rather than
        # deriving Membership and joining back to the same tables.
        paths = union_all(
            select(
                UserRoleAssignment.user_id,
                UserRoleAssignment.role_id,
                literal(0).label("via_group"),
            ).where(UserRoleAssignment.workspace_id == workspace_id),
            select(
                GroupMember.user_id,
                GroupRoleAssignment.role_id,
                literal(1).label("via_group"),
            )
            .join_from(
                GroupRoleAssignment,
                GroupMember,
                GroupMember.group_id == GroupRoleAssignment.group_id,
            )
            .where(GroupRoleAssignment.workspace_id == workspace_id),
        ).subquery("paths")
        # One row per member; a direct assignment wins over group grants.
        statement = (
            select(User, DBRole.name)
            .select_from(paths)
            .join(User, User.id == paths.c.user_id)  # pyright: ignore[reportArgumentType]
            .join(DBRole, DBRole.id == paths.c.role_id)
            .distinct(paths.c.user_id)
            .order_by(paths.c.user_id, paths.c.via_group, DBRole.name)
        )
        rows = (await self.session.execute(statement)).tuples().all()
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
    ) -> Membership | None:
        """Get a workspace membership."""
        statement = select(Membership).where(
            Membership.user_id == user_id,
            Membership.workspace_id == workspace_id,
        )
        return (await self.session.execute(statement)).scalars().first()

    async def list_user_memberships(self, user_id: UserID) -> Sequence[Membership]:
        """List all workspace memberships for a specific user.

        This is used by the authorization middleware to cache user permissions.
        """
        statement = select(Membership).where(Membership.user_id == user_id)
        result = await self.session.execute(statement)
        return result.scalars().all()

    @require_scope("workspace:member:invite")
    async def create_membership(
        self,
        workspace_id: WorkspaceID,
        params: WorkspaceMembershipCreate,
    ) -> None:
        """Create a workspace membership.

        Note: The authorization cache is request-scoped, so changes will be
        reflected in subsequent requests automatically.

        Raises:
            TracecatNotFoundError: If the user holds no role path in the
                workspace's organization.
            TracecatConflictError: If the user is already a workspace member.
            TracecatValidationError: If the workspace or default role is
                missing.
            TracecatAuthorizationError: If the granted role exceeds the
                caller's scopes.
        """
        org_stmt = select(Workspace.organization_id).where(Workspace.id == workspace_id)
        organization_id = (await self.session.execute(org_stmt)).scalar_one_or_none()
        if organization_id is None:
            raise TracecatValidationError("Workspace or default role not found")

        # Membership grants the editor role, so it is a role grant: apply the
        # same scope ceiling as invitations rather than assigning unconditionally.
        if self.role is None:
            raise TracecatAuthorizationError(
                "Operator context is required to grant workspace membership"
            )
        try:
            granted_role = await resolve_grantable_role_by_slug(
                self.session, self.role, organization_id, "workspace-editor"
            )
        except TracecatNotFoundError as e:
            raise TracecatValidationError("Workspace or default role not found") from e
        role_id = granted_role.id

        # Membership grants a role, so the target must already have a role
        # path in this organization; otherwise any platform user could be
        # pulled in and then administered as an org member.
        org_presence_stmt = (
            select(OrganizationMembership.user_id)
            .where(
                OrganizationMembership.user_id == params.user_id,
                OrganizationMembership.organization_id == organization_id,
            )
            .limit(1)
        )
        if (await self.session.execute(org_presence_stmt)).scalar_one_or_none() is None:
            raise TracecatNotFoundError("User not found in organization")

        existing_member_stmt = select(Membership.user_id).where(
            Membership.workspace_id == workspace_id,
            Membership.user_id == params.user_id,
        )
        if (
            await self.session.execute(existing_member_stmt)
        ).scalar_one_or_none() is not None:
            raise TracecatConflictError("User is already a member of workspace.")

        # Written for app versions that still read the legacy table.
        await self.session.execute(
            pg_insert(LegacyMembership)
            .values(user_id=params.user_id, workspace_id=workspace_id)
            .on_conflict_do_nothing(
                index_elements=[LegacyMembership.user_id, LegacyMembership.workspace_id]
            )
        )
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

        Raises:
            TracecatConflictError: If a workspace-scoped group grant would keep
                the user in the workspace after the direct assignment is gone.
        """
        # Only workspace-scoped group grants keep workspace presence; org-wide
        # group roles do not.
        group_name = (
            await self.session.execute(
                select(Group.name)
                .join(GroupMember, GroupMember.group_id == Group.id)
                .join(
                    GroupRoleAssignment,
                    GroupRoleAssignment.group_id == Group.id,
                )
                .where(
                    GroupMember.user_id == user_id,
                    GroupRoleAssignment.workspace_id == workspace_id,
                )
                .limit(1)
            )
        ).scalar_one_or_none()
        if group_name is not None:
            raise TracecatConflictError(
                f"User remains a member through group '{group_name}'. "
                "Remove them from the group first."
            )

        await self.session.execute(
            delete(LegacyMembership).where(
                LegacyMembership.workspace_id == workspace_id,
                LegacyMembership.user_id == user_id,
            )
        )
        await self.session.execute(
            delete(UserRoleAssignment).where(
                UserRoleAssignment.workspace_id == workspace_id,
                UserRoleAssignment.user_id == user_id,
            )
        )
        await self.session.commit()
