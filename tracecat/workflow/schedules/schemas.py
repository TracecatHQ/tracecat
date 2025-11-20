from datetime import datetime, timedelta
from typing import Any, Literal

from croniter import croniter
from pydantic import BaseModel, Field, field_validator, model_validator

from tracecat.auth.types import Role
from tracecat.core.schemas import Schema
from tracecat.identifiers import OwnerID, ScheduleID, WorkflowID
from tracecat.identifiers.workflow import AnyWorkflowID


class ScheduleRead(Schema):
    id: ScheduleID
    owner_id: OwnerID
    created_at: datetime
    updated_at: datetime
    workflow_id: WorkflowID
    inputs: dict[str, Any] | None = None
    cron: str | None = None
    every: timedelta | None = None
    offset: timedelta | None = None
    start_at: datetime | None = None
    end_at: datetime | None = None
    timeout: float | None = None
    status: Literal["online", "offline"]


class ScheduleCreate(BaseModel):
    workflow_id: AnyWorkflowID
    inputs: dict[str, Any] | None = None
    cron: str | None = None
    every: timedelta | None = Field(
        default=None, description="ISO 8601 duration string"
    )
    offset: timedelta | None = Field(
        default=None, description="ISO 8601 duration string"
    )
    start_at: datetime | None = Field(
        default=None, description="ISO 8601 datetime string"
    )
    end_at: datetime | None = Field(
        default=None, description="ISO 8601 datetime string"
    )
    status: Literal["online", "offline"] = "online"
    timeout: float = Field(
        default=0,
        description="The maximum number of seconds to wait for the workflow to complete",
    )

    @model_validator(mode="after")
    def validate_schedule_spec(self) -> "ScheduleCreate":
        """Ensure at least one schedule specifier is provided."""
        if self.cron is None and self.every is None:
            raise ValueError("Either cron or every must be provided for a schedule")
        return self

    @field_validator("cron")
    @classmethod
    def validate_cron_expression(cls, cron: str | None) -> str | None:
        if cron and not croniter.is_valid(cron):
            raise ValueError(f"Invalid cron expression: {cron}")
        return cron


class ScheduleUpdate(BaseModel):
    inputs: dict[str, Any] | None = None
    cron: str | None = None
    every: timedelta | None = Field(
        default=None, description="ISO 8601 duration string"
    )
    offset: timedelta | None = Field(
        default=None, description="ISO 8601 duration string"
    )
    start_at: datetime | None = Field(
        default=None, description="ISO 8601 datetime string"
    )
    end_at: datetime | None = Field(
        default=None, description="ISO 8601 datetime string"
    )
    status: Literal["online", "offline"] | None = None

    @field_validator("cron")
    @classmethod
    def validate_cron_expression(cls, cron: str | None) -> str | None:
        if cron and not croniter.is_valid(cron):
            raise ValueError(f"Invalid cron expression: {cron}")
        return cron


class ScheduleSearch(BaseModel):
    workflow_id: str | None = None
    limit: int = 100
    order_by: str = "created_at"
    query: str | None = None
    group_by: list[str] | None = None
    agg: str | None = None


class GetScheduleActivityInputs(BaseModel):
    role: Role
    schedule_id: ScheduleID
    workflow_id: WorkflowID
