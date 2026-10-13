"""Global relationship sort keys for workspace case cursor pagination."""

import uuid
from typing import Literal

import sqlalchemy as sa
from sqlalchemy.sql.elements import ColumnElement

from tracecat.db.models import Case, CaseTag, CaseTagLink, User

type CaseRelationshipSort = Literal["assignee", "tags"]
type CaseSortDirection = Literal["asc", "desc"]


def case_relationship_sort_key(
    workspace_id: uuid.UUID, field: CaseRelationshipSort
) -> ColumnElement[str]:
    """Return one nullable string key per case, without multiplying result rows.

    Assignees use the lowercased email of the stored User relationship, including
    former workspace members. Tags use the minimum lowercased name among linked
    tags belonging to the requested workspace, after trimming ASCII edge
    whitespace. The multi-tag key
    stays the minimum in either sort direction, independent of relationship
    loading order. Normalized strings use bytewise C collation.

    Both scalar lookups are anchored to the requested case workspace. Callers
    must also scope their outer Case select to the authorized workspace.
    Explicit String types let the paginator serialize and bind text cursor
    values without guessing, including names that resemble dates.
    """
    if field == "assignee":
        return (
            sa.select(sa.func.lower(User.email, type_=sa.String()).collate("C"))
            .where(Case.assignee_id == User.id, Case.workspace_id == workspace_id)
            .correlate(Case)
            .scalar_subquery()
        )
    if field == "tags":
        normalized_name = sa.func.lower(
            sa.func.btrim(CaseTag.name, " \t\n\r\f\v", type_=sa.String()),
            type_=sa.String(),
        ).collate("C")
        return (
            sa.select(sa.func.min(normalized_name, type_=sa.String()))
            .join(CaseTagLink, CaseTagLink.tag_id == CaseTag.id)
            .where(
                CaseTagLink.case_id == Case.id,
                CaseTag.workspace_id == workspace_id,
                Case.workspace_id == workspace_id,
            )
            .correlate(Case)
            .scalar_subquery()
        )
    raise ValueError(f"Unsupported case relationship sort: {field}")


def case_relationship_ordering(
    workspace_id: uuid.UUID,
    field: CaseRelationshipSort,
    direction: CaseSortDirection,
) -> tuple[ColumnElement[str], ColumnElement[uuid.UUID]]:
    """Order globally by the relationship key, then UUID in the same direction.

    Missing assignees and tags are last in both directions. Pass these clauses
    to paginate(order_by=...); the paginator reverses seek and null placement
    internally when following a previous-page cursor.
    """
    key = case_relationship_sort_key(workspace_id, field)
    if direction == "asc":
        return key.asc().nulls_last(), Case.id.asc()
    if direction == "desc":
        return key.desc().nulls_last(), Case.id.desc()
    raise ValueError(f"Unsupported case sort direction: {direction}")
