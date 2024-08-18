from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel

from tracecat.contexts import RunContext
from tracecat.db.schemas import Schedule, Workflow
from tracecat.dsl.models import ActionStatement
from tracecat.identifiers import OwnerID, WorkflowID
from tracecat.types.api import (
    ActionResponse,
    UDFArgsValidationResponse,
    WebhookResponse,
)
from tracecat.types.auth import Role


class CreateWorkflowFromDSLResponse(BaseModel):
    workflow: Workflow | None = None
    errors: list[UDFArgsValidationResponse] | None = None


class WorkflowResponse(BaseModel):
    id: str
    title: str
    description: str
    status: str
    actions: dict[str, ActionResponse]
    object: dict[str, Any] | None  # React Flow object
    owner_id: OwnerID
    version: int | None = None
    webhook: WebhookResponse
    schedules: list[Schedule]
    entrypoint: str | None
    static_inputs: dict[str, Any]
    returns: Any
    config: dict[str, Any] | None


class UpdateWorkflowParams(BaseModel):
    title: str | None = None
    description: str | None = None
    status: Literal["online", "offline"] | None = None
    object: dict[str, Any] | None = None
    version: int | None = None
    entrypoint: str | None = None
    icon_url: str | None = None
    static_inputs: dict[str, Any] | None = None
    returns: Any | None = None


class WorkflowMetadataResponse(BaseModel):
    id: str
    title: str
    description: str
    status: str
    icon_url: str | None
    created_at: datetime
    updated_at: datetime
    version: int | None


class CreateWorkflowParams(BaseModel):
    title: str | None = None
    description: str | None = None


class GetWorkflowDefinitionActivityInputs(BaseModel):
    role: Role
    task: ActionStatement
    workflow_id: WorkflowID
    trigger_inputs: dict[str, Any]
    version: int | None = None
    run_context: RunContext
