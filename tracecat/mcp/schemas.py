"""Typed response schemas for MCP tools."""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from tracecat.dsl.common import DSLInput


class LayoutPosition(BaseModel):
    x: float | None = None
    y: float | None = None
    position: dict[str, float] | None = None

    @model_validator(mode="after")
    def apply_nested_position(self) -> LayoutPosition:
        if self.position is not None:
            if self.x is None:
                self.x = self.position.get("x")
            if self.y is None:
                self.y = self.position.get("y")
        return self


class LayoutViewport(BaseModel):
    x: float | None = None
    y: float | None = None
    zoom: float | None = None


class LayoutActionPosition(BaseModel):
    ref: str
    x: float | None = None
    y: float | None = None
    position: dict[str, float] | None = None

    @model_validator(mode="after")
    def apply_nested_position(self) -> LayoutActionPosition:
        if self.position is not None:
            if self.x is None:
                self.x = self.position.get("x")
            if self.y is None:
                self.y = self.position.get("y")
        return self


class WorkflowLayout(BaseModel):
    trigger: LayoutPosition | None = None
    viewport: LayoutViewport | None = None
    actions: list[LayoutActionPosition] = Field(default_factory=list)


def _parse_iso8601_duration(duration_str: str) -> timedelta:
    """Parse a simple ISO 8601 duration string into a timedelta."""
    pattern = r"P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?"
    match = re.fullmatch(pattern, duration_str)
    if not match:
        raise ValueError(f"Invalid ISO 8601 duration: {duration_str}")

    days = int(match.group(1) or 0)
    hours = int(match.group(2) or 0)
    minutes = int(match.group(3) or 0)
    seconds = int(match.group(4) or 0)
    return timedelta(days=days, hours=hours, minutes=minutes, seconds=seconds)


class WorkflowSchedule(BaseModel):
    model_config = ConfigDict(extra="ignore")

    status: Literal["online", "offline"] = "online"
    inputs: dict[str, Any] | None = None
    cron: str | None = None
    every: timedelta | None = None
    offset: timedelta | None = None
    start_at: datetime | None = None
    end_at: datetime | None = None
    timeout: float = 0

    @field_validator("every", "offset", mode="before")
    @classmethod
    def parse_duration(cls, value: Any) -> Any:
        if isinstance(value, str):
            return _parse_iso8601_duration(value)
        return value

    @model_validator(mode="after")
    def validate_schedule_spec(self) -> WorkflowSchedule:
        if self.cron is None and self.every is None:
            raise ValueError("Either cron or every must be provided for a schedule")
        return self


class WorkflowYamlPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    definition: DSLInput | None = None
    layout: WorkflowLayout | None = None
    schedules: list[WorkflowSchedule] | None = None
    case_trigger: dict[str, Any] | None = None


class ActionSecretRequirement(BaseModel):
    name: str
    required_keys: list[str] = Field(default_factory=list)
    optional_keys: list[str] = Field(default_factory=list)


class ActionDiscoveryItem(BaseModel):
    action_name: str
    description: str | None = None
    configured: bool
    missing_requirements: list[str] = Field(default_factory=list)


class ActionContext(BaseModel):
    action_name: str
    description: str | None = None
    parameters_json_schema: dict[str, Any]
    required_secrets: list[ActionSecretRequirement] = Field(default_factory=list)
    configured: bool
    missing_requirements: list[str] = Field(default_factory=list)
    examples: list[dict[str, Any]] = Field(default_factory=list)


class VariableHint(BaseModel):
    name: str
    keys: list[str] = Field(default_factory=list)
    environment: str


class SecretHint(BaseModel):
    name: str
    keys: list[str] = Field(default_factory=list)
    environment: str


class WorkflowAuthoringContext(BaseModel):
    actions: list[ActionContext] = Field(default_factory=list)
    variable_hints: list[VariableHint] = Field(default_factory=list)
    secret_hints: list[SecretHint] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class ValidationResponse(BaseModel):
    valid: bool
    errors: list[dict[str, Any]] = Field(default_factory=list)


class WorkflowRunResponse(BaseModel):
    workflow_id: str
    execution_id: str
    message: str
