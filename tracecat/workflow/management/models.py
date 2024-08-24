from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from tracecat.contexts import RunContext
from tracecat.db.schemas import Schedule, Workflow, WorkflowDefinition
from tracecat.dsl.common import DSLInput
from tracecat.dsl.models import ActionStatement
from tracecat.identifiers import OwnerID, WorkflowID, WorkspaceID
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


WorkflowExportFormat = Literal["json", "yaml"]


class ExternalWorkflowDefinition(BaseModel):
    """External interchange format for workflow definitions.

    Lets you restore a workflow from a JSON or YAML file."""

    workspace_id: WorkspaceID | None = Field(
        default=None,
        description=(
            "If provided, can only be restored in the same workspace (TBD)."
            "Otherwise, can be added to any workspace."
            "This will be set to `owner_id`"
        ),
    )
    workflow_id: WorkflowID | None = Field(
        default=None,
        description="Workflow ID. If not provided, a new workflow ID will be created.",
    )
    created_at: datetime | None = Field(
        default=None,
        description="Creation datetime of the workflow, will be set to current time if not provided.",
    )
    updated_at: datetime | None = Field(
        default=None,
        description="Last update datetime of the workflow, will be set to current time if not provided.",
    )
    version: int = Field(default=1, gt=0)
    definition: DSLInput

    @staticmethod
    def from_database(defn: WorkflowDefinition) -> ExternalWorkflowDefinition:
        return ExternalWorkflowDefinition(
            workspace_id=defn.owner_id,
            workflow_id=defn.workflow_id,
            created_at=defn.created_at,
            updated_at=defn.updated_at,
            version=defn.version,
            definition=defn.content,
        )
