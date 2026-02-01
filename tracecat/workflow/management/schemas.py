from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from fastapi.responses import ORJSONResponse
from pydantic import BaseModel, Field, field_validator

from tracecat.auth.types import Role
from tracecat.core.schemas import Schema
from tracecat.db.models import Workflow, WorkflowDefinition
from tracecat.dsl.common import DSLInput, DSLRunArgs
from tracecat.dsl.schemas import ActionStatement, DSLConfig
from tracecat.expressions.expectations import ExpectedField
from tracecat.identifiers import WorkspaceID
from tracecat.identifiers.workflow import AnyWorkflowID, WorkflowIDShort, WorkflowUUID
from tracecat.registry.lock.types import RegistryLock
from tracecat.tags.schemas import TagRead
from tracecat.validation.schemas import ValidationResult
from tracecat.webhooks.schemas import WebhookRead
from tracecat.workflow.actions.schemas import ActionRead
from tracecat.workflow.case_triggers.schemas import CaseTriggerConfig
from tracecat.workflow.schedules.schemas import ScheduleRead


class WorkflowRead(Schema):
    id: WorkflowIDShort
    title: str
    description: str
    status: str
    actions: dict[str, ActionRead]
    workspace_id: WorkspaceID
    version: int | None = None
    webhook: WebhookRead
    schedules: list[ScheduleRead]
    entrypoint: str | None
    expects: dict[str, ExpectedField] | None = None
    expects_schema: dict[str, Any] | None = None
    returns: Any
    config: DSLConfig | None
    alias: str | None = None
    error_handler: str | None = None
    trigger_position_x: float = 0.0
    trigger_position_y: float = 0.0
    graph_version: int = 1


class WorkflowDefinitionReadMinimal(Schema):
    id: uuid.UUID
    version: int
    created_at: datetime


class WorkflowDefinitionRead(Schema):
    """API response model for persisted workflow definitions."""

    id: uuid.UUID
    workflow_id: WorkflowUUID | None
    workspace_id: WorkspaceID
    version: int
    content: dict[str, Any] | None = None
    created_at: datetime
    updated_at: datetime


class WorkflowReadMinimal(Schema):
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
    version: int | None = None
    entrypoint: str | None = None
    icon_url: str | None = None
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


class WorkflowDefinitionActivityResult(BaseModel):
    """Result from get_workflow_definition_activity.

    Contains both the DSL and the registry lock for this workflow definition.
    """

    dsl: DSLInput
    registry_lock: RegistryLock | None = None


class ResolveRegistryLockActivityInputs(BaseModel):
    """Inputs for resolve_registry_lock_activity."""

    role: Role
    action_names: set[str]


class ResolveWorkflowAliasActivityInputs(BaseModel):
    workflow_alias: str
    """Possibly a templated expression"""
    role: Role
    use_committed: bool = True
    """Use committed WorkflowDefinition alias (True) or draft Workflow alias (False)."""


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
            "This will be set to `workspace_id`"
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
    case_trigger: CaseTriggerConfig | None = None

    @staticmethod
    def from_database(
        defn: WorkflowDefinition,
        *,
        case_trigger: CaseTriggerConfig | None = None,
    ) -> ExternalWorkflowDefinition:
        if case_trigger is None and defn.workflow and defn.workflow.case_trigger:
            case_trigger = CaseTriggerConfig(
                status=defn.workflow.case_trigger.status,
                event_types=defn.workflow.case_trigger.event_types,
                tag_filters=defn.workflow.case_trigger.tag_filters,
            )
        return ExternalWorkflowDefinition(
            workspace_id=defn.workspace_id,
            workflow_id=WorkflowUUID.new(defn.workflow_id),
            created_at=defn.created_at,
            updated_at=defn.updated_at,
            version=defn.version,
            definition=DSLInput.model_validate(defn.content),
            case_trigger=case_trigger,
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
    errors: list[ValidationResult] | None = None
    metadata: dict[str, Any] | None = None

    def to_orjson(self, status_code: int) -> ORJSONResponse:
        return ORJSONResponse(
            status_code=status_code, content=self.model_dump(exclude_none=True)
        )


class WorkflowDSLCreateResponse(Schema):
    workflow: Workflow | None = None
    errors: list[ValidationResult] | None = None


class WorkflowEntrypointValidationRequest(BaseModel):
    expects: dict[str, ExpectedField] | None = None


class WorkflowEntrypointValidationResponse(BaseModel):
    valid: bool
    errors: list[ValidationResult] = Field(default_factory=list)


class WorkflowMoveToFolder(BaseModel):
    folder_path: str | None = None


# =============================================================================
# Graph API Schemas
# =============================================================================


class GraphResponse(Schema):
    """Response for GET /workflows/{id}/graph.

    Returns the canonical graph projection from Actions.
    """

    version: int = Field(description="Graph version for optimistic concurrency")
    nodes: list[dict[str, Any]] = Field(description="React Flow nodes")
    edges: list[dict[str, Any]] = Field(description="React Flow edges")
    viewport: dict[str, Any] = Field(
        default_factory=lambda: {"x": 0, "y": 0, "zoom": 1}
    )


class GraphOperationType(StrEnum):
    """Graph operation types."""

    ADD_NODE = "add_node"
    UPDATE_NODE = "update_node"
    DELETE_NODE = "delete_node"
    ADD_EDGE = "add_edge"
    DELETE_EDGE = "delete_edge"
    MOVE_NODES = "move_nodes"
    UPDATE_TRIGGER_POSITION = "update_trigger_position"
    UPDATE_VIEWPORT = "update_viewport"


class GraphOperation(Schema):
    """A single graph operation."""

    type: GraphOperationType
    payload: dict[str, Any] = Field(description="Operation-specific payload")


class GraphOperationsRequest(Schema):
    """Request for PATCH /workflows/{id}/graph.

    Applies a batch of graph operations with optimistic concurrency.
    """

    base_version: int = Field(
        description="Expected current graph_version. Returns 409 if mismatched."
    )
    operations: list[GraphOperation] = Field(
        description="List of operations to apply atomically"
    )


class AddNodePayload(Schema):
    """Payload for add_node operation."""

    type: str = Field(description="Action type (UDF key)")
    title: str = Field(description="Action title")
    description: str | None = Field(default="", description="Action description")
    inputs: str | None = Field(
        default=None,
        description="YAML inputs for the action. Defaults to None.",
    )
    control_flow: dict[str, Any] | None = Field(
        default=None,
        description="Control flow configuration for the action",
    )
    position_x: float = 0.0
    position_y: float = 0.0


class UpdateNodePayload(Schema):
    """Payload for update_node operation."""

    action_id: uuid.UUID
    title: str | None = None
    description: str | None = None
    inputs: str | None = None
    control_flow: dict[str, Any] | None = None


class DeleteNodePayload(Schema):
    """Payload for delete_node operation."""

    action_id: uuid.UUID


class AddEdgePayload(Schema):
    """Payload for add_edge operation."""

    source_id: str = Field(
        description="Source node ID. For trigger: 'trigger-{uuid}', for action: action UUID"
    )
    source_type: Literal["trigger", "udf"] = Field(description="Type of source node")
    target_id: uuid.UUID = Field(description="Target action ID")
    source_handle: Literal["success", "error"] | None = Field(
        default=None,
        description="Edge handle type. Required for 'udf' source, ignored for 'trigger'",
    )


class DeleteEdgePayload(Schema):
    """Payload for delete_edge operation."""

    source_id: str = Field(
        description="Source node ID. For trigger: 'trigger-{uuid}', for action: action UUID"
    )
    source_type: Literal["trigger", "udf"] = Field(description="Type of source node")
    target_id: uuid.UUID
    source_handle: Literal["success", "error"] | None = Field(
        default=None,
        description="Edge handle type. Required for 'udf' source, ignored for 'trigger'",
    )


class MoveNodesPayload(Schema):
    """Payload for move_nodes operation (layout only)."""

    positions: list[dict[str, Any]] = Field(
        description="List of {action_id, x, y} positions"
    )


class UpdateTriggerPositionPayload(Schema):
    """Payload for update_trigger_position operation."""

    x: float
    y: float


class UpdateViewportPayload(Schema):
    """Payload for update_viewport operation."""

    x: float
    y: float
    zoom: float
