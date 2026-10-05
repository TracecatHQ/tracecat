from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import delete, exists, func, or_, select
from sqlalchemy.dialects.postgresql import array as pg_array
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlalchemy.sql.elements import ColumnElement

from tracecat.audit.logger import audit_log
from tracecat.auth.types import Role
from tracecat.authz.controls import ensure_can_grant_scopes, has_scope, require_scope
from tracecat.authz.membership import (
    drop_workspace_membership_mirror,
    lock_role_changes,
    mirror_workspace_membership,
)
from tracecat.authz.scopes import (
    ORG_MEMBER_FLOOR_SCOPES,
    ORG_MEMBER_ROLE_SLUG,
    is_org_level_role,
)
from tracecat.contexts import ctx_role
from tracecat.db.engine import SupportsExecute
from tracecat.db.models import (
    Group,
    GroupRoleAssignment,
    Membership,
    OrganizationMembership,
    RoleScope,
    Scope,
    User,
    UserRoleAssignment,
    Workspace,
    _role_paths,
    effective_group_members,
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
    WorkspaceMembershipUpdate,
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

    # Manual and IdP members inherit the same group role scopes.
    group_scopes = (
        select(Scope.name)
        .join(RoleScope, RoleScope.scope_id == Scope.id)
        .join(DBRole, DBRole.id == RoleScope.role_id)
        .join(GroupRoleAssignment, GroupRoleAssignment.role_id == DBRole.id)
        .join(
            effective_group_members,
            effective_group_members.c.group_id == GroupRoleAssignment.group_id,
        )
        .where(
            effective_group_members.c.user_id == user_id,
            GroupRoleAssignment.organization_id == organization_id,
            group_workspace_condition,
        )
    )

    # Presence alone carries a scope floor; same statement as the role paths so
    # a concurrent removal is never read half-applied.
    floor_scopes = select(func.unnest(pg_array(sorted(ORG_MEMBER_FLOOR_SCOPES)))).where(
        exists().where(
            OrganizationMembership.user_id == user_id,
            OrganizationMembership.organization_id == organization_id,
        )
    )
    combined = user_scopes.union(group_scopes, floor_scopes)
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


async def workspace_membership_exists(
    session: SupportsExecute,
    *,
    user_id: UserID,
    workspace_id: WorkspaceID,
) -> bool:
    """Check whether a user holds any role path into a workspace.

    Reads the role-path union directly, so arms added to ``_role_paths`` are
    covered without changing this helper.

    Args:
        session: Session used to run the existence query.
        user_id: User whose presence is being checked.
        workspace_id: Workspace the user must hold a role path into.

    Returns:
        True if at least one role path grants the user presence there.
    """
    stmt = select(
        exists(
            select(1)
            .select_from(_role_paths)
            .where(
                _role_paths.c.user_id == user_id,
                _role_paths.c.workspace_id == workspace_id,
            )
        )
    )
    return bool((await session.execute(stmt)).scalar_one())


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


def ensure_role_fits_scope(role: DBRole, workspace_id: WorkspaceID | None) -> None:
    """Reject an organization role granted on a single workspace.

    A workspace role granted org-wide applies in every workspace.

    Args:
        role: Role with ``scopes`` loaded.
        workspace_id: Target workspace, or None for an org-wide grant.

    Raises:
        TracecatValidationError: If an org-level role targets a workspace.
    """
    if workspace_id is not None and is_org_level_role(
        scope.name for scope in role.scopes
    ):
        raise TracecatValidationError(
            f"{role.name} is an organization role and can only be assigned organization-wide"
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
    if role.slug == ORG_MEMBER_ROLE_SLUG:
        raise TracecatValidationError("organization-member is granted implicitly")

    if not granter.is_platform_superuser:
        granter_scopes = await resolve_granter_scopes(session, granter)
        ensure_can_grant_scopes(granter_scopes, [scope.name for scope in role.scopes])
    return role


class MembershipService(BaseService):
    """Manage workspace memberships.

    This service optionally accepts a role for authorization-controlled methods
    (like add/update/delete membership). Methods used during the auth flow
    (like get_membership) don't require a role.
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
        paths = _role_paths
        # One row per member; a workspace grant wins over an org-wide one, and a
        # direct grant over a group one.
        statement = (
            select(User, DBRole.id, DBRole.name, paths.c.via_group, paths.c.org_wide)
            .select_from(paths)
            .join(User, User.id == paths.c.user_id)  # pyright: ignore[reportArgumentType]
            .join(DBRole, DBRole.id == paths.c.role_id)
            .where(paths.c.workspace_id == workspace_id)
            .distinct(paths.c.user_id)
            .order_by(paths.c.user_id, paths.c.org_wide, paths.c.via_group, DBRole.name)
        )
        rows = (await self.session.execute(statement)).tuples().all()
        return [
            WorkspaceMember(
                user_id=user.id,
                first_name=user.first_name,
                last_name=user.last_name,
                email=user.email,
                role_id=role_id,
                role_name=role_name,
                via_group=via_group,
                org_wide=org_wide,
            )
            for user, role_id, role_name, via_group, org_wide in rows
        ]

    @require_scope(
        "workspace:member:invite",
        "workspace:member:update",
        "org:member:invite",
        require_all=False,
    )
    async def list_assignable_roles(self, workspace_id: WorkspaceID) -> list[DBRole]:
        """List roles the caller may grant on this workspace.

        Mirrors the grant checks: workspace-level roles within the caller's
        live scope ceiling.
        """
        if self.role is None:
            raise TracecatAuthorizationError("Operator context is required")
        organization_id = await self._workspace_organization_id(workspace_id)
        statement = (
            select(DBRole)
            .where(
                DBRole.organization_id == organization_id,
                DBRole.slug.is_distinct_from(ORG_MEMBER_ROLE_SLUG),
                ~exists().where(
                    RoleScope.role_id == DBRole.id,
                    RoleScope.scope_id == Scope.id,
                    Scope.name.startswith("org:"),
                ),
            )
            .options(selectinload(DBRole.scopes))
            .order_by(DBRole.name)
        )
        roles = (await self.session.execute(statement)).scalars().all()
        if self.role.is_platform_superuser:
            return list(roles)
        granter_scopes = await resolve_granter_scopes(self.session, self.role)
        return [
            role
            for role in roles
            if all(has_scope(granter_scopes, scope.name) for scope in role.scopes)
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

    @require_scope("workspace:member:invite", "org:member:invite", require_all=False)
    async def create_membership(
        self,
        workspace_id: WorkspaceID,
        params: WorkspaceMembershipCreate,
    ) -> None:
        """Create a workspace membership."""
        org_stmt = select(Workspace.organization_id).where(Workspace.id == workspace_id)
        organization_id = (await self.session.execute(org_stmt)).scalar_one_or_none()
        if organization_id is None:
            raise TracecatValidationError("Workspace or default role not found")

        # Membership is a role grant: apply the same scope ceiling as
        # invitations rather than assigning unconditionally.
        if self.role is None:
            raise TracecatAuthorizationError(
                "Operator context is required to grant workspace membership"
            )
        await lock_role_changes(self.session, organization_id)
        try:
            if params.role_id is None:
                granted_role = await resolve_grantable_role_by_slug(
                    self.session, self.role, organization_id, "workspace-editor"
                )
            else:
                granted_role = await resolve_grantable_role(
                    self.session, self.role, organization_id, params.role_id
                )
        except TracecatNotFoundError as e:
            raise TracecatValidationError("Workspace or role not found") from e
        ensure_role_fits_scope(granted_role, workspace_id)
        role_id = granted_role.id

        existing_member_stmt = select(Membership.user_id).where(
            Membership.workspace_id == workspace_id,
            Membership.user_id == params.user_id,
        )
        if (
            await self.session.execute(existing_member_stmt)
        ).scalar_one_or_none() is not None:
            raise TracecatConflictError("User is already a member of workspace.")

        # Workspace add must not admit outsiders: org membership is a
        # precondition, not a side effect.
        org_member_stmt = select(OrganizationMembership.user_id).where(
            OrganizationMembership.user_id == params.user_id,
            OrganizationMembership.organization_id == organization_id,
        )
        if (await self.session.execute(org_member_stmt)).scalar_one_or_none() is None:
            raise TracecatNotFoundError("User not found in organization")

        # Legacy workspace table is still written for app versions that read it.
        await mirror_workspace_membership(
            self.session, user_id=params.user_id, workspace_id=workspace_id
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

    @require_scope("workspace:member:update")
    @audit_log(
        resource_type="rbac_user_assignment",
        action="update",
        resource_id_attr="user_id",
    )
    async def update_membership_role(
        self,
        workspace_id: WorkspaceID,
        user_id: UserID,
        params: WorkspaceMembershipUpdate,
    ) -> None:
        """Change a member's direct role on a workspace.

        Raises:
            TracecatAuthorizationError: If the caller targets themselves, or the
                member's current or new role exceeds the caller's scopes.
            TracecatConflictError: If the role comes from a group or an
                org-wide grant.
            TracecatNotFoundError: If the user is not a workspace member.
        """
        if self.role is None:
            raise TracecatAuthorizationError("Operator context is required")
        if user_id == self.role.user_id:
            raise TracecatAuthorizationError("You cannot change your own role")
        organization_id = await self._workspace_organization_id(workspace_id)
        await lock_role_changes(self.session, organization_id)

        assignment = (
            await self.session.execute(
                select(UserRoleAssignment)
                .where(
                    UserRoleAssignment.workspace_id == workspace_id,
                    UserRoleAssignment.user_id == user_id,
                )
                .options(
                    selectinload(UserRoleAssignment.role).selectinload(DBRole.scopes)
                )
            )
        ).scalar_one_or_none()
        if assignment is None:
            if await workspace_membership_exists(
                self.session, user_id=user_id, workspace_id=workspace_id
            ):
                raise TracecatConflictError(
                    "User's role comes from a group or an organization-wide grant. "
                    "Change it in organization settings."
                )
            raise TracecatNotFoundError("User is not a member of this workspace")

        # Changing a role also revokes the old one, so it must fit the ceiling too.
        if not self.role.is_platform_superuser:
            granter_scopes = await resolve_granter_scopes(self.session, self.role)
            current_scopes = [scope.name for scope in assignment.role.scopes]
            if not all(has_scope(granter_scopes, name) for name in current_scopes):
                raise TracecatAuthorizationError(
                    "Cannot change the role of a member with scopes you do not hold"
                )
        new_role = await resolve_grantable_role(
            self.session, self.role, organization_id, params.role_id
        )
        ensure_role_fits_scope(new_role, workspace_id)
        assignment.role_id = new_role.id
        await self.session.commit()

    async def _workspace_organization_id(
        self, workspace_id: WorkspaceID
    ) -> OrganizationID:
        """Return the workspace's organization.

        Raises:
            TracecatNotFoundError: If the workspace does not exist.
        """
        organization_id = (
            await self.session.execute(
                select(Workspace.organization_id).where(Workspace.id == workspace_id)
            )
        ).scalar_one_or_none()
        if organization_id is None:
            raise TracecatNotFoundError("Workspace not found")
        return organization_id

    @require_scope("workspace:member:remove")
    async def delete_membership(
        self, workspace_id: WorkspaceID, user_id: UserID
    ) -> None:
        """Delete a workspace membership.

        Raises:
            TracecatConflictError: If a group grant or an org-wide workspace role
                would keep the user in the workspace after the direct assignment
                is gone.
        """
        if self.role is None:
            raise TracecatAuthorizationError("Operator context is required")
        organization_id = (
            await self.session.execute(
                select(Workspace.organization_id).where(Workspace.id == workspace_id)
            )
        ).scalar_one()
        await lock_role_changes(self.session, organization_id)
        paths = _role_paths
        retained = (
            await self.session.execute(
                select(paths.c.org_wide, Group.name, DBRole.name)
                .select_from(paths)
                .join(DBRole, DBRole.id == paths.c.role_id)
                .outerjoin(Group, Group.id == paths.c.group_id)
                .where(
                    paths.c.user_id == user_id,
                    paths.c.workspace_id == workspace_id,
                    or_(paths.c.via_group, paths.c.org_wide),
                )
                # Report an org-wide grant first: removing a workspace group
                # alone would still leave it in place.
                .order_by(paths.c.org_wide.desc(), paths.c.via_group)
                .limit(1)
            )
        ).first()
        if retained is not None:
            org_wide, group_name, role_name = retained
            if group_name is not None and not org_wide:
                raise TracecatConflictError(
                    f"User remains a member through group '{group_name}'. "
                    "Remove them from the group first."
                )
            if group_name is not None:
                raise TracecatConflictError(
                    f"User has {role_name} in every workspace through group "
                    f"'{group_name}'. Change the group in organization settings."
                )
            raise TracecatConflictError(
                f"User has {role_name} in every workspace. "
                "Change their organization-wide role in organization settings."
            )

        await drop_workspace_membership_mirror(
            self.session, user_id=user_id, workspace_ids=[workspace_id]
        )
        await self.session.execute(
            delete(UserRoleAssignment).where(
                UserRoleAssignment.workspace_id == workspace_id,
                UserRoleAssignment.user_id == user_id,
            )
        )
        await self.session.commit()
