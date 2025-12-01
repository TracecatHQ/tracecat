"""Pydantic models for case duration metrics."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from tracecat.cases.enums import CaseEventType


class CaseDurationAnchorSelection(StrEnum):
    """Strategies for choosing which matching event should anchor a duration."""

    FIRST = "first"
    LAST = "last"


class CaseDurationEventAnchor(BaseModel):
    """Selection criteria describing an event boundary for a duration."""

    event_type: CaseEventType = Field(
        ..., description="Case event type that should be matched for this anchor."
    )
    timestamp_path: str = Field(
        default="created_at",
        description=(
            "Dot-delimited path to the timestamp field on the event. "
            "Defaults to the event creation timestamp."
        ),
    )
    field_filters: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Optional dot-delimited equality filters that must match on the event "
            "payload, e.g. {'data.new': 'resolved'}."
        ),
    )
    selection: CaseDurationAnchorSelection = Field(
        default=CaseDurationAnchorSelection.FIRST,
        description=(
            "Whether to use the first or last matching event for this anchor. "
            "Defaults to the first match."
        ),
    )


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


class CaseDurationRecord(BaseModel):
    """Flat, denormalized duration record optimized for data visualization tools.

    Designed for easy upload to analytics platforms like Grafana, Splunk, or
    data warehouses. All fields are primitive types (strings, numbers) for
    maximum compatibility. Each record represents a single duration measurement
    with embedded case context for filtering and grouping.

    Example use cases:
    - Mean Time to Detect (MTTD) aggregated by day/week/month
    - Response time distributions by severity or priority
    - SLA compliance tracking over time
    """

    # Case identifiers
    case_id: str = Field(..., description="Case UUID as string")
    case_short_id: str = Field(..., description="Human-readable case identifier")

    # Case timestamps (ISO 8601 strings for universal compatibility)
    case_created_at: str = Field(..., description="Case creation timestamp (ISO 8601)")
    case_updated_at: str = Field(
        ..., description="Case last update timestamp (ISO 8601)"
    )

    # Case attributes for filtering/grouping
    case_summary: str = Field(..., description="Case summary text")
    case_status: str = Field(..., description="Case status value")
    case_priority: str = Field(..., description="Case priority value")
    case_severity: str = Field(..., description="Case severity value")

    # Duration definition identification
    duration_id: str = Field(..., description="Duration definition UUID as string")
    duration_name: str = Field(..., description="Name of the duration definition")
    duration_description: str | None = Field(
        default=None, description="Description of the duration definition"
    )

    # Duration timestamps (ISO 8601 strings, null if not yet reached)
    started_at: datetime | None = Field(
        default=None, description="Duration start timestamp (ISO 8601)"
    )
    ended_at: datetime | None = Field(
        default=None, description="Duration end timestamp (ISO 8601)"
    )

    # Duration value as numeric seconds for easy aggregation
    duration_seconds: float | None = Field(
        default=None,
        description="Duration in seconds (null if incomplete). Use for aggregations.",
    )

    # Event references (as strings for compatibility)
    start_event_id: str | None = Field(
        default=None, description="UUID of the event that started the duration"
    )
    end_event_id: str | None = Field(
        default=None, description="UUID of the event that ended the duration"
    )
