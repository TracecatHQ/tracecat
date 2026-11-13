"""Validated matching and result options for structured case search."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from tracecat.cases.constants import (
    MAX_PAYLOAD_FIELDS,
    MAX_PAYLOAD_PATH_KEYS,
    MAX_PAYLOAD_PATHS,
)
from tracecat.cases.enums import CasePriority, CaseSeverity, CaseStatus
from tracecat.cases.views.types import BuiltinCaseViewId
from tracecat.pagination import CursorPaginationParams
from tracecat.query.filters import (
    MAX_FILTER_CONDITIONS,
    MAX_FILTER_DEPTH,
    MAX_FILTER_VALUES,
    Filter,
    FilterOp,
)
from tracecat.query.lexical import (
    literal_search_pattern,
    parse_full_text_query,
    resolve_search_columns,
)
from tracecat.query.lexical_types import SearchMode
from tracecat.query.resolver import FieldKind

MAX_RELATIVE_WINDOW_AMOUNT = 1000

type CaseSearchOrder = Literal[
    "relevance",
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


class CaseFilterField(BaseModel):
    """Server-defined operators and input metadata for one searchable field."""

    field: str
    label: str
    kind: FieldKind
    operators: list[FilterOp]
    choices: list[str] | None = Field(default=None)
    range_choices: list[str] | None = Field(default=None)
    input_type: Literal["text", "number", "date", "datetime-local"] = Field(
        default="text"
    )


class CaseFilterMetadata(BaseModel):
    """Discoverable filter fields and shared query limits."""

    fields: list[CaseFilterField]
    max_depth: int = Field(default=MAX_FILTER_DEPTH)
    max_conditions: int = Field(default=MAX_FILTER_CONDITIONS)
    max_values: int = Field(default=MAX_FILTER_VALUES)


class RelativeTimeUnit(StrEnum):
    """Calendar units a relative time window can count back in."""

    HOUR = "hour"
    DAY = "day"
    WEEK = "week"
    MONTH = "month"


class RelativeTimeWindow(BaseModel):
    """A time range that ends now, such as "the last 30 days".

    The database resolves the start each time a query runs, so a saved view
    keeps meaning "the last 30 days" instead of a fixed date. Months use
    calendar arithmetic: one month before 31 March is 28 or 29 February.
    """

    model_config = ConfigDict(extra="forbid")

    amount: int = Field(ge=1, le=MAX_RELATIVE_WINDOW_AMOUNT)
    unit: RelativeTimeUnit


class CaseQueryErrorDetail(BaseModel):
    """A case query error with a machine-readable code."""

    code: Literal["current_user_required"]
    message: str


class CaseQueryErrorResponse(BaseModel):
    """A rejected case query: a plain message, or a typed error."""

    detail: str | CaseQueryErrorDetail


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

    view_id: UUID | BuiltinCaseViewId | None = Field(
        default=None,
        description=(
            "Run a saved case view. The view supplies the matching criteria; "
            "the request may add a `filters` tree, which is ANDed with the view's. "
            "Other matching fields cannot be combined with a view."
        ),
    )
    filters: Filter | None = Field(
        default=None,
        description='AND/OR/NOT conditions. Payload scalar equality uses field="payload.metadata.product.vendor_name", op="eq", value="Microsoft". Dot-separated literal keys or numeric array indices; keys containing dots are unsupported. Equality preserves JSON types. Payload is_null matches a missing path; explicit JSON null is a present value. Exclusions omit missing paths unless ORed with is_null.',
    )
    search_term: str | None = Field(default=None, max_length=1000)
    search_mode: SearchMode | None = Field(
        default=None,
        description="Explicit word/phrase or escaped substring search. Omitted mode retains legacy matching and ordering.",
    )
    search_columns: list[Literal["summary", "description"]] | None = Field(
        default=None, min_length=1, max_length=2
    )
    short_id: str | None = Field(default=None, max_length=1000)
    case_id: UUID | None = Field(default=None)
    hierarchy: Literal["all", "top_level"] = Field(default="all")
    parent_id: UUID | None = Field(default=None)
    status: list[CaseStatus] | None = Field(default=None)
    priority: list[CasePriority] | None = Field(default=None)
    severity: list[CaseSeverity] | None = Field(default=None)
    assignee_id: list[UUID | Literal["unassigned", "__UNASSIGNED__", "me"]] | None = (
        Field(
            default=None,
            description='User IDs, "unassigned", or "me" for the requesting user.',
        )
    )
    created_by: list[UUID | Literal["me"]] | None = Field(
        default=None,
        max_length=MAX_FILTER_VALUES,
        description=(
            'Users who created the case, or "me" for the requesting user. '
            "Cases created by workflows or other non-user callers never match."
        ),
    )
    tags: list[str] | None = Field(default=None, max_length=1000)
    dropdown: list[str] | None = Field(default=None, max_length=50)
    start_time: datetime | None = Field(default=None)
    end_time: datetime | None = Field(default=None)
    created_within: RelativeTimeWindow | None = Field(
        default=None,
        description="Created within this window, ending at the time the query runs.",
    )
    updated_after: datetime | None = Field(default=None)
    updated_before: datetime | None = Field(default=None)
    updated_within: RelativeTimeWindow | None = Field(
        default=None,
        description="Updated within this window, ending at the time the query runs.",
    )

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
        if self.created_within is not None and (
            self.start_time is not None or self.end_time is not None
        ):
            raise ValueError(
                "created_within cannot be combined with start_time or end_time"
            )
        if self.updated_within is not None and (
            self.updated_after is not None or self.updated_before is not None
        ):
            raise ValueError(
                "updated_within cannot be combined with updated_after or updated_before"
            )
        if self.search_mode is not None:
            if self.search_term is None:
                raise ValueError("search_mode requires search_term")
            if self.search_mode == SearchMode.FULL_TEXT:
                parse_full_text_query(self.search_term)
            else:
                literal_search_pattern(self.search_term)
            resolve_search_columns(
                "all_columns"
                if self.search_columns is None
                else tuple(self.search_columns),
                ("summary", "description"),
            )
        elif self.search_columns is not None:
            raise ValueError("search_columns requires an explicit search_mode")
        return self


class CaseSearchRequest(CaseQuery, CursorPaginationParams):
    """A case query with pagination, sorting, and independent result details."""

    order_by: CaseSearchOrder | None = Field(default=None)
    sort: Literal["asc", "desc"] | None = Field(default=None)
    include_rows: bool = Field(default=False)
    field_ids: list[str] | None = Field(default=None)
    include_durations: bool = Field(default=False)
    include_payload: bool = Field(default=False)
    payload_fields: list[str] | None = Field(
        default=None, max_length=MAX_PAYLOAD_FIELDS
    )
    payload_paths: (
        list[
            Annotated[list[str], Field(min_length=1, max_length=MAX_PAYLOAD_PATH_KEYS)]
        ]
        | None
    ) = Field(default=None, max_length=MAX_PAYLOAD_PATHS)
    include_count: bool = Field(default=True)
    promote_exact_match: bool = Field(
        default=False,
        description="Place complete case references before filtered text matches within the authorized workspace.",
    )
    match_none: bool = Field(
        default=False, description="The ordinary filter population matches no cases."
    )

    @model_validator(mode="after")
    def validate_relevance(self) -> Self:
        """Relevance is a descending full-text order; explicit columns keep theirs."""
        relevance = self.order_by == "relevance" or (
            self.order_by is None and self.search_mode == SearchMode.FULL_TEXT
        )
        if relevance and self.search_mode != SearchMode.FULL_TEXT:
            raise ValueError("Relevance ordering requires full_text search")
        if relevance and self.sort == "asc":
            raise ValueError("Relevance ordering must be descending")
        return self
