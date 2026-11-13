"""API models for saved case views."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator

from tracecat.cases.enums import CaseViewVisibility
from tracecat.cases.search_adapters import parse_dropdown_entry
from tracecat.cases.search_schemas import (
    CaseQuery,
    CaseSearchOrder,
    check_relevance_order,
)
from tracecat.pagination import CursorPaginatedResponse

MAX_CASE_VIEW_COLUMNS = 100
MAX_CASE_VIEW_COLUMN_LENGTH = 200

type CaseViewKind = Literal["builtin", "custom"]
type CaseViewSort = Literal["asc", "desc"]


def _validate_stored_query(query: CaseQuery) -> CaseQuery:
    """A stored query holds criteria only, in a form the view can keep running."""
    if query.view_id is not None:
        raise ValueError("A saved view's query cannot reference another view")
    if query.match_none:
        raise ValueError("A saved view's query cannot set match_none")
    # A malformed entry would later look like a deleted option, so reject it.
    for entry in query.dropdown or []:
        parse_dropdown_entry(entry)
    return query


def _strip_name(name: str) -> str:
    stripped = name.strip()
    if not stripped:
        raise ValueError("View name cannot be blank")
    return stripped


def _validate_columns(columns: list[str]) -> list[str]:
    if any(
        not column or len(column) > MAX_CASE_VIEW_COLUMN_LENGTH for column in columns
    ):
        raise ValueError(
            f"Column IDs must be 1 to {MAX_CASE_VIEW_COLUMN_LENGTH} characters"
        )
    return list(dict.fromkeys(columns))


type CaseViewName = Annotated[
    str, Field(min_length=1, max_length=255), AfterValidator(_strip_name)
]
type StoredCaseQuery = Annotated[CaseQuery, AfterValidator(_validate_stored_query)]
type CaseViewColumns = Annotated[
    list[str],
    Field(max_length=MAX_CASE_VIEW_COLUMNS),
    AfterValidator(_validate_columns),
]
"""Column IDs owned by the UI; validated for size only."""


def validate_view_order(
    query: CaseQuery, order_by: str | None, sort: str | None
) -> None:
    """Reject an order the view's query cannot produce, such as relevance
    without a full-text term.

    Raises:
        ValueError: The order does not fit the query.
    """
    if sort is not None and order_by is None:
        # The view's direction only applies to its own saved order.
        raise ValueError("sort requires order_by")
    check_relevance_order(query.search_mode, order_by, sort)


class CaseViewCreate(BaseModel):
    """Save a case search as a named view."""

    model_config = ConfigDict(extra="forbid")

    name: CaseViewName
    visibility: CaseViewVisibility = Field(default=CaseViewVisibility.PERSONAL)
    query: StoredCaseQuery = Field(default_factory=CaseQuery)
    order_by: CaseSearchOrder | None = Field(default=None)
    sort: CaseViewSort | None = Field(default=None)
    columns: CaseViewColumns | None = Field(default=None)

    @model_validator(mode="after")
    def validate_order(self) -> Self:
        """Only allow relevance sorting for full-text views."""
        validate_view_order(self.query, self.order_by, self.sort)
        return self


class CaseViewUpdate(BaseModel):
    """Change a saved view. Omitted fields stay as they are.

    ``order_by``, ``sort``, and ``columns`` accept ``null`` to clear them.
    """

    model_config = ConfigDict(extra="forbid")

    name: CaseViewName | None = Field(default=None)
    visibility: CaseViewVisibility | None = Field(default=None)
    query: StoredCaseQuery | None = Field(default=None)
    order_by: CaseSearchOrder | None = Field(default=None)
    sort: CaseViewSort | None = Field(default=None)
    columns: CaseViewColumns | None = Field(default=None)

    @model_validator(mode="after")
    def reject_null_required_fields(self) -> Self:
        """``name``, ``visibility``, and ``query`` can change but not be cleared."""
        sent = self.model_fields_set
        if "name" in sent and self.name is None:
            raise ValueError("name cannot be null")
        if "visibility" in sent and self.visibility is None:
            raise ValueError("visibility cannot be null")
        if "query" in sent and self.query is None:
            raise ValueError("query cannot be null")
        return self


class CaseViewStaleRef(BaseModel):
    """A reference in a view's query to something that no longer exists."""

    kind: Literal["tag", "dropdown"]
    value: str = Field(description="The identifier as stored in the view's query.")


class CaseViewEmptyRules(BaseModel):
    """Rules of a view left with nothing to match after deletions.

    Each one makes the view match no cases until it is given new values.
    """

    tags: bool = Field(default=False)
    dropdown_refs: list[str] = Field(default_factory=list)
    filters: bool = Field(
        default=False,
        description="The advanced filter tree names something deleted.",
    )

    @property
    def matches_none(self) -> bool:
        """Whether any rule is empty, so the view matches no cases."""
        return self.tags or bool(self.dropdown_refs) or self.filters


class CaseViewRead(BaseModel):
    """A saved or built-in case view, as seen by the requesting user."""

    id: str = Field(description="A UUID for saved views, or a built-in view ID.")
    kind: CaseViewKind
    name: str
    visibility: CaseViewVisibility
    owner_id: UUID | None = Field(default=None)
    query: CaseQuery = Field(
        description="The view's criteria. When reading one view, deleted tags "
        "and options are removed; see stale_refs and empty_rules."
    )
    order_by: CaseSearchOrder | None = Field(default=None)
    sort: CaseViewSort | None = Field(default=None)
    columns: list[str] | None = Field(default=None)
    can_edit: bool = Field(
        description="Whether the requesting user may rename, change, or delete it."
    )
    pinned: bool = Field(default=False)
    pin_position: int | None = Field(default=None)
    created_at: datetime | None = Field(default=None)
    updated_at: datetime | None = Field(default=None)
    stale_refs: list[CaseViewStaleRef] | None = Field(
        default=None,
        description="Deleted tags or dropdown options the stored query names. "
        "Null when not computed, which is the case in list responses.",
    )
    empty_rules: CaseViewEmptyRules | None = Field(
        default=None,
        description="Rules left with nothing to match. Null when not computed.",
    )


class CaseViewListRead(BaseModel):
    """Built-in views, and one page of saved views, kept apart.

    Built-ins are a fixed set and are never paginated, so ``saved`` always
    holds at most the requested page size.
    """

    builtins: list[CaseViewRead]
    saved: CursorPaginatedResponse[CaseViewRead]


class CaseViewPinUpdate(BaseModel):
    """Where to put a pinned view in the sidebar."""

    model_config = ConfigDict(extra="forbid")

    position: int | None = Field(
        default=None,
        ge=0,
        description="Sidebar position. Omit to add the view at the end.",
    )
