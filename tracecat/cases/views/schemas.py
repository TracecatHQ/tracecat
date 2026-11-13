"""API models for saved case views."""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from tracecat.cases.enums import CaseViewVisibility
from tracecat.cases.search_schemas import CaseQuery, CaseSearchOrder

MAX_CASE_VIEW_COLUMNS = 100
MAX_CASE_VIEW_COLUMN_LENGTH = 200

type CaseViewKind = Literal["builtin", "custom"]
type CaseViewSort = Literal["asc", "desc"]


def _validate_stored_query(query: CaseQuery | None) -> CaseQuery | None:
    if query is not None and query.view_id is not None:
        raise ValueError("A saved view's query cannot reference another view")
    return query


def _strip_name(name: str | None) -> str | None:
    if name is None:
        return None
    stripped = name.strip()
    if not stripped:
        raise ValueError("View name cannot be blank")
    return stripped


type CaseViewColumns = list[str]  # Column IDs owned by the UI; validated for size only.


class CaseViewCreate(BaseModel):
    """Save a case search as a named view."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=255)
    visibility: CaseViewVisibility = Field(default=CaseViewVisibility.PERSONAL)
    query: CaseQuery = Field(default_factory=CaseQuery)
    order_by: CaseSearchOrder | None = Field(default=None)
    sort: CaseViewSort | None = Field(default=None)
    columns: CaseViewColumns | None = Field(
        default=None, max_length=MAX_CASE_VIEW_COLUMNS
    )

    _strip = field_validator("name")(_strip_name)
    _no_nested_view = field_validator("query")(_validate_stored_query)

    @field_validator("columns")
    @classmethod
    def validate_columns(cls, columns: list[str] | None) -> list[str] | None:
        """Bound each UI column ID."""
        return _validate_columns(columns)


class CaseViewUpdate(BaseModel):
    """Change a saved view. Omitted fields stay as they are.

    ``order_by``, ``sort``, and ``columns`` accept ``null`` to clear them.
    """

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=255)
    visibility: CaseViewVisibility | None = Field(default=None)
    query: CaseQuery | None = Field(default=None)
    order_by: CaseSearchOrder | None = Field(default=None)
    sort: CaseViewSort | None = Field(default=None)
    columns: CaseViewColumns | None = Field(
        default=None, max_length=MAX_CASE_VIEW_COLUMNS
    )

    _strip = field_validator("name")(_strip_name)
    _no_nested_view = field_validator("query")(_validate_stored_query)

    @field_validator("columns")
    @classmethod
    def validate_columns(cls, columns: list[str] | None) -> list[str] | None:
        """Bound each UI column ID."""
        return _validate_columns(columns)

    @model_validator(mode="after")
    def reject_null_required_fields(self) -> Self:
        """``name``, ``visibility``, and ``query`` can change but not be cleared."""
        for field in ("name", "visibility", "query"):
            if field in self.model_fields_set and getattr(self, field) is None:
                raise ValueError(f"{field} cannot be null")
        return self


def _validate_columns(columns: list[str] | None) -> list[str] | None:
    if columns is None:
        return None
    if any(
        not column or len(column) > MAX_CASE_VIEW_COLUMN_LENGTH for column in columns
    ):
        raise ValueError(
            f"Column IDs must be 1 to {MAX_CASE_VIEW_COLUMN_LENGTH} characters"
        )
    return list(dict.fromkeys(columns))


class CaseViewStaleRef(BaseModel):
    """A reference in a view's query to something that no longer exists.

    When the view runs, a stale reference matches nothing.
    """

    kind: Literal["tag", "dropdown"]
    value: str = Field(description="The identifier as stored in the view's query.")


class CaseViewRead(BaseModel):
    """A saved or built-in case view, as seen by the requesting user."""

    id: str = Field(description="A UUID for saved views, or a built-in view ID.")
    kind: CaseViewKind
    name: str
    visibility: CaseViewVisibility
    owner_id: UUID | None = Field(default=None)
    query: CaseQuery
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
    stale_refs: list[CaseViewStaleRef] = Field(
        default_factory=list,
        description="Deleted tags or dropdown options the query still names. "
        "Only filled when reading a single view.",
    )


class CaseViewPinUpdate(BaseModel):
    """Where to put a pinned view in the sidebar."""

    model_config = ConfigDict(extra="forbid")

    position: int | None = Field(
        default=None,
        ge=0,
        description="Sidebar position. Omit to add the view at the end.",
    )
