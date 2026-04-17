"""Typed response schemas for MCP tools."""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Annotated, Any, Literal, TypeVar

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    field_validator,
    model_validator,
)

from tracecat.dsl.common import DSLEntrypoint, DSLInput
from tracecat.dsl.schemas import ActionRetryPolicy, ActionStatement, DSLConfig
from tracecat.interactions.schemas import ApprovalInteraction, ResponseInteraction
from tracecat.workflow.case_triggers.schemas import CaseTriggerConfig

T = TypeVar("T")
type JsonPrimitive = None | bool | int | float | str
type JsonValue = JsonPrimitive | list[JsonValue] | dict[str, JsonValue]


class LayoutPosition(BaseModel):
    x: float | None = None
    y: float | None = None
    position: dict[str, float] | None = None

    @model_validator(mode="after")
    def apply_nested_position(self) -> LayoutPosition:
        if self.position is not None:
            if self.x is None:
                self.x = self.position.get("x")
            if self.y is None:
                self.y = self.position.get("y")
        return self


class LayoutViewport(BaseModel):
    x: float | None = None
    y: float | None = None
    zoom: float | None = None


class LayoutActionPosition(BaseModel):
    ref: str
    x: float | None = None
    y: float | None = None
    position: dict[str, float] | None = None

    @model_validator(mode="after")
    def apply_nested_position(self) -> LayoutActionPosition:
        if self.position is not None:
            if self.x is None:
                self.x = self.position.get("x")
            if self.y is None:
                self.y = self.position.get("y")
        return self


class WorkflowLayout(BaseModel):
    trigger: LayoutPosition | None = None
    viewport: LayoutViewport | None = None
    actions: list[LayoutActionPosition] = Field(default_factory=list)


def _parse_iso8601_duration(duration_str: str) -> timedelta:
    """Parse a simple ISO 8601 duration string into a timedelta."""
    pattern = r"P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?"
    match = re.fullmatch(pattern, duration_str)
    if not match:
        raise ValueError(f"Invalid ISO 8601 duration: {duration_str}")

    days = int(match.group(1) or 0)
    hours = int(match.group(2) or 0)
    minutes = int(match.group(3) or 0)
    seconds = int(match.group(4) or 0)
    return timedelta(days=days, hours=hours, minutes=minutes, seconds=seconds)


class WorkflowSchedule(BaseModel):
    model_config = ConfigDict(extra="ignore")

    status: Literal["online", "offline"] = "online"
    inputs: dict[str, Any] | None = None
    cron: str | None = None
    every: timedelta | None = None
    offset: timedelta | None = None
    start_at: datetime | None = None
    end_at: datetime | None = None
    timeout: float = 0

    @field_validator("every", "offset", mode="before")
    @classmethod
    def parse_duration(cls, value: Any) -> Any:
        if isinstance(value, str):
            return _parse_iso8601_duration(value)
        return value

    @model_validator(mode="after")
    def validate_schedule_spec(self) -> WorkflowSchedule:
        if self.cron is None and self.every is None:
            raise ValueError("Either cron or every must be provided for a schedule")
        return self


class WorkflowYamlPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    definition: DSLInput | None = None
    layout: WorkflowLayout | None = None
    schedules: list[WorkflowSchedule] | None = None
    case_trigger: dict[str, Any] | None = None


class WorkflowEditMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=3, max_length=100)
    description: str = Field(max_length=1000)
    status: Literal["online", "offline"]
    alias: str | None = None
    error_handler: str | None = None


class _StrictWorkflowEditDSLEntrypoint(DSLEntrypoint):
    model_config = ConfigDict(extra="forbid")


class _StrictWorkflowEditDSLConfig(DSLConfig):
    model_config = ConfigDict(extra="forbid")


class _StrictWorkflowEditActionRetryPolicy(ActionRetryPolicy):
    model_config = ConfigDict(extra="forbid")


class _StrictWorkflowEditResponseInteraction(ResponseInteraction):
    model_config = ConfigDict(extra="forbid")


class _StrictWorkflowEditApprovalInteraction(ApprovalInteraction):
    model_config = ConfigDict(extra="forbid")


type _StrictWorkflowEditActionInteraction = Annotated[
    _StrictWorkflowEditResponseInteraction | _StrictWorkflowEditApprovalInteraction,
    Field(
        discriminator="type",
        description="An interaction configuration",
    ),
]
_STRICT_WORKFLOW_EDIT_ACTION_INTERACTION_ADAPTER: TypeAdapter[
    _StrictWorkflowEditActionInteraction
] = TypeAdapter(_StrictWorkflowEditActionInteraction)


class _StrictWorkflowEditAction(ActionStatement):
    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="before")
    @classmethod
    def validate_nested_fields(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        if (retry_policy := value.get("retry_policy")) is not None:
            _StrictWorkflowEditActionRetryPolicy.model_validate(retry_policy)
        if (interaction := value.get("interaction")) is not None:
            _STRICT_WORKFLOW_EDIT_ACTION_INTERACTION_ADAPTER.validate_python(
                interaction
            )
        return value


class _StrictWorkflowEditLayoutPosition(LayoutPosition):
    model_config = ConfigDict(extra="forbid")


class _StrictWorkflowEditLayoutViewport(LayoutViewport):
    model_config = ConfigDict(extra="forbid")


class _StrictWorkflowEditLayoutActionPosition(LayoutActionPosition):
    model_config = ConfigDict(extra="forbid")


class _StrictWorkflowEditLayout(WorkflowLayout):
    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="before")
    @classmethod
    def validate_nested_fields(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        if (trigger := value.get("trigger")) is not None:
            _StrictWorkflowEditLayoutPosition.model_validate(trigger)
        if (viewport := value.get("viewport")) is not None:
            _StrictWorkflowEditLayoutViewport.model_validate(viewport)
        for action in value.get("actions", []):
            _StrictWorkflowEditLayoutActionPosition.model_validate(action)
        return value


class _StrictWorkflowEditSchedule(WorkflowSchedule):
    model_config = ConfigDict(extra="forbid")


class _StrictWorkflowEditCaseTriggerConfig(CaseTriggerConfig):
    model_config = ConfigDict(extra="forbid")


class _StrictWorkflowEditDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entrypoint: _StrictWorkflowEditDSLEntrypoint = Field(
        default_factory=_StrictWorkflowEditDSLEntrypoint
    )
    actions: list[_StrictWorkflowEditAction] = Field(default_factory=list)
    config: _StrictWorkflowEditDSLConfig = Field(
        default_factory=_StrictWorkflowEditDSLConfig
    )
    returns: Any | None = None


class _StrictWorkflowEditDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    metadata: WorkflowEditMetadata
    definition: _StrictWorkflowEditDefinition
    layout: _StrictWorkflowEditLayout = Field(default_factory=_StrictWorkflowEditLayout)
    schedules: list[_StrictWorkflowEditSchedule] = Field(default_factory=list)
    case_trigger: _StrictWorkflowEditCaseTriggerConfig | None = None


class WorkflowEditDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entrypoint: DSLEntrypoint = Field(default_factory=DSLEntrypoint)
    actions: list[ActionStatement] = Field(default_factory=list)
    config: DSLConfig = Field(default_factory=DSLConfig)
    returns: Any | None = None


class WorkflowEditDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    metadata: WorkflowEditMetadata
    definition: WorkflowEditDefinition
    layout: WorkflowLayout = Field(default_factory=WorkflowLayout)
    schedules: list[WorkflowSchedule] = Field(default_factory=list)
    case_trigger: CaseTriggerConfig | None = None

    @model_validator(mode="before")
    @classmethod
    def reject_unknown_nested_fields(cls, value: Any) -> Any:
        _StrictWorkflowEditDocument.model_validate(value)
        return value


class JsonPatchOperation(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    op: Literal["add", "remove", "replace", "move", "copy", "test"]
    path: str
    from_: str | None = Field(default=None, alias="from")
    value: JsonValue | None = None

    @model_validator(mode="after")
    def validate_operation_shape(self) -> JsonPatchOperation:
        has_value = "value" in self.model_fields_set
        match self.op:
            case "add" | "replace" | "test":
                if not has_value:
                    raise ValueError(
                        f"Patch operation {self.op!r} requires field 'value'"
                    )
            case "move" | "copy":
                if self.from_ is None:
                    raise ValueError(
                        f"Patch operation {self.op!r} requires field 'from'"
                    )
        return self


class WorkflowEditRequest(BaseModel):
    base_revision: str
    patch_ops: list[JsonPatchOperation]
    validate_only: bool = False


class WorkflowEditResponse(BaseModel):
    message: str
    workflow_id: str
    draft_revision: str
    valid: bool | None = None
    validate_only: bool = False


class ActionSecretRequirement(BaseModel):
    name: str
    required_keys: list[str] = Field(default_factory=list)
    optional_keys: list[str] = Field(default_factory=list)


class ActionDiscoveryItem(BaseModel):
    action_name: str
    description: str | None = None
    configured: bool
    missing_requirements: list[str] = Field(default_factory=list)


class ActionContext(BaseModel):
    action_name: str
    description: str | None = None
    parameters_json_schema: dict[str, Any]
    required_secrets: list[ActionSecretRequirement] = Field(default_factory=list)
    configured: bool
    missing_requirements: list[str] = Field(default_factory=list)
    examples: list[dict[str, Any]] = Field(default_factory=list)


class VariableHint(BaseModel):
    name: str
    keys: list[str] = Field(default_factory=list)
    environment: str


class SecretHint(BaseModel):
    name: str
    keys: list[str] = Field(default_factory=list)
    environment: str


class WorkflowAuthoringContext(BaseModel):
    actions: list[ActionContext] = Field(default_factory=list)
    variable_hints: list[VariableHint] = Field(default_factory=list)
    secret_hints: list[SecretHint] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class ValidationResponse(BaseModel):
    valid: bool
    errors: list[dict[str, Any]] = Field(default_factory=list)


class WorkflowRunResponse(BaseModel):
    workflow_id: str
    execution_id: str
    message: str


class MCPPaginatedResponse[T](BaseModel):
    items: list[T]
    next_cursor: str | None = Field(default=None)
    prev_cursor: str | None = Field(default=None)
    has_more: bool = Field(default=False)
    has_previous: bool = Field(default=False)


class MCPTruncationInfo(BaseModel):
    limit: int
    total: int
    returned: int
    truncated: bool


class MCPTruncationSummary(BaseModel):
    collections: dict[str, MCPTruncationInfo] = Field(default_factory=dict)
