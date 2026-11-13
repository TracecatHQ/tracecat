"""Case views defined in code. Every workspace has them, and nobody can edit them."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from tracecat.cases.enums import CaseStatus
from tracecat.cases.search_schemas import CaseQuery, CaseSearchOrder
from tracecat.cases.views.types import BuiltinCaseViewId

OPEN_CASE_STATUSES: tuple[CaseStatus, ...] = tuple(
    status
    for status in CaseStatus
    if status not in (CaseStatus.RESOLVED, CaseStatus.CLOSED)
)
"""Every status except resolved and closed."""


@dataclass(frozen=True, slots=True)
class BuiltinCaseView:
    """One built-in view's name, query, and default sort."""

    id: BuiltinCaseViewId
    name: str
    query: CaseQuery
    order_by: CaseSearchOrder
    sort: Literal["asc", "desc"]


BUILTIN_CASE_VIEWS: dict[BuiltinCaseViewId, BuiltinCaseView] = {
    view.id: view
    for view in (
        # Includes sub-cases, since a sub-case can be assigned on its own.
        BuiltinCaseView(
            id="assigned_to_me",
            name="Assigned to me",
            query=CaseQuery(
                status=list(OPEN_CASE_STATUSES),
                assignee_id=["me"],
                hierarchy="all",
            ),
            order_by="created_at",
            sort="desc",
        ),
    )
}
