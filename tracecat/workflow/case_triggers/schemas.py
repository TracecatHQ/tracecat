from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from tracecat.cases.enums import (
    CaseEventType,
    CasePriority,
    CaseSeverity,
    CaseStatus,
)
from tracecat.core.schemas import Schema
from tracecat.identifiers import WorkflowID

CaseTriggerStatus = Literal["online", "offline"]
CaseTriggerFilterEventType = Literal[
    CaseEventType.STATUS_CHANGED,
    CaseEventType.SEVERITY_CHANGED,
    CaseEventType.PRIORITY_CHANGED,
]
FILTERED_CASE_TRIGGER_EVENT_TYPES = (
    CaseEventType.STATUS_CHANGED,
    CaseEventType.SEVERITY_CHANGED,
    CaseEventType.PRIORITY_CHANGED,
)


def _dedupe_items[T](items: list[T]) -> list[T]:
    seen: set[T] = set()
    deduped: list[T] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        deduped.append(item)
    return deduped


def _normalize_filter_event_types(
    event_types: Sequence[CaseEventType | str] | None,
) -> set[str]:
    if not event_types:
        return set()
    return {
        event_type.value if isinstance(event_type, CaseEventType) else event_type
        for event_type in event_types
    }


class CaseTriggerEventFilters(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status_changed: list[CaseStatus] = Field(default_factory=list)
    severity_changed: list[CaseSeverity] = Field(default_factory=list)
    priority_changed: list[CasePriority] = Field(default_factory=list)

    @field_validator("status_changed", "severity_changed", "priority_changed")
    @classmethod
    def dedupe_filter_values[T](cls, value: list[T]) -> list[T]:
        return _dedupe_items(value)

    def values_for(self, event_type: CaseTriggerFilterEventType | str) -> list[str]:
        match event_type:
            case CaseEventType.STATUS_CHANGED | "status_changed":
                return [value.value for value in self.status_changed]
            case CaseEventType.SEVERITY_CHANGED | "severity_changed":
                return [value.value for value in self.severity_changed]
            case CaseEventType.PRIORITY_CHANGED | "priority_changed":
                return [value.value for value in self.priority_changed]
            case _:
                return []


def normalize_case_trigger_event_filters(
    event_filters: CaseTriggerEventFilters | Mapping[str, object] | None,
    *,
    event_types: Sequence[CaseEventType | str] | None = None,
) -> CaseTriggerEventFilters:
    normalized = CaseTriggerEventFilters.model_validate(event_filters or {})
    if event_types is None:
        return normalized

    selected_event_types = _normalize_filter_event_types(event_types)
    invalid_filter_event_types = [
        event_type.value
        for event_type in FILTERED_CASE_TRIGGER_EVENT_TYPES
        if (
            event_type.value not in selected_event_types
            and normalized.values_for(event_type)
        )
    ]
    if invalid_filter_event_types:
        invalid_types = ", ".join(invalid_filter_event_types)
        raise ValueError(
            f"event_filters keys must also be present in event_types: {invalid_types}"
        )
    return normalized


def is_case_trigger_configured(
    *,
    status: str | None,
    event_types: Sequence[CaseEventType | str] | None,
    tag_filters: Sequence[str] | None,
) -> bool:
    if status == "online":
        return True
    if event_types:
        return True
    return any(ref.strip() for ref in tag_filters or [])


class CaseTriggerConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: CaseTriggerStatus = "offline"
    event_types: list[CaseEventType] = Field(default_factory=list)
    tag_filters: list[str] = Field(default_factory=list)
    event_filters: CaseTriggerEventFilters = Field(
        default_factory=CaseTriggerEventFilters
    )

    @field_validator("event_types")
    @classmethod
    def dedupe_event_types(cls, value: list[CaseEventType]) -> list[CaseEventType]:
        return _dedupe_items(value)

    @field_validator("tag_filters")
    @classmethod
    def normalize_tag_filters(cls, value: list[str]) -> list[str]:
        normalized = [ref.strip() for ref in value if ref and ref.strip()]
        return _dedupe_items(normalized)

    @model_validator(mode="after")
    def validate_online_has_events(self) -> CaseTriggerConfig:
        self.event_filters = normalize_case_trigger_event_filters(
            self.event_filters,
            event_types=self.event_types,
        )
        if self.status == "online" and not self.event_types:
            raise ValueError("event_types must be non-empty when status is online")
        return self

    def is_configured(self) -> bool:
        return is_case_trigger_configured(
            status=self.status,
            event_types=self.event_types,
            tag_filters=self.tag_filters,
        )


class CaseTriggerCreate(CaseTriggerConfig):
    pass


class CaseTriggerUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: CaseTriggerStatus | None = None
    event_types: list[CaseEventType] | None = None
    tag_filters: list[str] | None = None
    event_filters: CaseTriggerEventFilters | None = None

    @field_validator("event_types")
    @classmethod
    def dedupe_event_types(
        cls, value: list[CaseEventType] | None
    ) -> list[CaseEventType] | None:
        if value is None:
            return None
        return _dedupe_items(value)

    @field_validator("tag_filters")
    @classmethod
    def normalize_tag_filters(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        normalized = [ref.strip() for ref in value if ref and ref.strip()]
        return _dedupe_items(normalized)

    @model_validator(mode="after")
    def validate_event_filter_event_types(self) -> CaseTriggerUpdate:
        if self.event_types is not None and self.event_filters is not None:
            self.event_filters = normalize_case_trigger_event_filters(
                self.event_filters,
                event_types=self.event_types,
            )
        return self


class CaseTriggerRead(Schema):
    id: uuid.UUID
    workflow_id: WorkflowID
    status: CaseTriggerStatus
    event_types: list[CaseEventType]
    tag_filters: list[str]
    event_filters: CaseTriggerEventFilters = Field(
        default_factory=CaseTriggerEventFilters
    )
