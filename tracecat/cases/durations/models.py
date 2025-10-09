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


class CaseDurationBase(BaseModel):
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


class CaseDurationCreate(CaseDurationBase):
    """Create payload for case duration definitions."""


class CaseDurationUpdate(BaseModel):
    """Patch payload for case duration definitions."""

    name: str | None = Field(default=None, max_length=255)
    description: str | None = Field(default=None, max_length=1024)
    start_anchor: CaseDurationEventAnchor | None = None
    end_anchor: CaseDurationEventAnchor | None = None


class CaseDurationRead(CaseDurationBase):
    """Read model for case duration definitions."""

    id: uuid.UUID = Field(...)
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
