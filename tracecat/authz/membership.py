"""Shared predicates over the derived ``Membership`` selectable.

``Membership`` is a read-only union over RBAC role assignments, so a row with a
NULL ``workspace_id`` means org-wide presence rather than workspace access.
That distinction is easy to drop, so the predicate lives here instead of being
rewritten at each call site.
"""

from __future__ import annotations

from sqlalchemy import and_
from sqlalchemy.sql.elements import ColumnElement

from tracecat.db.models import Membership


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
