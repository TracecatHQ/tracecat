from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from fastapi.responses import ORJSONResponse
from pydantic import BaseModel, Field, field_validator

from tracecat.db.schemas import Schedule, Workflow, WorkflowDefinition
from tracecat.dsl.common import DSLInput, DSLRunArgs
from tracecat.dsl.models import ActionStatement, DSLConfig
from tracecat.expressions.expectations import ExpectedField
from tracecat.identifiers import OwnerID, WorkspaceID
from tracecat.identifiers.workflow import AnyWorkflowID, WorkflowIDShort, WorkflowUUID
from tracecat.registry.actions.models import RegistryActionValidateResponse
from tracecat.tags.models import TagRead
from tracecat.types.auth import Role
from tracecat.webhooks.models import WebhookRead
from tracecat.workflow.actions.models import ActionRead


class WorkflowRead(BaseModel):
    id: WorkflowIDShort
    title: str
    description: str
    status: str
    actions: dict[str, ActionRead]
    object: dict[str, Any] | None  # React Flow object
    owner_id: OwnerID
    version: int | None = None
    webhook: WebhookRead
    schedules: list[Schedule]
    entrypoint: str | None
    static_inputs: dict[str, Any]
    expects: dict[str, ExpectedField] | None = None
    returns: Any
    config: DSLConfig | None
    alias: str | None = None
    error_handler: str | None = None


class WorkflowDefinitionReadMinimal(BaseModel):
    id: str
    version: int
    created_at: datetime


class WorkflowReadMinimal(BaseModel):
    """Minimal version of WorkflowRead model for list endpoints."""

    id: WorkflowIDShort
    title: str
    description: str
    status: str
    icon_url: str | None
    created_at: datetime
    updated_at: datetime
    version: int | None
    tags: list[TagRead] | None = None
    alias: str | None = None
    error_handler: str | None = None
    latest_definition: WorkflowDefinitionReadMinimal | None = None
    folder_id: uuid.UUID | None = None


class WorkflowUpdate(BaseModel):
    title: str | None = Field(
        default=None,
        min_length=3,
        max_length=100,
        description="Workflow title, between 3 and 100 characters",
    )
    description: str | None = Field(
        default=None,
        max_length=1000,
        description="Optional workflow description, up to 1000 characters",
    )
    status: Literal["online", "offline"] | None = None
    object: dict[str, Any] | None = None
    version: int | None = None
    entrypoint: str | None = None
    icon_url: str | None = None
    static_inputs: dict[str, Any] | None = None
    expects: dict[str, ExpectedField] | None = None
    returns: Any | None = None
    config: DSLConfig | None = None
    alias: str | None = None
    error_handler: str | None = None


class WorkflowCreate(BaseModel):
    title: str | None = Field(
        default=None,
        min_length=3,
        max_length=100,
        description="Workflow title, between 3 and 100 characters",
    )
    description: str | None = Field(
        default=None,
        max_length=1000,
        description="Optional workflow description, up to 1000 characters",
    )


class GetWorkflowDefinitionActivityInputs(BaseModel):
    role: Role
    workflow_id: WorkflowUUID
    version: int | None = None
    task: ActionStatement | None = None

    @field_validator("workflow_id", mode="before")
    @classmethod
    def validate_workflow_id(cls, v: AnyWorkflowID) -> WorkflowUUID:
        """Convert any valid workflow ID format to WorkflowUUID."""
        return WorkflowUUID.new(v)


class ResolveWorkflowAliasActivityInputs(BaseModel):
    workflow_alias: str
    role: Role


class GetErrorHandlerWorkflowIDActivityInputs(BaseModel):
    role: Role
    args: DSLRunArgs


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
    workflow_id: WorkflowUUID | None = Field(
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
            workflow_id=WorkflowUUID.new(defn.workflow_id),
            created_at=defn.created_at,
            updated_at=defn.updated_at,
            version=defn.version,
            definition=DSLInput(**defn.content),
        )

    @field_validator("workflow_id", mode="before")
    @classmethod
    def validate_workflow_id(cls, v: AnyWorkflowID | None) -> WorkflowUUID | None:
        """Convert any valid workflow ID format to WorkflowUUID."""
        if v is None:
            return None
        return WorkflowUUID.new(v)


class WorkflowCommitResponse(BaseModel):
    workflow_id: WorkflowIDShort
    status: Literal["success", "failure"]
    message: str
    errors: list[RegistryActionValidateResponse] | None = None
    metadata: dict[str, Any] | None = None

    def to_orjson(self, status_code: int) -> ORJSONResponse:
        return ORJSONResponse(
            status_code=status_code, content=self.model_dump(exclude_none=True)
        )


class WorkflowDSLCreateResponse(BaseModel):
    workflow: Workflow | None = None
    errors: list[RegistryActionValidateResponse] | None = None


@dataclass
class WorkflowDefinitionMinimal:
    """Workflow definition metadata domain model."""

    id: str
    version: int
    created_at: datetime


class WorkflowMoveToFolder(BaseModel):
    folder_path: str | None = None
