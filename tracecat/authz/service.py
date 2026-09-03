from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import and_, delete, func, literal, or_, select
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
    Membership,
    RoleScope,
    Scope,
    User,
    UserRoleAssignment,
    Workspace,
)
from tracecat.db.models import Role as DBRole
from tracecat.exceptions import (
    GroupDerivedMembershipError,
    TracecatAuthorizationError,
    TracecatNotFoundError,
    TracecatValidationError,
)
from tracecat.identifiers import OrganizationID, UserID, WorkspaceID
from tracecat.service import BaseService
from tracecat.workspaces.schemas import (
    WorkspaceMember,
    WorkspaceMembershipCreate,
    WorkspaceMemberSource,
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
                UserRoleAssignment.id.is_not(None).label("is_direct"),
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

        # One extra query for group provenance rather than an N+1 per member.
        group_statement = (
            select(GroupMember.user_id, Group.name)
            .join(Group, Group.id == GroupMember.group_id)
            .join(GroupRoleAssignment, GroupRoleAssignment.group_id == Group.id)
            .where(GroupRoleAssignment.workspace_id == workspace_id)
            .distinct()
            .order_by(GroupMember.user_id, Group.name)
        )
        groups_by_user: dict[UUID, list[str]] = {}
        for member_user_id, group_name in (
            await self.session.execute(group_statement)
        ).all():
            groups_by_user.setdefault(member_user_id, []).append(group_name)

        return [
            WorkspaceMember(
                user_id=user.id,
                first_name=user.first_name,
                last_name=user.last_name,
                email=user.email,
                role_name=role_name,
                source=WorkspaceMemberSource(kind="direct")
                if is_direct
                else WorkspaceMemberSource(
                    kind="group", group_names=groups_by_user.get(user.id, [])
                ),
            )
            for user, role_name, is_direct in rows
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
        statement = select(Membership).where(
            Membership.user_id == user_id,
            Membership.workspace_id.is_not(None),
        )
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

        # Heal stale direct assignments left behind by prior failed remove flows.
        await self.session.execute(
            delete(UserRoleAssignment).where(
                UserRoleAssignment.user_id == params.user_id,
                UserRoleAssignment.workspace_id == workspace_id,
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
        """
        # Membership is derived, so deleting the workspace-scoped assignments
        # delists the user. Group-derived access survives the delete, so refuse
        # rather than return success without removing the member.
        has_direct = (
            await self.session.execute(
                select(literal(1)).where(
                    UserRoleAssignment.workspace_id == workspace_id,
                    UserRoleAssignment.user_id == user_id,
                )
            )
        ).first() is not None
        if not has_direct:
            group_names = await self.list_membership_group_names(
                workspace_id, user_id=user_id
            )
            if group_names:
                raise GroupDerivedMembershipError(
                    "This member's workspace access comes from group membership. "
                    "Remove them from the granting group instead.",
                    group_names=group_names,
                )

        await self.session.execute(
            delete(UserRoleAssignment).where(
                UserRoleAssignment.workspace_id == workspace_id,
                UserRoleAssignment.user_id == user_id,
            )
        )
        await self.session.commit()

    async def list_membership_group_names(
        self, workspace_id: WorkspaceID, user_id: UserID
    ) -> list[str]:
        """Names of the groups granting a user access to a workspace."""
        statement = (
            select(Group.name)
            .join(GroupRoleAssignment, GroupRoleAssignment.group_id == Group.id)
            .join(GroupMember, GroupMember.group_id == Group.id)
            .where(
                GroupMember.user_id == user_id,
                GroupRoleAssignment.workspace_id == workspace_id,
            )
            .distinct()
            .order_by(Group.name)
        )
        return list((await self.session.execute(statement)).scalars().all())
