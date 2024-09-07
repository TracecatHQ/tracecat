from datetime import datetime, timedelta
from typing import Any, Literal

from pydantic import BaseModel, Field

from tracecat.identifiers import WorkflowID


class ScheduleCreate(BaseModel):
    workflow_id: WorkflowID
    inputs: dict[str, Any] | None = None
    cron: str | None = None
    every: timedelta = Field(..., description="ISO 8601 duration string")
    offset: timedelta | None = Field(None, description="ISO 8601 duration string")
    start_at: datetime | None = Field(None, description="ISO 8601 datetime string")
    end_at: datetime | None = Field(None, description="ISO 8601 datetime string")
    status: Literal["online", "offline"] = "online"


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
