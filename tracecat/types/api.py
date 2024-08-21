from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Literal

from fastapi.responses import ORJSONResponse
from pydantic import BaseModel, Field, ValidationError

from tracecat.db.schemas import Resource
from tracecat.identifiers import WorkflowID
from tracecat.types.exceptions import TracecatValidationError
from tracecat.types.validation import ValidationResult

# TODO: Consistent API design
# Action and Workflow create / update params
# should be the same as the metadata responses

RunStatus = Literal["pending", "running", "failure", "success", "canceled"]


class ActionControlFlow(BaseModel):
    run_if: str | None = None
    for_each: str | list[str] | None = None


class ActionResponse(BaseModel):
    id: str
    type: str
    title: str
    description: str
    status: str
    inputs: dict[str, Any]
    key: str  # Computed field
    control_flow: ActionControlFlow = Field(default_factory=ActionControlFlow)


class ActionMetadataResponse(BaseModel):
    id: str
    workflow_id: str
    type: str
    title: str
    description: str
    status: str
    key: str


class CreateActionParams(BaseModel):
    workflow_id: str
    type: str
    title: str


class UpdateActionParams(BaseModel):
    title: str | None = None
    description: str | None = None
    status: str | None = None
    inputs: dict[str, Any] | None = None
    control_flow: ActionControlFlow | None = None


class UpsertWebhookParams(BaseModel):
    status: Literal["online", "offline"] | None = None
    entrypoint_ref: str | None = None
    method: Literal["GET", "POST"] | None = None


class WebhookResponse(Resource):
    id: str
    secret: str
    status: Literal["online", "offline"]
    entrypoint_ref: str | None = None
    filters: dict[str, Any]
    method: Literal["GET", "POST"]
    workflow_id: str
    url: str


class SearchWebhooksParams(BaseModel):
    action_id: str | None = None
    workflow_id: str | None = None
    limit: int = 100
    order_by: str = "created_at"
    query: str | None = None
    group_by: list[str] | None = None
    agg: str | None = None


class TriggerWorkflowRunParams(BaseModel):
    action_key: str
    payload: dict[str, Any]


class StartWorkflowParams(BaseModel):
    entrypoint_key: str
    entrypoint_payload: dict[str, Any]


class StartWorkflowResponse(BaseModel):
    status: str
    message: str
    id: str


class UDFArgsValidationResponse(BaseModel):
    ok: bool
    message: str
    detail: Any | None = None

    @staticmethod
    def from_validation_result(
        result: ValidationResult,
    ) -> UDFArgsValidationResponse:
        return UDFArgsValidationResponse(
            ok=result.status == "success",
            message=result.msg,
            # Dump this to get subclass-specific fields
            detail=result.model_dump(exclude={"status", "msg"}, exclude_none=True),
        )

    @staticmethod
    def from_dsl_validation_error(exc: TracecatValidationError):
        return UDFArgsValidationResponse(ok=False, message=str(exc), detail=exc.detail)

    @staticmethod
    def from_pydantic_validation_error(exc: ValidationError):
        return UDFArgsValidationResponse(
            ok=False,
            message=f"Schema validation error: {exc.title}",
            detail=exc.errors(),
        )


class CommitWorkflowResponse(BaseModel):
    workflow_id: str
    status: Literal["success", "failure"]
    message: str
    errors: list[UDFArgsValidationResponse] | None = None
    metadata: dict[str, Any] | None = None

    def to_orjson(self, status_code: int) -> ORJSONResponse:
        return ORJSONResponse(
            status_code=status_code, content=self.model_dump(exclude_none=True)
        )


class ServiceCallbackAction(BaseModel):
    action: Literal["webhook"]
    payload: dict[str, Any]
    metadata: dict[str, Any]


class CreateScheduleParams(BaseModel):
    workflow_id: WorkflowID
    inputs: dict[str, Any] | None = None
    cron: str | None = None
    every: timedelta = Field(..., description="ISO 8601 duration string")
    offset: timedelta | None = Field(None, description="ISO 8601 duration string")
    start_at: datetime | None = Field(None, description="ISO 8601 datetime string")
    end_at: datetime | None = Field(None, description="ISO 8601 datetime string")
    status: Literal["online", "offline"] = "online"


class UpdateScheduleParams(BaseModel):
    inputs: dict[str, Any] | None = None
    cron: str | None = None
    every: timedelta | None = Field(None, description="ISO 8601 duration string")
    offset: timedelta | None = Field(None, description="ISO 8601 duration string")
    start_at: datetime | None = Field(None, description="ISO 8601 datetime string")
    end_at: datetime | None = Field(None, description="ISO 8601 datetime string")
    status: Literal["online", "offline"] | None = None


class SearchScheduleParams(BaseModel):
    workflow_id: str | None = None
    limit: int = 100
    order_by: str = "created_at"
    query: str | None = None
    group_by: list[str] | None = None
    agg: str | None = None
