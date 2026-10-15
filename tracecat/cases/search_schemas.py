"""Validated matching and result options for structured case search."""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from tracecat.cases.enums import CasePriority, CaseSeverity, CaseStatus
from tracecat.pagination import CursorPaginationParams
from tracecat.query.filters import Filter

type CaseSearchOrder = Literal[
    "created_at",
    "updated_at",
    "priority",
    "severity",
    "status",
    "tasks",
    "id",
    "short_id",
    "assignee",
    "tags",
]


class CaseQuery(BaseModel):
    """Matching criteria shared by case search and filtered status counts.

    Explicit text, identity, and hierarchy constraints are combined with
    ``filters`` using AND. Workspace and entitlement checks belong to the
    service and cannot be supplied or negated by this request.

    Tag membership uses existing filter nodes: ``in`` matches any tag, an
    ``and`` of ``contains`` conditions requires all tags, and ``not`` around
    ``in`` excludes the selected tags. ``is_null`` matches no tags.

    Scalar dropdown/custom-field exclusions retain SQL NULL semantics:
    ``ne``, ``not_in``, and negated comparisons exclude unset values. Include
    unset values explicitly with an ``or`` containing ``is_null``.
    """

    model_config = ConfigDict(extra="forbid")

    filters: Filter | None = Field(default=None)
    search_term: str | None = Field(default=None, max_length=1000)
    short_id: str | None = Field(default=None, max_length=1000)
    case_id: UUID | None = Field(default=None)
    hierarchy: Literal["all", "top_level"] = Field(default="all")
    parent_id: UUID | None = Field(default=None)
    status: list[CaseStatus] | None = Field(default=None)
    priority: list[CasePriority] | None = Field(default=None)
    severity: list[CaseSeverity] | None = Field(default=None)
    assignee_id: list[UUID | Literal["unassigned", "__UNASSIGNED__"]] | None = Field(
        default=None
    )
    tags: list[str] | None = Field(default=None, max_length=1000)
    dropdown: list[str] | None = Field(default=None, max_length=50)
    start_time: datetime | None = Field(default=None)
    end_time: datetime | None = Field(default=None)
    updated_after: datetime | None = Field(default=None)
    updated_before: datetime | None = Field(default=None)

    @field_validator("search_term", "short_id")
    @classmethod
    def reject_nul(cls, value: str | None) -> str | None:
        """Reject text values that PostgreSQL cannot represent."""
        if value is not None and "\x00" in value:
            raise ValueError("Search values cannot contain null bytes")
        return value

    @model_validator(mode="after")
    def validate_hierarchy(self) -> Self:
        """Reject the incompatible parent and top-level search constraints."""
        if self.parent_id is not None and self.hierarchy == "top_level":
            raise ValueError("parent_id cannot be combined with hierarchy=top_level")
        return self


class CaseSearchRequest(CaseQuery, CursorPaginationParams):
    """A case query with pagination, sorting, and independent result details."""

    order_by: CaseSearchOrder | None = Field(default=None)
    sort: Literal["asc", "desc"] | None = Field(default=None)
    include_rows: bool = Field(default=False)
    field_ids: list[str] | None = Field(default=None)
    include_durations: bool = Field(default=False)
    include_payload: bool = Field(default=False)
    payload_fields: list[str] | None = Field(default=None)
    payload_paths: list[list[str]] | None = Field(default=None)
    include_count: bool = Field(default=True)
    promote_exact_match: bool = Field(
        default=False,
        description="Place complete case references before filtered text matches within the authorized workspace.",
    )
    match_none: bool = Field(
        default=False, description="The ordinary filter population matches no cases."
    )
