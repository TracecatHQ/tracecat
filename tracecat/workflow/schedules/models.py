from datetime import datetime, timedelta
from typing import Any, Literal

from pydantic import BaseModel, Field

from tracecat.identifiers import OwnerID, ScheduleID, WorkflowID
from tracecat.identifiers.workflow import AnyWorkflowID
from tracecat.types.auth import Role


class ScheduleRead(BaseModel):
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
    every: timedelta = Field(..., description="ISO 8601 duration string")
    offset: timedelta | None = Field(None, description="ISO 8601 duration string")
    start_at: datetime | None = Field(None, description="ISO 8601 datetime string")
    end_at: datetime | None = Field(None, description="ISO 8601 datetime string")
    status: Literal["online", "offline"] = "online"
    timeout: float = Field(
        300,
        description="The maximum number of seconds to wait for the workflow to complete",
    )


class ScheduleUpdate(BaseModel):
    inputs: dict[str, Any] | None = None
    cron: str | None = None
    every: timedelta | None = Field(None, description="ISO 8601 duration string")
    offset: timedelta | None = Field(None, description="ISO 8601 duration string")
    start_at: datetime | None = Field(None, description="ISO 8601 datetime string")
    end_at: datetime | None = Field(None, description="ISO 8601 datetime string")
    status: Literal["online", "offline"] | None = None


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
