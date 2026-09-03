"""Shared predicates over the derived ``Membership`` selectable.

``Membership`` is a read-only union over RBAC role assignments, so a row with a
NULL ``workspace_id`` means org-wide presence rather than workspace access.
That distinction is easy to drop, so the predicate lives here instead of being
rewritten at each call site.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import and_, literal, select, union_all
from sqlalchemy.sql.elements import ColumnElement

from tracecat.db.engine import SupportsExecute
from tracecat.db.models import (
    GroupMember,
    GroupRoleAssignment,
    Membership,
    UserRoleAssignment,
)
from tracecat.db.models import Role as DBRole


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
