from datetime import datetime, timedelta
from typing import Any, Literal

from croniter import croniter
from pydantic import BaseModel, Field, field_validator, model_validator

from tracecat.auth.types import Role
from tracecat.core.schemas import Schema
from tracecat.identifiers import ScheduleUUID, WorkflowID, WorkspaceID
from tracecat.identifiers.workflow import AnyWorkflowID


def check_cron(cron: str | None) -> str | None:
    """Reject a cron expression croniter cannot parse."""
    if cron and not croniter.is_valid(cron):
        raise ValueError(f"Invalid cron expression: {cron}")
    return cron


def check_schedule_spec(cron: str | None, every: timedelta | None) -> None:
    """Require exactly one of ``cron`` and ``every``."""
    if cron is None and every is None:
        raise ValueError("Either cron or every must be provided for a schedule")
    if cron is not None and every is not None:
        raise ValueError("Only one of cron or every may be provided for a schedule")


class ScheduleRead(Schema):
    id: ScheduleUUID
    workspace_id: WorkspaceID
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
        check_schedule_spec(self.cron, self.every)
        return self

    @field_validator("cron")
    @classmethod
    def validate_cron_expression(cls, cron: str | None) -> str | None:
        return check_cron(cron)


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
    timeout: float | None = Field(
        default=None,
        ge=0,
        description="The maximum number of seconds to wait for the workflow to complete",
    )

    @field_validator("cron")
    @classmethod
    def validate_cron_expression(cls, cron: str | None) -> str | None:
        return check_cron(cron)


class ScheduleSearch(BaseModel):
    workflow_id: str | None = None
    limit: int = 100
    order_by: str = "created_at"
    query: str | None = None
    group_by: list[str] | None = None
    agg: str | None = None


class GetScheduleActivityInputs(BaseModel):
    """Activity input for fetching schedule data.

    ScheduleUUID (ScheduleUUID) auto-validates from any format:
    - Native UUID
    - Short ID: sch_xxx
    - Legacy format: sch-<32hex>
    """

    role: Role
    schedule_id: ScheduleUUID
    workflow_id: WorkflowID
