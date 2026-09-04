"""Shared predicates over the derived ``Membership`` selectable.

``Membership`` is a read-only union over RBAC role assignments, so a row with a
NULL ``workspace_id`` means org-wide presence rather than workspace access.
That distinction is easy to drop, so the predicate lives here instead of being
rewritten at each call site.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import and_, delete, literal, or_, select, text, union_all
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from tracecat.audit.rls import audit_rls_bypass
from tracecat.contexts import ctx_role
from tracecat.db.engine import SupportsExecute
from tracecat.db.models import (
    GroupMember,
    GroupRoleAssignment,
    LegacyMembership,
    LegacyOrganizationMembership,
    Membership,
    UserRoleAssignment,
)
from tracecat.db.models import Role as DBRole
from tracecat.db.rls import (
    RLS_BYPASS_OFF,
    RLS_BYPASS_ON,
    RLS_VAR_BYPASS,
    is_rls_mode_enforce,
)

_GET_RLS_BYPASS = text(f"SELECT current_setting('{RLS_VAR_BYPASS}', true)")
_SET_RLS_BYPASS = text(f"SELECT set_config('{RLS_VAR_BYPASS}', :bypass, true)")


@asynccontextmanager
async def _legacy_membership_rls_bypass(
    session: AsyncSession,
) -> AsyncIterator[None]:
    if not is_rls_mode_enforce():
        yield
        return

    previous = await session.scalar(_GET_RLS_BYPASS)
    audit_rls_bypass("mirror legacy membership rows", ctx_role.get())
    await session.execute(_SET_RLS_BYPASS, {"bypass": RLS_BYPASS_ON})
    try:
        yield
    finally:
        await session.execute(
            _SET_RLS_BYPASS,
            {"bypass": previous or RLS_BYPASS_OFF},
        )


def org_membership_predicate(
    user_id: ColumnElement[object] | object | None = None,
    organization_id: ColumnElement[object] | object | None = None,
) -> ColumnElement[bool]:
    """Predicate matching a user's org-wide presence rows.

    Both operands accept a literal value or a column expression, so the same
    helper serves ``WHERE`` clauses and outer-join ``ON`` clauses that correlate
    with ``User.id``. Omit either argument to leave that side unconstrained: no
    ``user_id`` lists an organization's members, and no ``organization_id``
    lists a user's organizations.
    """
    clauses: list[ColumnElement[bool]] = [Membership.workspace_id.is_(None)]
    if user_id is not None:
        clauses.insert(0, Membership.user_id == user_id)
    if organization_id is not None:
        clauses.insert(-1, Membership.organization_id == organization_id)
    return and_(*clauses)


@dataclass(frozen=True, slots=True)
class OrgRoleName:
    """An org-wide role resolved for a user."""

    name: str
    slug: str | None


async def resolve_org_role_names(
    session: SupportsExecute,
    organization_id: object,
    user_ids: Sequence[object],
) -> dict[UUID, OrgRoleName]:
    """Resolve each user's org-wide role, direct assignment or via a group.

    A user can hold several org-wide roles at once, so the rule is: a direct
    assignment wins over a group-derived one, and ties break on role name
    then slug ascending. Users with no org-wide role are absent from the result.
    """
    if not user_ids:
        return {}

    direct = select(
        UserRoleAssignment.user_id.label("user_id"),
        DBRole.name.label("name"),
        DBRole.slug.label("slug"),
        literal(0).label("priority"),
    ).join(DBRole, DBRole.id == UserRoleAssignment.role_id)
    direct = direct.where(
        UserRoleAssignment.organization_id == organization_id,
        UserRoleAssignment.workspace_id.is_(None),
        UserRoleAssignment.user_id.in_(user_ids),
    )

    via_group = (
        select(
            GroupMember.user_id.label("user_id"),
            DBRole.name.label("name"),
            DBRole.slug.label("slug"),
            literal(1).label("priority"),
        )
        .join(GroupRoleAssignment, GroupRoleAssignment.group_id == GroupMember.group_id)
        .join(DBRole, DBRole.id == GroupRoleAssignment.role_id)
        .where(
            GroupRoleAssignment.organization_id == organization_id,
            GroupRoleAssignment.workspace_id.is_(None),
            GroupMember.user_id.in_(user_ids),
        )
    )

    candidates = union_all(direct, via_group).subquery("org_role_candidates")
    stmt = select(candidates.c.user_id, candidates.c.name, candidates.c.slug).order_by(
        candidates.c.user_id,
        candidates.c.priority,
        candidates.c.name,
        candidates.c.slug,
    )
    resolved: dict[UUID, OrgRoleName] = {}
    for user_id, name, slug in (await session.execute(stmt)).tuples().all():
        resolved.setdefault(user_id, OrgRoleName(name=name, slug=slug))
    return resolved


async def mirror_assignment_grant(
    session: AsyncSession,
    *,
    user_id: object,
    organization_id: object,
    workspace_id: object | None,
) -> None:
    """Mirror an assignment grant into the legacy membership tables.

    N-1 pods still read those tables directly, so they must be kept in step
    until the contract release drops them. Does not commit.
    """
    async with _legacy_membership_rls_bypass(session):
        await session.execute(
            pg_insert(LegacyOrganizationMembership)
            .values(user_id=user_id, organization_id=organization_id)
            .on_conflict_do_nothing(
                index_elements=[
                    LegacyOrganizationMembership.user_id,
                    LegacyOrganizationMembership.organization_id,
                ]
            )
        )
        if workspace_id is not None:
            await session.execute(
                pg_insert(LegacyMembership)
                .values(user_id=user_id, workspace_id=workspace_id)
                .on_conflict_do_nothing(
                    index_elements=[
                        LegacyMembership.user_id,
                        LegacyMembership.workspace_id,
                    ]
                )
            )


async def mirror_group_member_grant(
    session: AsyncSession,
    *,
    group_id: object,
    user_id: object,
    organization_id: object,
) -> None:
    """Mirror every role path gained when a user joins a group. Does not commit."""
    group_has_assignment = (
        select(GroupRoleAssignment.id)
        .where(
            GroupRoleAssignment.group_id == group_id,
            GroupRoleAssignment.organization_id == organization_id,
        )
        .exists()
    )
    org_rows = select(literal(user_id), literal(organization_id)).where(
        group_has_assignment
    )
    workspace_rows = select(literal(user_id), GroupRoleAssignment.workspace_id).where(
        GroupRoleAssignment.group_id == group_id,
        GroupRoleAssignment.organization_id == organization_id,
        GroupRoleAssignment.workspace_id.is_not(None),
    )

    async with _legacy_membership_rls_bypass(session):
        await session.execute(
            pg_insert(LegacyOrganizationMembership)
            .from_select(
                ["user_id", "organization_id"],
                org_rows,
            )
            .on_conflict_do_nothing(
                index_elements=[
                    LegacyOrganizationMembership.user_id,
                    LegacyOrganizationMembership.organization_id,
                ]
            )
        )
        await session.execute(
            pg_insert(LegacyMembership)
            .from_select(["user_id", "workspace_id"], workspace_rows)
            .on_conflict_do_nothing(
                index_elements=[
                    LegacyMembership.user_id,
                    LegacyMembership.workspace_id,
                ]
            )
        )


async def mirror_group_assignment_grant(
    session: AsyncSession,
    *,
    group_id: object,
    organization_id: object,
    workspace_id: object | None,
) -> None:
    """Mirror every role path gained when a group gets a role. Does not commit."""
    org_rows = select(GroupMember.user_id, literal(organization_id)).where(
        GroupMember.group_id == group_id
    )

    async with _legacy_membership_rls_bypass(session):
        await session.execute(
            pg_insert(LegacyOrganizationMembership)
            .from_select(
                ["user_id", "organization_id"],
                org_rows,
            )
            .on_conflict_do_nothing(
                index_elements=[
                    LegacyOrganizationMembership.user_id,
                    LegacyOrganizationMembership.organization_id,
                ]
            )
        )
        if workspace_id is not None:
            workspace_rows = select(GroupMember.user_id, literal(workspace_id)).where(
                GroupMember.group_id == group_id
            )
            await session.execute(
                pg_insert(LegacyMembership)
                .from_select(["user_id", "workspace_id"], workspace_rows)
                .on_conflict_do_nothing(
                    index_elements=[
                        LegacyMembership.user_id,
                        LegacyMembership.workspace_id,
                    ]
                )
            )


async def _mirror_role_path_revoke(
    session: AsyncSession,
    *,
    user_ids: Sequence[object],
    organization_id: object,
    workspace_ids: Sequence[object],
) -> None:
    if not user_ids:
        return

    remaining_workspace_direct = (
        select(UserRoleAssignment.id)
        .where(
            UserRoleAssignment.user_id == LegacyMembership.user_id,
            UserRoleAssignment.organization_id == organization_id,
            UserRoleAssignment.workspace_id == LegacyMembership.workspace_id,
        )
        .exists()
    )
    remaining_workspace_group = (
        select(GroupMember.user_id)
        .join(
            GroupRoleAssignment,
            GroupRoleAssignment.group_id == GroupMember.group_id,
        )
        .where(
            GroupMember.user_id == LegacyMembership.user_id,
            GroupRoleAssignment.organization_id == organization_id,
            GroupRoleAssignment.workspace_id == LegacyMembership.workspace_id,
        )
        .exists()
    )
    remaining_org_direct = (
        select(UserRoleAssignment.id)
        .where(
            UserRoleAssignment.user_id == LegacyOrganizationMembership.user_id,
            UserRoleAssignment.organization_id == organization_id,
        )
        .exists()
    )
    remaining_org_group = (
        select(GroupMember.user_id)
        .join(
            GroupRoleAssignment,
            GroupRoleAssignment.group_id == GroupMember.group_id,
        )
        .where(
            GroupMember.user_id == LegacyOrganizationMembership.user_id,
            GroupRoleAssignment.organization_id == organization_id,
        )
        .exists()
    )

    async with _legacy_membership_rls_bypass(session):
        if workspace_ids:
            await session.execute(
                delete(LegacyMembership).where(
                    LegacyMembership.user_id.in_(user_ids),
                    LegacyMembership.workspace_id.in_(workspace_ids),
                    ~or_(remaining_workspace_direct, remaining_workspace_group),
                )
            )
        await session.execute(
            delete(LegacyOrganizationMembership).where(
                LegacyOrganizationMembership.user_id.in_(user_ids),
                LegacyOrganizationMembership.organization_id == organization_id,
                ~or_(remaining_org_direct, remaining_org_group),
            )
        )


async def mirror_assignment_revoke(
    session: AsyncSession,
    *,
    user_id: object,
    organization_id: object,
    workspace_id: object | None,
) -> None:
    """Mirror a direct revoke, retaining rows backed by another role path.

    Does not commit.
    """
    await _mirror_role_path_revoke(
        session,
        user_ids=[user_id],
        organization_id=organization_id,
        workspace_ids=[workspace_id] if workspace_id is not None else [],
    )


async def mirror_group_revoke(
    session: AsyncSession,
    *,
    user_ids: Sequence[object],
    organization_id: object,
    workspace_ids: Sequence[object],
) -> None:
    """Mirror removed group paths, retaining rows backed by another path."""
    await _mirror_role_path_revoke(
        session,
        user_ids=user_ids,
        organization_id=organization_id,
        workspace_ids=workspace_ids,
    )
