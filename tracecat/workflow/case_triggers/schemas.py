from __future__ import annotations

import uuid
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from tracecat.cases.enums import CaseEventType
from tracecat.core.schemas import Schema
from tracecat.identifiers import WorkflowID

CaseTriggerStatus = Literal["online", "offline"]


def _dedupe_items[T](items: list[T]) -> list[T]:
    seen: set[T] = set()
    deduped: list[T] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        deduped.append(item)
    return deduped


class CaseTriggerConfig(BaseModel):
    status: CaseTriggerStatus = "offline"
    event_types: list[CaseEventType] = Field(default_factory=list)
    tag_filters: list[str] = Field(default_factory=list)

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
        if self.status == "online" and not self.event_types:
            raise ValueError("event_types must be non-empty when status is online")
        return self


class CaseTriggerCreate(CaseTriggerConfig):
    pass


class CaseTriggerUpdate(BaseModel):
    status: CaseTriggerStatus | None = None
    event_types: list[CaseEventType] | None = None
    tag_filters: list[str] | None = None

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


class CaseTriggerRead(Schema):
    id: uuid.UUID
    workflow_id: WorkflowID
    status: CaseTriggerStatus
    event_types: list[CaseEventType]
    tag_filters: list[str]
