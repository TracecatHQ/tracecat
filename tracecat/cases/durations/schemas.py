"""Pydantic models for case duration metrics."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, model_validator

from tracecat.cases.dropdowns.schemas import CaseDropdownValueRead
from tracecat.cases.enums import CaseEventType
from tracecat.cases.tags.schemas import CaseTagRead


class CaseDurationAnchorSelection(StrEnum):
    """Strategies for choosing which matching event should anchor a duration."""

    FIRST = "first"
    LAST = "last"


class CaseDurationEventFilters(BaseModel):
    """Product-level filters for narrowing case duration event anchors."""

    new_values: list[str] = Field(
        default_factory=list,
        description="New priority, severity, or status values to match.",
    )
    tag_refs: list[str] = Field(
        default_factory=list,
        description="Case tag refs to match for tag add/remove events.",
    )
    field_ids: list[str] = Field(
        default_factory=list,
        description="Case custom field IDs to match for field change events.",
    )
    dropdown_definition_id: str | None = Field(
        default=None,
        description="Dropdown definition ID to match for dropdown value change events.",
    )
    dropdown_option_ids: list[str] = Field(
        default_factory=list,
        description="Dropdown option IDs to match for dropdown value change events.",
    )

    model_config = ConfigDict(extra="forbid")

    def is_empty(self) -> bool:
        return (
            not self.new_values
            and not self.tag_refs
            and not self.field_ids
            and self.dropdown_definition_id is None
            and not self.dropdown_option_ids
        )


class CaseDurationEventAnchor(BaseModel):
    """Selection criteria describing an event boundary for a duration."""

    event_type: CaseEventType = Field(
        ..., description="Case event type that should be matched for this anchor."
    )
    filters: CaseDurationEventFilters = Field(
        default_factory=CaseDurationEventFilters,
        description="Optional product-level filters for matching event payload values.",
    )
    selection: CaseDurationAnchorSelection = Field(
        default=CaseDurationAnchorSelection.FIRST,
        description=(
            "Whether to use the first or last matching event for this anchor. "
            "Defaults to the first match."
        ),
    )

    model_config = ConfigDict(extra="forbid")

    _has_unsupported_filters: bool = PrivateAttr(default=False)
    _timestamp_path: str = PrivateAttr(default="created_at")
    _legacy_field_filters: dict[str, Any] = PrivateAttr(default_factory=dict)

    @model_validator(mode="after")
    def validate_filters_for_event_type(self) -> CaseDurationEventAnchor:
        filters = self.filters
        if filters.is_empty():
            return self

        allowed_fields = _allowed_filter_fields_for_event_type(self.event_type)
        active_fields = _active_filter_fields(filters)
        unsupported = active_fields - allowed_fields
        if unsupported:
            fields = ", ".join(sorted(unsupported))
            raise ValueError(
                f"Unsupported filters for {self.event_type.value}: {fields}"
            )

        if self.event_type in _CATEGORY_FILTER_EVENT_TYPES and not filters.new_values:
            raise ValueError(f"{self.event_type.value} requires new_values filters")
        if self.event_type in _TAG_FILTER_EVENT_TYPES and not filters.tag_refs:
            raise ValueError(f"{self.event_type.value} requires tag_refs filters")
        if self.event_type is CaseEventType.FIELDS_CHANGED and not filters.field_ids:
            raise ValueError("fields_changed requires field_ids filters")
        if self.event_type is CaseEventType.DROPDOWN_VALUE_CHANGED and (
            not filters.dropdown_definition_id or not filters.dropdown_option_ids
        ):
            raise ValueError(
                "dropdown_value_changed requires dropdown_definition_id and "
                "dropdown_option_ids filters"
            )
        return self


_CATEGORY_FILTER_EVENT_TYPES = frozenset(
    {
        CaseEventType.PRIORITY_CHANGED,
        CaseEventType.SEVERITY_CHANGED,
        CaseEventType.STATUS_CHANGED,
    }
)
_TAG_FILTER_EVENT_TYPES = frozenset(
    {CaseEventType.TAG_ADDED, CaseEventType.TAG_REMOVED}
)


def _active_filter_fields(filters: CaseDurationEventFilters) -> set[str]:
    fields: set[str] = set()
    if filters.new_values:
        fields.add("new_values")
    if filters.tag_refs:
        fields.add("tag_refs")
    if filters.field_ids:
        fields.add("field_ids")
    if filters.dropdown_definition_id is not None:
        fields.add("dropdown_definition_id")
    if filters.dropdown_option_ids:
        fields.add("dropdown_option_ids")
    return fields


def _allowed_filter_fields_for_event_type(event_type: CaseEventType) -> set[str]:
    if event_type in _CATEGORY_FILTER_EVENT_TYPES:
        return {"new_values"}
    if event_type in _TAG_FILTER_EVENT_TYPES:
        return {"tag_refs"}
    if event_type is CaseEventType.FIELDS_CHANGED:
        return {"field_ids"}
    if event_type is CaseEventType.DROPDOWN_VALUE_CHANGED:
        return {"dropdown_definition_id", "dropdown_option_ids"}
    return set()


class CaseDurationDefinitionBase(BaseModel):
    """Shared fields for duration definitions."""

    name: str = Field(
        ..., max_length=255, description="Human readable name for the metric."
    )
    description: str | None = Field(
        default=None,
        max_length=1024,
        description="Optional description providing more context.",
    )
    start_anchor: CaseDurationEventAnchor = Field(
        ..., description="Event configuration that marks the start of the duration."
    )
    end_anchor: CaseDurationEventAnchor = Field(
        ..., description="Event configuration that marks the end of the duration."
    )


class CaseDurationDefinitionCreate(CaseDurationDefinitionBase):
    """Create payload for case duration definitions."""


class CaseDurationDefinitionUpdate(BaseModel):
    """Patch payload for case duration definitions."""

    name: str | None = Field(default=None, max_length=255)
    description: str | None = Field(default=None, max_length=1024)
    start_anchor: CaseDurationEventAnchor | None = None
    end_anchor: CaseDurationEventAnchor | None = None


class CaseDurationDefinitionRead(CaseDurationDefinitionBase):
    """Read model for case duration definitions."""

    id: uuid.UUID = Field(...)
    model_config = ConfigDict(from_attributes=True)


class CaseDurationBase(BaseModel):
    """Shared fields for persisted case durations."""

    definition_id: uuid.UUID = Field(
        ...,
        description="Identifier of the case duration definition generating this duration.",
    )
    start_event_id: uuid.UUID | None = Field(
        default=None,
        description="Case event that started the duration, if available.",
    )
    end_event_id: uuid.UUID | None = Field(
        default=None,
        description="Case event that ended the duration, if available.",
    )
    started_at: datetime | None = Field(
        default=None,
        description="Timestamp when the duration began.",
    )
    ended_at: datetime | None = Field(
        default=None,
        description="Timestamp when the duration ended.",
    )
    duration: timedelta | None = Field(
        default=None,
        description="Total elapsed time between start and end timestamps.",
    )


class CaseDurationCreate(CaseDurationBase):
    """Create payload for case duration records."""


class CaseDurationUpdate(BaseModel):
    """Patch payload for case duration records."""

    definition_id: uuid.UUID | None = None
    start_event_id: uuid.UUID | None = None
    end_event_id: uuid.UUID | None = None
    started_at: datetime | None = None
    ended_at: datetime | None = None
    duration: timedelta | None = None


class CaseDurationRead(CaseDurationBase):
    """Read model for case duration records."""

    id: uuid.UUID = Field(...)
    case_id: uuid.UUID = Field(...)
    model_config = ConfigDict(from_attributes=True)


class CaseDurationComputation(BaseModel):
    """Computed duration metrics for a case."""

    duration_id: uuid.UUID
    name: str
    description: str | None
    start_event_id: uuid.UUID | None
    end_event_id: uuid.UUID | None
    started_at: datetime | None
    ended_at: datetime | None
    duration: timedelta | None


class CaseDurationMetric(BaseModel):
    """OTEL-aligned Gauge metric for time-series platforms."""

    # Timestamp (when the duration was measured - the end point)
    timestamp: datetime = Field(
        ...,
        description="When the duration was measured (ISO 8601 with timezone)",
    )

    # Metric identity (Prometheus/OTEL convention: include unit in name)
    metric_name: str = Field(
        default="case_duration_seconds",
        description="Metric name including unit per OTEL/Prometheus conventions",
    )

    # The measurement value
    value: float = Field(
        ...,
        description="Duration in seconds",
    )

    # Metric dimensions: duration identification
    duration_name: str = Field(
        ...,
        description="Human-readable duration name (e.g., Time to Resolve, TTA)",
    )
    duration_slug: str = Field(
        ...,
        description="Slugified duration name for filtering (e.g., time_to_resolve, tta)",
    )

    # Case dimensions (low cardinality - good for groupby/faceting)
    case_priority: str = Field(..., description="Case priority value")
    case_severity: str = Field(..., description="Case severity value")
    case_status: str = Field(..., description="Case status value")

    # Case identifiers (high cardinality - for drill-down/lookups)
    case_id: str = Field(..., description="Case UUID for lookups")
    case_short_id: str = Field(..., description="Human-readable case identifier")

    # Case details
    fields: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Case custom fields and their values.",
    )
    tags: list[CaseTagRead] = Field(
        default_factory=list,
        description="Case tags.",
    )
    dropdown_values: list[CaseDropdownValueRead] = Field(
        default_factory=list,
        description="Case dropdown selections with definition and option info.",
    )
