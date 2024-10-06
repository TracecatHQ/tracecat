from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from tracecat.db.schemas import Resource

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


class ServiceCallbackAction(BaseModel):
    action: Literal["webhook"]
    payload: dict[str, Any]
    metadata: dict[str, Any]
