"""Transactional membership projection for the RBAC compatibility release."""

from collections.abc import Sequence
from typing import cast
from uuid import UUID

from sqlalchemy import Table, delete, literal, or_, select, union_all
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.authz.controls import ensure_can_grant_scopes, scope_matches
from tracecat.db.models import (
    GroupMember,
    GroupRoleAssignment,
    LegacyMembership,
    LegacyOrganizationMembership,
    RoleScope,
    Scope,
    User,
    UserRoleAssignment,
)


async def sync_membership(
    session: AsyncSession,
    *,
    organization_id: UUID,
    user_ids: Sequence[UUID],
    workspace_id: UUID | None,
    granter_scopes: frozenset[str],
    grant_user_ids: Sequence[UUID] | None = None,
) -> None:
    """Mirror an affected scope after assignment changes, before committing.

    Both reader versions retain the existing admission gate during rollout.
    Only explicit grants may create membership; revocations pass no grant users.
    Validate all permissions admission would activate, including dormant paths.
    Keep existing workspace admission while organization roles still provide
    workspace permissions; deleting a workspace role must not revoke those.
    Serialize projections for each user so concurrent revocations cannot
    leave a stale membership behind. Group writers must also lock their group
    before changing its assignments or members.
    """
    if not user_ids:
        return
    await session.flush()
    # NO KEY UPDATE permits the FK locks held by concurrent assignment inserts.
    user_id_column = User.__table__.c.id
    await session.execute(
        select(user_id_column)
        .where(user_id_column.in_(user_ids))
        .order_by(user_id_column)
        .with_for_update(key_share=True)
    )
    role_paths = union_all(
        select(
            UserRoleAssignment.user_id,
            UserRoleAssignment.workspace_id,
            UserRoleAssignment.role_id,
        ).where(
            UserRoleAssignment.organization_id == organization_id,
            or_(
                UserRoleAssignment.workspace_id.is_(None),
                UserRoleAssignment.workspace_id == workspace_id,
            ),
            UserRoleAssignment.user_id.in_(user_ids),
        ),
        select(
            GroupMember.user_id,
            GroupRoleAssignment.workspace_id,
            GroupRoleAssignment.role_id,
        )
        .join(GroupRoleAssignment, GroupRoleAssignment.group_id == GroupMember.group_id)
        .where(
            GroupRoleAssignment.organization_id == organization_id,
            or_(
                GroupRoleAssignment.workspace_id.is_(None),
                GroupRoleAssignment.workspace_id == workspace_id,
            ),
            GroupMember.user_id.in_(user_ids),
        ),
    ).subquery("membership_role_paths")
    present_users = (
        select(role_paths.c.user_id)
        .where(role_paths.c.workspace_id == workspace_id)
        .distinct()
        .subquery("present_users")
    )
    table = cast(
        Table,
        LegacyMembership.__table__
        if workspace_id is not None
        else LegacyOrganizationMembership.__table__,
    )
    scope_column = "workspace_id" if workspace_id is not None else "organization_id"
    scope_id = workspace_id if workspace_id is not None else organization_id
    grant_users = user_ids if grant_user_ids is None else grant_user_ids
    if grant_users:
        new_members = select(present_users.c.user_id).where(
            present_users.c.user_id.in_(grant_users),
            ~select(table.c.user_id)
            .where(
                table.c.user_id == present_users.c.user_id,
                table.c[scope_column] == scope_id,
            )
            .exists(),
        )
        # Admission can activate an older assignment with more scopes than the
        # role being granted. Enforce the ceiling on the full resulting access.
        if "*" not in granter_scopes:
            activated_scopes = (
                select(role_paths.c.user_id, role_paths.c.workspace_id, Scope.name)
                .join(RoleScope, RoleScope.role_id == role_paths.c.role_id)
                .join(Scope, Scope.id == RoleScope.scope_id)
                .where(role_paths.c.user_id.in_(new_members))
                .distinct()
            )
            scopes = (await session.execute(activated_scopes)).tuples().all()
            # Org-wide member-management roles already bypass the workspace
            # gate. Use the same wildcard matcher as auth for this exception.
            admitted_admins = {
                user_id
                for user_id, assigned_workspace, scope in scopes
                if workspace_id is not None
                and assigned_workspace is None
                and scope_matches(scope, "org:member:invite")
            }
            ensure_can_grant_scopes(
                granter_scopes,
                (
                    scope
                    for user_id, _, scope in scopes
                    if user_id not in admitted_admins
                ),
            )
        await session.execute(
            insert(table)
            .from_select(
                ["user_id", scope_column],
                select(present_users.c.user_id, literal(scope_id)).where(
                    present_users.c.user_id.in_(grant_users)
                ),
            )
            .on_conflict_do_nothing()
        )
    if workspace_id is not None:
        # Org-scoped resource permissions still need the existing workspace
        # membership gate. Retain that gate without granting any new membership.
        inherited_users = (
            select(role_paths.c.user_id)
            .join(RoleScope, RoleScope.role_id == role_paths.c.role_id)
            .join(Scope, Scope.id == RoleScope.scope_id)
            .where(role_paths.c.workspace_id.is_(None), ~Scope.name.startswith("org:"))
        )
        retained_users = select(present_users.c.user_id).union(inherited_users)
    else:
        retained_users = select(present_users.c.user_id)
    await session.execute(
        delete(table).where(
            table.c[scope_column] == scope_id,
            table.c.user_id.in_(user_ids),
            table.c.user_id.not_in(retained_users),
        )
    )
