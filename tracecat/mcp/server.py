"""Standalone MCP server for Tracecat workflow management.

Exposes workflow operations to external MCP clients (Claude Desktop, Cursor, etc.).
Users authenticate via their existing Tracecat OIDC login.
"""

from __future__ import annotations

import asyncio
import csv
import json
import re
import uuid
from collections import defaultdict, deque
from datetime import datetime, timedelta
from io import StringIO
from typing import Any, Literal, cast, get_args

import sqlalchemy as sa
import yaml
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.server.middleware.error_handling import ErrorHandlingMiddleware
from fastmcp.server.middleware.logging import LoggingMiddleware
from fastmcp.server.middleware.rate_limiting import RateLimitingMiddleware
from google.protobuf.json_format import MessageToDict
from mcp.types import Annotations, TextContent
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)
from sqlalchemy import delete, select
from sqlalchemy.exc import NoResultFound
from temporalio.client import WorkflowExecutionStatus
from tracecat_registry import RegistryOAuthSecret, RegistrySecret

from tracecat import config
from tracecat.agent.common.stream_types import StreamEventType, UnifiedStreamEvent
from tracecat.agent.preset.schemas import AgentPresetCreate, AgentPresetRead
from tracecat.agent.preset.service import AgentPresetService
from tracecat.agent.service import AgentManagementService
from tracecat.agent.session.schemas import AgentSessionCreate
from tracecat.agent.session.service import AgentSessionService
from tracecat.agent.session.types import AgentSessionEntity
from tracecat.agent.stream.connector import AgentStream
from tracecat.agent.stream.events import StreamDelta, StreamEnd, StreamError
from tracecat.agent.tools import create_tool_from_registry
from tracecat.agent.types import OutputType
from tracecat.cases.enums import CaseEventType
from tracecat.cases.schemas import (
    CaseFieldCreate,
    CaseFieldReadMinimal,
    CaseFieldUpdate,
)
from tracecat.cases.service import CaseFieldsService
from tracecat.cases.tags.schemas import CaseTagRead
from tracecat.cases.tags.service import CaseTagsService
from tracecat.chat.schemas import BasicChatRequest, ChatRequest
from tracecat.db.engine import get_async_session_context_manager
from tracecat.db.models import Action, Workflow, WorkflowDefinition
from tracecat.dsl.common import (
    DSLInput,
    get_execution_type_from_search_attr,
    get_trigger_type_from_search_attr,
)
from tracecat.dsl.validation import (
    format_input_schema_validation_error,
    normalize_trigger_inputs,
)
from tracecat.exceptions import TracecatNotFoundError, TracecatValidationError
from tracecat.identifiers.workflow import (
    WorkflowUUID,
    exec_id_to_parts,
)
from tracecat.integrations.enums import IntegrationStatus
from tracecat.integrations.providers import all_providers
from tracecat.integrations.service import IntegrationService
from tracecat.logger import logger
from tracecat.mcp.auth import (
    create_mcp_auth,
    list_workspaces_for_request,
    resolve_role_for_request,
)
from tracecat.mcp.config import (
    TRACECAT_MCP__RATE_LIMIT_BURST,
    TRACECAT_MCP__RATE_LIMIT_RPS,
)
from tracecat.mcp.middleware import (
    MCPInputSizeLimitMiddleware,
    MCPTimeoutMiddleware,
    WatchtowerMonitorMiddleware,
    get_mcp_client_id,
)
from tracecat.pagination import CursorPaginationParams
from tracecat.registry.actions.schemas import TemplateAction
from tracecat.registry.actions.service import (
    RegistryActionsService,
)
from tracecat.registry.actions.service import (
    validate_action_template as validate_template_action_impl,
)
from tracecat.registry.lock.service import RegistryLockService
from tracecat.registry.lock.types import RegistryLock
from tracecat.registry.repository import Repository
from tracecat.secrets.constants import DEFAULT_SECRETS_ENVIRONMENT
from tracecat.secrets.service import SecretsService
from tracecat.tables.enums import SqlType
from tracecat.tables.schemas import TableCreate, TableRowInsert, TableUpdate
from tracecat.tables.service import TablesService
from tracecat.tags.schemas import TagCreate, TagRead, TagUpdate
from tracecat.tags.service import TagsService
from tracecat.validation.schemas import ValidationDetail
from tracecat.validation.service import validate_dsl
from tracecat.variables.service import VariablesService
from tracecat.webhooks import service as webhook_service
from tracecat.webhooks.schemas import WebhookRead, WebhookUpdate
from tracecat.workflow.case_triggers.schemas import (
    CaseTriggerConfig,
    CaseTriggerRead,
    CaseTriggerUpdate,
    is_case_trigger_configured,
)
from tracecat.workflow.case_triggers.service import CaseTriggersService
from tracecat.workflow.executions.service import WorkflowExecutionsService
from tracecat.workflow.management.definitions import WorkflowDefinitionsService
from tracecat.workflow.management.folders.service import WorkflowFolderService
from tracecat.workflow.management.management import WorkflowsManagementService
from tracecat.workflow.management.schemas import WorkflowCreate, WorkflowUpdate
from tracecat.workflow.schedules.schemas import (
    ScheduleCreate,
    ScheduleRead,
    ScheduleUpdate,
)
from tracecat.workflow.schedules.service import WorkflowSchedulesService
from tracecat.workflow.tags.service import WorkflowTagsService


def _parse_json_arg(raw_value: str | None, field_name: str) -> Any | None:
    """Parse a JSON-encoded argument."""
    if raw_value is None:
        return None
    try:
        return json.loads(raw_value)
    except json.JSONDecodeError as exc:
        raise ToolError(f"Invalid JSON for {field_name}: {exc}") from exc


def _validation_result_payload(vr: Any) -> dict[str, Any]:
    """Serialize a validation result for user-facing error output."""
    payload = vr.root.model_dump(mode="json", exclude_none=True)
    if "msg" in payload and "message" not in payload:
        payload["message"] = payload["msg"]
    if payload.get("detail") is not None:
        payload["details"] = [
            (
                {
                    "type": detail.get("type", ""),
                    "msg": detail.get("msg", str(detail)),
                    "loc": list(detail.get("loc", ()) or ()),
                }
                if isinstance(detail, dict)
                else {
                    "type": getattr(detail, "type", ""),
                    "msg": getattr(detail, "msg", str(detail)),
                    "loc": list(getattr(detail, "loc", ()) or ()),
                }
            )
            for detail in payload["detail"]
        ]
    return payload


def _validation_detail_to_payload(
    details: list[ValidationDetail],
) -> list[dict[str, Any]]:
    """Serialize trigger validation details into JSON-serializable dictionaries."""
    return [
        {
            "type": detail.type,
            "msg": detail.msg,
            "loc": list(detail.loc) if detail.loc is not None else None,
        }
        for detail in details
    ]


def _validate_and_parse_trigger_inputs(
    dsl_input: DSLInput,
    inputs: str | None,
) -> Any | None:
    """Parse trigger inputs and validate using the DSL entrypoint schema."""
    parsed_inputs = _parse_json_arg(inputs, "inputs")

    expects = dsl_input.entrypoint.expects
    if not expects:
        return parsed_inputs

    try:
        normalize_trigger_inputs(
            expects,
            {} if parsed_inputs is None else parsed_inputs,
            model_name="TriggerInputsValidator",
        )
    except ValidationError as exc:
        details = ValidationDetail.list_from_pydantic(exc)
        raise ToolError(
            _json(
                {
                    "type": "validation_error",
                    "message": format_input_schema_validation_error(details),
                    "status": "error",
                    "details": _validation_detail_to_payload(details),
                    "input_schema": {
                        field_name: field.model_dump(mode="json")
                        for field_name, field in expects.items()
                    },
                }
            )
        ) from exc

    return parsed_inputs


async def _resolve_workspace_role(workspace_id: str) -> tuple[uuid.UUID, Any]:
    """Resolve workspace UUID + role from current token."""
    try:
        ws_id = uuid.UUID(workspace_id)
    except ValueError as exc:
        raise ToolError("Invalid workspace ID") from exc
    try:
        role = await resolve_role_for_request(ws_id)
    except ValueError as exc:
        raise ToolError(str(exc)) from exc
    return ws_id, role


def _normalize_folder_path_arg(path: str, *, allow_root: bool = True) -> str:
    """Normalize a user-supplied folder path."""
    raw = path.strip()
    if not raw:
        raise ToolError("Path is required")
    if raw == "/":
        if allow_root:
            return raw
        raise ToolError("Root path '/' is not valid for this operation")
    if not raw.startswith("/"):
        raise ToolError("Path must start with '/'")

    parts = [part for part in raw.split("/") if part]
    if any(part in {".", ".."} for part in parts):
        raise ToolError("Path cannot contain '.' or '..' segments")
    if not parts:
        if allow_root:
            return "/"
        raise ToolError("Root path '/' is not valid for this operation")
    return f"/{'/'.join(parts)}/"


def _build_example_from_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Build a compact example payload from JSON schema properties."""
    example: dict[str, Any] = {}
    properties = schema.get("properties", {})
    required = schema.get("required", [])
    for key in required:
        prop = properties.get(key, {})
        prop_type = prop.get("type")
        if prop_type == "string":
            example[key] = "example"
        elif prop_type == "integer":
            example[key] = 1
        elif prop_type == "number":
            example[key] = 1.0
        elif prop_type == "boolean":
            example[key] = True
        elif prop_type == "array":
            example[key] = []
        elif prop_type == "object":
            example[key] = {}
        else:
            example[key] = "value"
    return example


def _secrets_to_requirements(secrets: list[Any]) -> list[dict[str, Any]]:
    """Convert registry secret objects to public requirement metadata."""
    requirements: list[dict[str, Any]] = []
    for secret in secrets:
        if isinstance(secret, RegistrySecret):
            requirements.append(
                {
                    "name": secret.name,
                    "required_keys": list(secret.keys or []),
                    "optional_keys": list(secret.optional_keys or []),
                    "optional": secret.optional,
                }
            )
        elif isinstance(secret, RegistryOAuthSecret):
            requirements.append(
                {
                    "name": secret.name,
                    "required_keys": [secret.token_name],
                    "optional_keys": [],
                    "optional": secret.optional,
                }
            )
    return requirements


async def _load_secret_inventory(
    role: Any,
) -> dict[str, set[str]]:
    """Load workspace secret key inventory for the default environment."""

    async with SecretsService.with_session(role=role) as svc:
        workspace_inventory: dict[str, set[str]] = {}

        workspace_secrets = await svc.list_secrets()
        for secret in workspace_secrets:
            if secret.environment != DEFAULT_SECRETS_ENVIRONMENT:
                continue
            keys = {kv.key for kv in svc.decrypt_keys(secret.encrypted_keys)}
            workspace_inventory[secret.name] = keys

        return workspace_inventory


def _evaluate_configuration(
    requirements: list[dict[str, Any]],
    workspace_inventory: dict[str, set[str]],
) -> tuple[bool, list[str]]:
    """Evaluate whether required secret names/keys are configured."""
    missing: list[str] = []
    for req in requirements:
        secret_name = req["name"]
        required_keys = set(req["required_keys"])
        if not required_keys and req.get("optional", False):
            continue
        available_keys = workspace_inventory.get(secret_name)
        if available_keys is None:
            missing.append(f"missing secret: {secret_name}")
            continue
        for key in sorted(required_keys):
            if key not in available_keys:
                missing.append(f"missing key: {secret_name}.{key}")
    return len(missing) == 0, missing


def _get_supported_output_type_literals() -> list[str]:
    """Return the supported primitive output_type literal values."""
    output_type_value = getattr(OutputType, "__value__", OutputType)
    for arg in get_args(output_type_value):
        literal_values = get_args(arg)
        if literal_values and all(isinstance(value, str) for value in literal_values):
            return sorted(cast(list[str], list(literal_values)))
    return []


def _build_output_type_context() -> dict[str, Any]:
    """Return compact authoring guidance for preset output_type."""
    return {
        "supported_literals": _get_supported_output_type_literals(),
        "accepts_json_schema": True,
        "examples": {
            "primitive": "str",
            "structured": {
                "type": "object",
                "properties": {
                    "summary": {"type": "string"},
                    "severity": {"type": "string"},
                },
                "required": ["summary"],
            },
        },
        "notes": [
            "Use a literal string output_type for simple primitive responses.",
            "Use a JSON Schema object when you want structured agent output.",
        ],
    }


async def _build_integrations_inventory(role: Any) -> dict[str, Any]:
    """Build integration inventory for workflow and preset authoring."""
    async with IntegrationService.with_session(role=role) as svc:
        user_id = getattr(role, "user_id", None)
        integrations = await svc.list_integrations()
        if user_id is not None:
            integrations = [
                integration
                for integration in integrations
                if getattr(integration, "user_id", None) == user_id
            ]
        existing = {
            (integration.provider_id, integration.grant_type): integration
            for integration in integrations
        }
        mcp_integrations = await svc.list_mcp_integrations()
        oauth_providers: list[dict[str, Any]] = []
        for provider_impl in all_providers():
            metadata = provider_impl.metadata
            integration = existing.get((provider_impl.id, provider_impl.grant_type))
            oauth_providers.append(
                {
                    "provider_id": provider_impl.id,
                    "name": metadata.name,
                    "description": metadata.description,
                    "grant_type": provider_impl.grant_type.value,
                    "enabled": metadata.enabled,
                    "requires_config": metadata.requires_config,
                    "integration_status": (
                        integration.status.value
                        if integration
                        else IntegrationStatus.NOT_CONFIGURED.value
                    ),
                }
            )

        for custom_provider in await svc.list_custom_providers():
            integration = existing.get(
                (custom_provider.provider_id, custom_provider.grant_type)
            )
            oauth_providers.append(
                {
                    "provider_id": custom_provider.provider_id,
                    "name": custom_provider.name,
                    "description": custom_provider.description
                    or "Custom OAuth provider",
                    "grant_type": custom_provider.grant_type.value,
                    "enabled": True,
                    "requires_config": True,
                    "integration_status": (
                        integration.status.value
                        if integration
                        else IntegrationStatus.NOT_CONFIGURED.value
                    ),
                }
            )

        return {
            "mcp_integrations": [
                {
                    "id": str(integration.id),
                    "name": integration.name,
                    "slug": integration.slug,
                    "description": integration.description,
                    "server_type": integration.server_type,
                    "auth_type": integration.auth_type.value,
                    "oauth_integration_id": (
                        str(integration.oauth_integration_id)
                        if integration.oauth_integration_id
                        else None
                    ),
                    "timeout": integration.timeout,
                    "attachable_to_agent_preset": True,
                }
                for integration in mcp_integrations
            ],
            "oauth_providers": oauth_providers,
            "notes": [
                "Only mcp_integrations can be attached directly to agent presets via mcp_integration_ids.",
                "oauth_providers describe broader workspace integration availability and connection status.",
            ],
        }


async def _resolve_agent_preset_model(
    role: Any,
    *,
    model_name: str | None,
    model_provider: str | None,
) -> tuple[str, str]:
    """Resolve explicit or default model inputs for preset creation."""
    async with AgentManagementService.with_session(role=role) as svc:
        if model_name is not None or model_provider is not None:
            if not model_name or not model_provider:
                raise ToolError(
                    "model_name and model_provider must both be provided when setting an explicit model"
                )
            try:
                model_config = await svc.get_model_config(model_name)
            except TracecatNotFoundError as exc:
                raise ToolError(f"Model '{model_name}' not found") from exc
            if model_config.provider != model_provider:
                raise ToolError(
                    f"Model '{model_name}' belongs to provider '{model_config.provider}', not '{model_provider}'"
                )
        else:
            if not (default_model := await svc.get_default_model()):
                raise ToolError(
                    "No default model configured for this organization. Set one before creating a preset without explicit model fields."
                )
            try:
                model_config = await svc.get_model_config(default_model)
            except TracecatNotFoundError as exc:
                raise ToolError(
                    f"Default model '{default_model}' is configured but no longer exists"
                ) from exc
        if not await svc.check_workspace_provider_credentials(model_config.provider):
            raise ToolError(
                f"Workspace credentials for provider '{model_config.provider}' are not configured"
            )
        return model_config.name, model_config.provider


_WORKFLOW_YAML_TOP_LEVEL_KEYS = frozenset(
    {"definition", "layout", "schedules", "case_trigger"}
)


class MCPLayoutPosition(BaseModel):
    x: float | None = None
    y: float | None = None
    position: dict[str, float] | None = None

    @model_validator(mode="after")
    def apply_nested_position(self) -> MCPLayoutPosition:
        if self.position is not None:
            if self.x is None:
                self.x = self.position.get("x")
            if self.y is None:
                self.y = self.position.get("y")
        return self


class MCPLayoutViewport(BaseModel):
    x: float | None = None
    y: float | None = None
    zoom: float | None = None


class MCPLayoutActionPosition(BaseModel):
    ref: str
    x: float | None = None
    y: float | None = None
    position: dict[str, float] | None = None

    @model_validator(mode="after")
    def apply_nested_position(self) -> MCPLayoutActionPosition:
        if self.position is not None:
            if self.x is None:
                self.x = self.position.get("x")
            if self.y is None:
                self.y = self.position.get("y")
        return self


class MCPWorkflowLayout(BaseModel):
    trigger: MCPLayoutPosition | None = None
    viewport: MCPLayoutViewport | None = None
    actions: list[MCPLayoutActionPosition] = Field(default_factory=list)


class MCPWorkflowSchedule(BaseModel):
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
    def validate_schedule_spec(self) -> MCPWorkflowSchedule:
        if self.cron is None and self.every is None:
            raise ValueError("Either cron or every must be provided for a schedule")
        return self


class MCPWorkflowYamlPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    definition: DSLInput | None = None
    layout: MCPWorkflowLayout | None = None
    schedules: list[MCPWorkflowSchedule] | None = None
    case_trigger: dict[str, Any] | None = None


def _normalize_workflow_yaml_payload(raw_payload: Any) -> dict[str, Any]:
    """Normalize YAML payload shape for MCP workflow update APIs."""
    if raw_payload is None:
        raise ToolError("Workflow definition YAML is empty")
    if not isinstance(raw_payload, dict):
        raise ToolError("Workflow definition YAML must decode to an object")
    if "definition" in raw_payload:
        return raw_payload
    if _WORKFLOW_YAML_TOP_LEVEL_KEYS.intersection(raw_payload.keys()):
        return raw_payload
    return {"definition": raw_payload}


def _parse_workflow_yaml_payload(definition_yaml: str) -> MCPWorkflowYamlPayload:
    """Parse and validate workflow definition YAML payload."""
    try:
        raw = yaml.safe_load(definition_yaml)
    except yaml.YAMLError as exc:
        raise ToolError(f"Invalid YAML: {exc}") from exc
    normalized = _normalize_workflow_yaml_payload(raw)
    return MCPWorkflowYamlPayload.model_validate(normalized)


def _auto_generate_layout(
    actions: list[dict[str, Any]],
) -> dict[str, Any]:
    """Generate a top-down layout for workflow actions when none is provided.

    Walks the dependency graph to assign each action a depth (row), then
    spreads siblings horizontally. The trigger node sits at the top.
    """
    NODE_HEIGHT = 150  # vertical spacing between rows
    NODE_WIDTH = 300  # horizontal spacing between columns

    # Build dependency graph
    dependents: dict[str, list[str]] = {a["ref"]: [] for a in actions}
    deps: dict[str, list[str]] = {}
    for a in actions:
        deps[a["ref"]] = a.get("depends_on", []) or []
        for dep in deps[a["ref"]]:
            if dep in dependents:
                dependents[dep].append(a["ref"])

    # Assign depth via BFS from roots
    depth: dict[str, int] = {}
    roots = [ref for ref, d in deps.items() if not d]
    # If no roots found (cycle?), just use insertion order
    if not roots:
        for i, a in enumerate(actions):
            depth[a["ref"]] = i
    else:
        queue = deque(roots)
        max_depth = max(len(actions) - 1, 0)
        for r in roots:
            depth[r] = 0
        while queue:
            ref = queue.popleft()
            for child in dependents.get(ref, []):
                new_depth = depth[ref] + 1
                if new_depth > max_depth:
                    continue
                if child not in depth or new_depth > depth[child]:
                    depth[child] = new_depth
                    queue.append(child)
    if len(depth) < len(actions):
        next_depth = max(depth.values(), default=-1) + 1
        for action in actions:
            ref = action["ref"]
            if ref not in depth:
                depth[ref] = next_depth
                next_depth += 1

    # Group actions by depth
    rows: dict[int, list[str]] = {}
    for ref, d in depth.items():
        rows.setdefault(d, []).append(ref)

    # Sort refs within each row for deterministic output
    for d in rows:
        rows[d].sort()

    # Position: trigger at top, then each row below
    layout: dict[str, Any] = {
        "trigger": {"x": 0, "y": 0},
        "actions": [],
    }
    for d in sorted(rows.keys()):
        refs_in_row = rows[d]
        total_width = (len(refs_in_row) - 1) * NODE_WIDTH
        start_x = -total_width / 2
        y = (d + 1) * NODE_HEIGHT  # +1 to leave room for trigger
        for i, ref in enumerate(refs_in_row):
            layout["actions"].append(
                {
                    "ref": ref,
                    "x": start_x + i * NODE_WIDTH,
                    "y": y,
                }
            )

    return layout


async def _replace_workflow_definition_from_dsl(
    service: WorkflowsManagementService,
    workflow_id: WorkflowUUID,
    dsl: DSLInput,
    action_positions: dict[str, tuple[float, float]] | None = None,
) -> None:
    """Replace draft workflow definition from DSL (actions + metadata)."""
    workflow = await service.get_workflow(workflow_id)
    if workflow is None:
        raise ToolError(f"Workflow {workflow_id} not found")

    workflow.title = dsl.title
    workflow.description = dsl.description
    workflow.entrypoint = dsl.entrypoint.ref
    entrypoint_data = dsl.entrypoint.model_dump()
    workflow.expects = entrypoint_data.get("expects") or {}
    workflow.returns = dsl.returns
    workflow.config = dsl.config.model_dump(mode="json")
    workflow.error_handler = dsl.error_handler
    service.session.add(workflow)

    await service.session.execute(
        delete(Action).where(
            Action.workspace_id == service.workspace_id,
            Action.workflow_id == workflow.id,
        )
    )
    await service.create_actions_from_dsl(dsl, workflow.id, action_positions)
    await service.session.flush()
    await service.session.refresh(workflow, ["actions"])


def _extract_layout_positions(
    layout_data: dict[str, Any] | None,
) -> tuple[
    tuple[float, float] | None,
    tuple[float, float, float] | None,
    dict[str, tuple[float, float]] | None,
]:
    """Extract layout data into position tuples for workflow/action creation.

    Returns (trigger_position, viewport, action_positions).
    """
    if not layout_data:
        return None, None, None
    layout = MCPWorkflowLayout.model_validate(layout_data)
    trigger_position: tuple[float, float] | None = None
    if layout.trigger is not None:
        trigger_position = (
            layout.trigger.x if layout.trigger.x is not None else 0.0,
            layout.trigger.y if layout.trigger.y is not None else 0.0,
        )
    viewport: tuple[float, float, float] | None = None
    if layout.viewport is not None:
        viewport = (
            layout.viewport.x if layout.viewport.x is not None else 0.0,
            layout.viewport.y if layout.viewport.y is not None else 0.0,
            layout.viewport.zoom if layout.viewport.zoom is not None else 1.0,
        )
    action_positions: dict[str, tuple[float, float]] | None = None
    if layout.actions:
        action_positions = {
            ap.ref: (
                ap.x if ap.x is not None else 0.0,
                ap.y if ap.y is not None else 0.0,
            )
            for ap in layout.actions
        }
    return trigger_position, viewport, action_positions


def _apply_layout_to_workflow(
    *,
    workflow: Any,
    layout: MCPWorkflowLayout,
) -> None:
    """Apply optional trigger/action/viewport layout updates to a workflow."""
    if layout.trigger is not None:
        if layout.trigger.x is not None:
            workflow.trigger_position_x = layout.trigger.x
        if layout.trigger.y is not None:
            workflow.trigger_position_y = layout.trigger.y

    if layout.viewport is not None:
        if layout.viewport.x is not None:
            workflow.viewport_x = layout.viewport.x
        if layout.viewport.y is not None:
            workflow.viewport_y = layout.viewport.y
        if layout.viewport.zoom is not None:
            workflow.viewport_zoom = layout.viewport.zoom

    action_by_ref = {action.ref: action for action in workflow.actions}
    for action_position in layout.actions:
        action = action_by_ref.get(action_position.ref)
        if action is None:
            raise ToolError(
                f"Unknown action ref {action_position.ref!r} in layout.actions"
            )
        if action_position.x is not None:
            action.position_x = action_position.x
        if action_position.y is not None:
            action.position_y = action_position.y


async def _replace_workflow_schedules(
    *,
    service: WorkflowSchedulesService,
    workflow_id: WorkflowUUID,
    schedules: list[MCPWorkflowSchedule],
) -> list[uuid.UUID]:
    """Replace all schedules for a workflow from YAML payload."""
    existing = await service.list_schedules(workflow_id=workflow_id)
    for schedule in existing:
        await service.delete_schedule(schedule.id, commit=False)

    offline_schedule_ids: list[uuid.UUID] = []
    for schedule in schedules:
        created = await service.create_schedule(
            ScheduleCreate(
                workflow_id=workflow_id,
                inputs=schedule.inputs,
                cron=schedule.cron,
                every=schedule.every,
                offset=schedule.offset,
                start_at=schedule.start_at,
                end_at=schedule.end_at,
                status=schedule.status,
                timeout=schedule.timeout,
            ),
            commit=False,
        )
        if schedule.status == "offline":
            offline_schedule_ids.append(created.id)
    return offline_schedule_ids


async def _apply_case_trigger_payload(
    *,
    service: CaseTriggersService,
    workflow_id: WorkflowUUID,
    case_trigger_payload: dict[str, Any],
    update_mode: Literal["replace", "patch"],
) -> None:
    """Apply case-trigger configuration based on update mode."""
    if update_mode == "replace":
        config = CaseTriggerConfig.model_validate(case_trigger_payload)
        await service.upsert_case_trigger(
            workflow_id,
            config,
            create_missing_tags=True,
            commit=False,
        )
        return

    patch = CaseTriggerUpdate.model_validate(case_trigger_payload)
    if not patch.model_dump(exclude_unset=True):
        return
    try:
        await service.update_case_trigger(
            workflow_id,
            patch,
            create_missing_tags=True,
            commit=False,
        )
    except TracecatNotFoundError:
        config = CaseTriggerConfig(
            status=patch.status or "offline",
            event_types=patch.event_types or [],
            tag_filters=patch.tag_filters or [],
        )
        await service.upsert_case_trigger(
            workflow_id,
            config,
            create_missing_tags=True,
            commit=False,
        )


auth = create_mcp_auth()

_CASE_EVENT_TYPE_VALUES = [event_type.value for event_type in CaseEventType]
_CASE_EVENT_TYPE_VALUES_JSON = json.dumps(
    _CASE_EVENT_TYPE_VALUES, separators=(",", ":")
)

# ---------------------------------------------------------------------------
# Server instructions — sent to every MCP client on connection
# ---------------------------------------------------------------------------

_MCP_INSTRUCTIONS = f"""\
Tracecat workflow management server. Use `list_workspaces` to discover available \
workspaces, then pass `workspace_id` to all other tools.

## Action namespaces
- `core.*` — built-in platform actions (core.http_request, core.transform.reshape, \
core.script.run_python, core.table.*, core.open_case, core.send_email, etc.)
- `core.transform.*` — data transforms (reshape, scatter, gather, filter, map)
- `tools.*` — third-party integrations (tools.slack.post_message, etc.)
- `ai.*` — AI/LLM actions:
  - `ai.action` — simple LLM call (no tools), supports `output_type` for structured output
  - `ai.agent` — full AI agent with tool calling via `actions` list
  - `ai.preset_agent` — run a saved agent preset by slug

Use `list_actions` to discover available actions. Use `get_action_context` or \
`get_workflow_authoring_context` to get parameter schemas for any action \
(including platform/interface actions like ai.agent, scatter, gather, etc.). \
Use `get_agent_preset_authoring_context` before creating agent presets to inspect \
available models, integration options, and output_type configuration.

## Expression syntax (used in action `args:` values)
- `${{ TRIGGER.<field> }}` — workflow trigger input
- `${{ ACTIONS.<ref>.result }}` — output from a completed action
- `${{ SECRETS.<name>.<KEY> }}` — secret value
- `${{ VARS.<name>.<key> }}` — workspace variable
- `${{ FN.<func>(<args>) }}` — built-in function call (e.g. FN.length, FN.join, FN.now)
- Operators: `||`, `&&`, `==`, `!=`, `<`, `>`, `<=`, `>=`, `+`, `-`, `*`, `/`
- Ternary: `${{ condition -> true_value : false_value }}`
- Literals: `None` (NOT `null`), `true`, `false`, strings, numbers
- **Important**: There is NO inline `for` comprehension syntax in expressions. \
Use `core.script.run_python` for list transformations, or `for_each` on actions.
- **Important**: Use `None` (Python-style) NOT `null` (JSON-style) in expressions.

## Scatter/Gather pattern (parallel fan-out)
Within a scatter stream, each child action accesses its item via \
`ACTIONS.<scatter_ref>.result`:
```yaml
- ref: my_scatter
  action: core.transform.scatter
  args:
    collection: ${{ ACTIONS.previous_step.result }}
- ref: process_item
  action: core.http_request
  depends_on: [my_scatter]
  args:
    url: "https://api.example.com/${{ ACTIONS.my_scatter.result.id }}"
    method: GET
- ref: my_gather
  action: core.transform.gather
  depends_on: [process_item]
  args:
    items: ${{ ACTIONS.process_item.result }}
```

## Key DSL fields (inside each action under `actions:`)
- `ref` — unique slug identifier for the action
- `action` — action type (e.g. `core.http_request`)
- `args` — action arguments as key-value pairs
- `depends_on` — list of action refs this action waits for
- `run_if` — conditional expression to skip execution
- `for_each` — iterate over a list \
(syntax: `${{ for var.x in ACTIONS.step.result }}`, access item as `${{ var.x }}`)
- `retry_policy` — {{max_attempts, timeout}}
- `join_strategy` — `all` (default) or `any`

## Recommended authoring sequence
1. `get_workflow_authoring_context` — get action schemas, secrets, and variables
2. `create_workflow` or `update_workflow` with `definition_yaml`
3. `validate_workflow` — check for structural and expression errors
4. `publish_workflow` — freeze a versioned snapshot
5. `run_published_workflow` or `run_draft_workflow` — execute it
6. `list_workflow_executions` — see run history, find execution IDs
7. `get_workflow_execution` — inspect execution status, per-action results/errors

## Agent preset authoring
1. `get_agent_preset_authoring_context` — inspect models, provider readiness, integrations, variables, and output_type options
2. `list_integrations` — inspect workspace MCP integrations and broader provider status
3. `list_actions` / `get_action_context` — choose preset tools and inspect arg schemas
4. `create_agent_preset` — create a reusable preset
5. `list_agent_presets` / `run_agent_preset` — inspect and test saved presets

## Debugging workflow runs
After running a workflow, use `list_workflow_executions` to see recent runs and their \
statuses (COMPLETED, FAILED, RUNNING, etc.). Then use `get_workflow_execution` with the \
execution ID to get a detailed event timeline showing each action's status, timing, \
inputs, results, and errors. This is essential for diagnosing failed runs.

## Important: workflow actions vs MCP tools
Action names like `core.open_case` are for use *inside* workflow YAML definitions \
(in the `action:` field of a workflow action step). They are NOT MCP tool names. \
To manage cases directly, use the MCP tools `create_case`, `list_cases`, `get_case`, \
`update_case`, and `delete_case`. Similarly, use `create_workflow` (not \
`core.workflow.execute`) to create workflows via MCP.

## Tag and case field argument rules
- Workflow tag definition tools (`list_workflow_tags`, `create_workflow_tag`, \
`update_workflow_tag`, `delete_workflow_tag`) operate on workspace tag definitions. \
Use `tag_id` from `list_workflow_tags`; refs are also accepted for update/delete.
- Workflow tag association tools (`list_tags_for_workflow`, `add_workflow_tag`, \
`remove_workflow_tag`) use `workflow_id` plus a workflow tag definition `tag_id`.
- Case tag definition tools (`list_case_tags`, `create_case_tag`, \
`update_case_tag`, `delete_case_tag`) operate on workspace case tag definitions.
- Case tag association tools (`list_tags_for_case`, `add_case_tag`, \
`remove_case_tag`) use `tag_identifier`, which can be a case tag UUID, ref, or a \
free-form name that slugifies to an existing tag. If no tag exists yet, create it \
first with `create_case_tag`.
- Case field tools use `field_id` from `list_case_fields`. This field id is the \
field name/column id, not a UUID.
- Case field `type` must be an uppercase SqlType value: `TEXT`, `INTEGER`, \
`NUMERIC`, `DATE`, `BOOLEAN`, `TIMESTAMP`, `TIMESTAMPTZ`, `JSONB`, `UUID`, \
`SELECT`, or `MULTI_SELECT`.
- Case field `options` must be a JSON array string such as `["low","medium","high"]`. \
`options` are required for `SELECT` and `MULTI_SELECT`, and invalid for other types.

## Structured argument schema quick reference
- `update_webhook.status`: `"online"` or `"offline"`.
- `update_webhook.methods`: JSON array of uppercase HTTP method strings, e.g. \
`["GET","POST"]`.
- `update_webhook.allowlisted_cidrs`: JSON array of CIDR strings, e.g. \
`["10.0.0.0/8","192.168.1.0/24"]`.
- `update_case_trigger.status`: `"online"` or `"offline"`.
- `update_case_trigger.event_types`: JSON array of case event strings. Valid values: \
`{_CASE_EVENT_TYPE_VALUES_JSON}`.
- `update_case_trigger.tag_filters`: JSON array of tag ref strings, e.g. \
`["malware","phishing"]`.
- `create_table.columns_json`: JSON array of objects with schema \
`{{"name": str, "type": SqlType, "nullable": bool?, "default": any?, "options": list[str]?}}`. \
`options` are only valid for `SELECT` and `MULTI_SELECT`.
- `create_case_field.options` and `update_case_field.options`: JSON array of strings, \
e.g. `["low","medium","high"]`; use `[]` to clear options on update.
- Tag `color` values should be hex strings such as `"#ff0000"` when provided.

Read the `tracecat://platform/dsl-reference` resource for the full DSL specification.
"""

mcp = FastMCP(
    "tracecat-workflows",
    auth=auth,
    instructions=_MCP_INSTRUCTIONS,
)

# ---------------------------------------------------------------------------
# Middleware pipeline (registration order = execution order)
# ---------------------------------------------------------------------------

mcp.add_middleware(
    RateLimitingMiddleware(
        max_requests_per_second=TRACECAT_MCP__RATE_LIMIT_RPS,
        burst_capacity=TRACECAT_MCP__RATE_LIMIT_BURST,
        get_client_id=get_mcp_client_id,
    )
)
mcp.add_middleware(MCPInputSizeLimitMiddleware())
mcp.add_middleware(WatchtowerMonitorMiddleware())
mcp.add_middleware(MCPTimeoutMiddleware())
mcp.add_middleware(
    ErrorHandlingMiddleware(include_traceback=False, transform_errors=True)
)
mcp.add_middleware(
    LoggingMiddleware(methods=["tools/call"], include_payload_length=True)
)


# ---------------------------------------------------------------------------
# MCP Resources — structured reference material for LLM clients
# ---------------------------------------------------------------------------

_DSL_REFERENCE_TEXT = """\
# Tracecat Workflow DSL Reference

## Workflow YAML Structure

A workflow definition YAML has four optional top-level sections:

```yaml
definition:
  title: "My Workflow"
  description: "What this workflow does"
  entrypoint:
    expects:           # Trigger input schema (optional)
      alert_id:
        type: str
        description: "The alert ID to investigate"
      severity:
        type: str
        default: "medium"
    ref: first_action   # Which action to start with (optional, defaults to first action)
  actions:
    - ref: first_action
      action: core.http_request
      args:
        url: "https://api.example.com/alerts/${{ TRIGGER.alert_id }}"
        method: GET
    - ref: notify
      action: tools.slack.post_message
      depends_on:
        - first_action
      args:
        channel: "#alerts"
        text: "Alert ${{ TRIGGER.alert_id }}: ${{ ACTIONS.first_action.result }}"

layout:                # Optional UI positioning
  trigger:
    x: 0
    y: 0
  actions:
    - ref: first_action
      x: 0
      y: 150
    - ref: notify
      x: 0
      y: 300

schedules:             # Optional recurring schedules
  - cron: "0 9 * * 1-5"
    status: online
  - every: PT1H
    status: offline

case_trigger:          # Optional case-event trigger
  status: offline
  event_types: ["case_created", "case_updated"]
  tag_filters: ["malware", "phishing"]
```

## Action Statement Fields

Each action in the `actions` list supports:

| Field | Type | Description |
|-------|------|-------------|
| `ref` | str (required) | Unique slug identifier (lowercase, hyphens, underscores) |
| `action` | str (required) | Fully qualified action type (e.g. `core.http_request`) |
| `args` | mapping | Action arguments — use expressions for dynamic values |
| `depends_on` | list[str] | Refs of actions that must complete before this one runs |
| `run_if` | expression | Conditional — skip this action if the expression is falsy |
| `for_each` | expression or list | Iterate: run this action once per item in the list |
| `retry_policy` | object | `{max_attempts: int, timeout: float}` |
| `join_strategy` | str | `"all"` (default) or `"any"` — how to join parallel branches |
| `start_delay` | float | Seconds to wait before starting |
| `environment` | str | Override secrets environment for this action |

## Expression Syntax

Expressions are wrapped in `${{ }}` and can reference:

### Context Variables
- `TRIGGER.<field>` — workflow trigger inputs (defined in `entrypoint.expects`)
- `ACTIONS.<ref>.result` — output from a completed action
- `SECRETS.<secret_name>.<KEY>` — secret value from the workspace/org
- `VARS.<variable_name>.<key>` — workspace variable value
- `ENV.<name>` — environment variable
- `LOCAL.<key>` — current for_each iteration item (only inside `for_each` actions)

### Operators
- Logical: `||` (or), `&&` (and)
- Comparison: `==`, `!=`, `<`, `>`, `<=`, `>=`
- Arithmetic: `+`, `-`, `*`, `/`
- Ternary: `${{ condition -> true_value : false_value }}`
- Member access: `obj.field` or `obj["field"]`
- Indexing: `list[0]`, `list[-1]`

### Built-in Functions (FN.)

**String**: capitalize, concat, endswith, format, join, lowercase, prefix, replace, \
slice, slugify, split, startswith, strip, suffix, titleize, uppercase, url_encode, \
url_decode

**Comparison**: greater_than, greater_than_or_equal, is_equal, is_null, \
less_than, less_than_or_equal, is_not_equal, is_not_null, not_equal, not_null

**Regex**: regex_extract, regex_match, regex_not_match

**Array/Collection**: compact, contains, does_not_contain, contains_any_of, \
contains_none_of, difference, flatten, intersection, is_empty, is_in, length, \
not_empty, is_not_empty, not_in, is_not_in, symmetric_difference, union, unique, \
zip_map, at

**Math**: add, sub, mul, div, mod, pow, sum, min, max

**Iteration**: zip, iter_product, range

**JSON/Dict**: lookup, map_keys, merge, to_keys, to_values, tabulate, \
is_json, deserialize_json, deserialize_ndjson, deserialize_yaml, prettify_json, \
serialize_json, serialize_yaml

**Formatting**: to_markdown_list, to_markdown_table, to_markdown_tasks

**Logical**: and, or, not

**Time/Date**: datetime, days_between, days, format_datetime, from_timestamp, \
get_day_of_week, get_day, get_hour, get_minute, get_month, get_second, get_year, \
hours_between, hours, is_working_hours, minutes_between, minutes, now, \
parse_datetime, parse_time, seconds_between, seconds, set_timezone, to_datetime, \
to_isoformat, to_time, to_timestamp, today, unset_timezone, utcnow, wall_clock, \
weeks_between, weeks, windows_filetime

**Encoding**: to_base64, from_base64, to_base64url, from_base64url

**Hash**: hash_md5, hash_sha1, hash_sha256, hash_sha512

**IP Address**: ipv4_in_subnet, ipv6_in_subnet, ipv4_is_public, ipv6_is_public, \
check_ip_version

**IOC Extraction**: extract_asns, extract_cves, extract_domains, extract_emails, \
extract_md5, extract_sha1, extract_sha256, extract_sha512, extract_ip, \
extract_ipv4, extract_ipv6, extract_mac, extract_urls, normalize_email

**Generators**: uuid4

**IO**: parse_csv

## Core Built-in Actions

| Action | Description |
|--------|-------------|
| `core.http_request` | Make an HTTP request (GET, POST, PUT, DELETE, PATCH) |
| `core.transform.reshape` | Reshape data using expressions |
| `core.transform.scatter` | Fan-out: scatter a collection into parallel streams |
| `core.transform.gather` | Fan-in: gather results from parallel streams into a list |
| `core.transform.filter` | Filter a collection using a Python lambda |
| `core.transform.map` | Map over items |
| `core.script.run_python` | Run inline Python script in a sandbox |
| `core.open_case` | Open a new case |
| `core.send_email` | Send an email via SMTP |
| `core.table.insert_row` | Insert a row into a table |
| `core.table.lookup` | Lookup a value in a table |
| `core.workflow.execute` | Execute a child workflow |
| `ai.action` | Call an LLM (no tools), supports structured output via `output_type` |
| `ai.agent` | AI agent with tool calling (can invoke Tracecat actions) |
| `ai.preset_agent` | Run a saved agent preset by slug |

## Common Workflow Patterns

### HTTP Request → Notify
```yaml
actions:
  - ref: fetch_data
    action: core.http_request
    args:
      url: "https://api.example.com/data"
      method: GET
      headers:
        Authorization: "Bearer ${{ SECRETS.api_creds.API_TOKEN }}"
  - ref: notify_slack
    action: tools.slack.post_message
    depends_on: [fetch_data]
    args:
      channel: "#alerts"
      text: "Got data: ${{ ACTIONS.fetch_data.result }}"
```

### Scatter/Gather (Parallel Fan-out/Fan-in)
```yaml
actions:
  - ref: get_items
    action: core.transform.reshape
    args:
      value: ${{ TRIGGER.items }}
  - ref: scatter_items
    action: core.transform.scatter
    depends_on: [get_items]
    args:
      collection: ${{ ACTIONS.get_items.result }}
  - ref: process_item
    action: core.http_request
    depends_on: [scatter_items]
    args:
      url: "https://api.example.com/process/${{ ACTIONS.scatter_items.result.id }}"
      method: POST
  - ref: gather_results
    action: core.transform.gather
    depends_on: [process_item]
    args:
      items: ${{ ACTIONS.process_item.result }}
```
**Key**: Inside a scatter stream, `ACTIONS.<scatter_ref>.result` gives each item. \
Multiple actions can chain within the stream before the gather collects results.

### AI Action (Simple LLM Call)
```yaml
actions:
  - ref: analyze
    action: ai.action
    args:
      user_prompt: "Analyze this data: ${{ ACTIONS.fetch.result }}"
      model_name: claude-sonnet-4-20250514
      model_provider: anthropic
      instructions: "You are a security analyst. Be concise."
      output_type: 'list[{"finding": "str", "severity": "str"}]'
```

### AI Agent (With Tool Calling)
```yaml
actions:
  - ref: investigate
    action: ai.agent
    args:
      user_prompt: "Investigate this alert and create a case if needed."
      model_name: claude-sonnet-4-20250514
      model_provider: anthropic
      actions:
        - core.open_case
        - tools.slack.post_message
      instructions: "You are a SOC analyst. Be thorough."
      max_tool_calls: 10
```

### For-each Loop
```yaml
actions:
  - ref: process_items
    action: core.http_request
    for_each: "${{ for var.x in TRIGGER.items }}"
    args:
      url: "https://api.example.com/process/${{ var.x }}"
      method: POST
```

### Conditional Execution
```yaml
actions:
  - ref: escalate
    action: tools.slack.post_message
    run_if: "${{ TRIGGER.severity == 'critical' }}"
    args:
      channel: "#critical-alerts"
      text: "CRITICAL: ${{ TRIGGER.alert_title }}"
```

### Python Script for Complex Logic
```yaml
actions:
  - ref: transform
    action: core.script.run_python
    args:
      inputs:
        raw_data: "${{ ACTIONS.fetch.result }}"
      script: |
        def main(raw_data):
            # Process and return transformed data
            return [item for item in raw_data if item["status"] == "active"]
```

### HTTP Request with JSON Payload
```yaml
actions:
  - ref: create_ticket
    action: core.http_request
    args:
      url: "https://api.example.com/tickets"
      method: POST
      headers:
        Authorization: "Bearer ${{ SECRETS.api.TOKEN }}"
        Content-Type: "application/json"
      payload:
        title: "${{ TRIGGER.title }}"
        priority: "${{ TRIGGER.severity }}"
```
"""


@mcp.resource(
    "tracecat://platform/dsl-reference",
    name="DSL Reference",
    description="Complete Tracecat workflow DSL specification including YAML structure, expression syntax, built-in functions, and common patterns.",
    mime_type="text/plain",
)
def get_dsl_reference() -> str:
    """Return the full Tracecat DSL reference documentation."""
    return _DSL_REFERENCE_TEXT


_DOMAIN_REFERENCE_TEXT = """\
# Tracecat Domain Reference

Valid enum values for cases, tables, workflows, and triggers.

## Case Management

### Priority
unknown, low, medium, high, critical, other

### Severity
unknown, informational, low, medium, high, critical, fatal, other

### Status
unknown, new, in_progress, on_hold, resolved, closed, other

### Task Status
todo, in_progress, completed, blocked

### Case Event Types (for case triggers)
case_created, case_updated, case_closed, case_reopened, case_viewed, \
priority_changed, severity_changed, status_changed, fields_changed, \
assignee_changed, attachment_created, attachment_deleted, tag_added, \
tag_removed, payload_changed, task_created, task_deleted, \
task_status_changed, task_priority_changed, task_workflow_changed, \
task_assignee_changed, dropdown_value_changed

## Table Column Types
TEXT, INTEGER, NUMERIC, DATE, BOOLEAN, TIMESTAMP, TIMESTAMPTZ, JSONB, UUID, SELECT, MULTI_SELECT

## Workflow Control Flow

### Join Strategy
all, any

### Loop Strategy (for_each)
parallel, batch, sequential

### Fail Strategy
isolated, all

### Edge Type
success, error

## Workflow Execution

### Trigger Type
manual, scheduled, webhook, case

### Execution Type
draft, published
"""


@mcp.resource(
    "tracecat://platform/domain-reference",
    name="Domain Reference",
    description="Valid enum values for cases, tables, workflows, and triggers.",
    mime_type="text/plain",
)
def get_domain_reference() -> str:
    """Return valid domain enum values for cases, tables, workflows, and triggers."""
    return _DOMAIN_REFERENCE_TEXT


async def _build_action_catalog(workspace_id: str) -> str:
    """Build the action catalog JSON for a workspace."""

    _, role = await _resolve_workspace_role(workspace_id)
    workspace_inventory = await _load_secret_inventory(role)

    async with RegistryActionsService.with_session(role=role) as svc:
        entries = await svc.list_actions_from_index()

        # Group by top-level namespace (e.g. "core", "tools.slack", "ai")
        namespaces: dict[str, dict[str, Any]] = {}
        for entry, _ in entries:
            action_name = f"{entry.namespace}.{entry.name}"
            # Use second-level namespace for tools.* (e.g. "tools.slack"),
            # first-level for everything else (e.g. "core")
            parts = entry.namespace.split(".")
            if parts[0] == "tools" and len(parts) >= 2:
                ns_key = f"{parts[0]}.{parts[1]}"
            else:
                ns_key = parts[0]

            if ns_key not in namespaces:
                namespaces[ns_key] = {"actions": [], "action_count": 0}

            namespaces[ns_key]["actions"].append(
                {"name": action_name, "description": entry.description or ""}
            )
            namespaces[ns_key]["action_count"] += 1

        # Evaluate secret configuration per namespace
        for ns_data in namespaces.values():
            ns_missing: list[str] = []
            ns_configured = True
            for action_info in ns_data["actions"]:
                indexed = await svc.get_action_from_index(action_info["name"])
                if indexed is None:
                    continue
                secrets = svc.aggregate_secrets_from_manifest(
                    indexed.manifest, action_info["name"]
                )
                if secrets:
                    requirements = _secrets_to_requirements(secrets)
                    configured, missing = _evaluate_configuration(
                        requirements, workspace_inventory
                    )
                    if not configured:
                        ns_configured = False
                        ns_missing.extend(missing)
            ns_data["configured"] = ns_configured
            if ns_missing:
                ns_data["missing_secrets"] = sorted(set(ns_missing))

    total_actions = sum(ns["action_count"] for ns in namespaces.values())
    return _json(
        {
            "workspace_id": workspace_id,
            "total_actions": total_actions,
            "namespaces": namespaces,
        }
    )


@mcp.resource(
    "tracecat://workspaces/{workspace_id}/action-catalog",
    name="Action Catalog",
    description="Complete browsable inventory of all available actions in a workspace, grouped by namespace with descriptions and secret configuration status.",
    mime_type="application/json",
)
async def get_action_catalog(workspace_id: str) -> str:
    """Return all available actions grouped by namespace with configuration status."""
    return await _build_action_catalog(workspace_id)


def _json(obj: Any) -> str:
    """Serialize to JSON string."""
    return json.dumps(obj, default=str)


def _workflow_tag_payload(tag: Any) -> dict[str, Any]:
    """Serialize a workflow tag definition."""
    return TagRead.model_validate(tag, from_attributes=True).model_dump(mode="json")


def _case_tag_payload(tag: Any) -> dict[str, Any]:
    """Serialize a case tag definition."""
    return CaseTagRead.model_validate(tag, from_attributes=True).model_dump(mode="json")


def _case_field_payload(
    column: sa.engine.interfaces.ReflectedColumn,
    *,
    field_schema: dict[str, Any],
) -> dict[str, Any]:
    """Serialize a case field definition."""
    return CaseFieldReadMinimal.from_sa(
        column,
        field_schema=field_schema,
    ).model_dump(mode="json")


def _format_temporal_status(status: Any) -> str | None:
    """Return a stable workflow status string for MCP responses."""
    if status is None:
        return None
    if isinstance(status, WorkflowExecutionStatus):
        return status.name
    if isinstance(status, int):
        try:
            return WorkflowExecutionStatus(status).name
        except ValueError:
            return str(status)
    if hasattr(status, "name"):
        return str(status.name)
    return str(status)


def _parse_sql_type_arg(raw_value: str, field_name: str = "type") -> SqlType:
    """Parse an uppercase SqlType string argument."""
    try:
        return SqlType(raw_value)
    except ValueError as exc:
        valid_values = ", ".join(sql_type.value for sql_type in SqlType)
        raise ToolError(
            f"Invalid {field_name}: {raw_value!r}. Expected one of: {valid_values}"
        ) from exc


def _build_tag_update_params(
    *,
    name: str | None = None,
    color: str | None = None,
) -> TagUpdate:
    """Build a partial tag update payload from provided arguments only."""
    update_kwargs: dict[str, Any] = {}
    if name is not None:
        update_kwargs["name"] = name
    if color is not None:
        update_kwargs["color"] = color
    return TagUpdate(**update_kwargs)


def _build_case_field_update_params(
    *,
    name: str | None = None,
    type: SqlType | None = None,
    options: list[str] | None = None,
    options_provided: bool = False,
) -> CaseFieldUpdate:
    """Build a partial case-field update payload from provided arguments only."""
    update_kwargs: dict[str, Any] = {}
    if name is not None:
        update_kwargs["name"] = name
    if type is not None:
        update_kwargs["type"] = type
    if options_provided:
        update_kwargs["options"] = options
    return CaseFieldUpdate(**update_kwargs)


def _normalize_exception_value(value: Any) -> Any:
    """Normalize non-JSON-safe values from temporal exceptions."""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, (list, tuple, set)):
        return [_normalize_exception_value(v) for v in value]
    if isinstance(value, dict):
        return {
            str(k): _normalize_exception_value(v)
            for k, v in value.items()
            if k is not None
        }
    if isinstance(value, BaseException):
        return {
            "type": value.__class__.__name__,
            "message": str(value),
        }
    return (
        str(value)
        if not isinstance(value, (str, int, float, bool, type(None)))
        else value
    )


def _serialize_temporal_exception(error: BaseException) -> dict[str, Any]:
    """Serialize a temporal exception recursively for MCP output."""
    payload: dict[str, Any] = {
        "type": error.__class__.__name__,
        "message": str(error),
    }

    error_type = getattr(error, "type", None)
    error_details = getattr(error, "details", None)
    error_stack_trace = getattr(error, "stack_trace", None)
    error_failure = getattr(error, "failure", None)

    if error_type is not None:
        payload["failure_type"] = str(error_type)
    if error_details is not None:
        payload["details"] = _normalize_exception_value(error_details)
    if error_stack_trace is not None:
        payload["stack_trace"] = _normalize_exception_value(error_stack_trace)
    if error_failure is not None:
        try:
            payload["failure"] = MessageToDict(error_failure)
        except Exception:
            payload["failure"] = _normalize_exception_value(error_failure)

    extra_attrs = {
        "non_retryable",
        "next_retry_delay",
        "category",
        "scheduled_event_id",
        "started_event_id",
        "identity",
        "activity_type",
        "activity_id",
        "retry_state",
        "workflow_id",
        "workflow_run_id",
        "first_execution_run_id",
    }
    for attr in extra_attrs:
        value = getattr(error, attr, None)
        if value is not None:
            payload[attr] = _normalize_exception_value(value)

    # Include nested error causality for richer diagnostics.
    nested_cause = getattr(error, "__cause__", None)
    if nested_cause is not None and isinstance(nested_cause, BaseException):
        payload["cause"] = _serialize_temporal_exception(nested_cause)

    return payload


# ---------------------------------------------------------------------------
# Discovery tools
# ---------------------------------------------------------------------------


@mcp.tool()
async def list_workspaces() -> str:
    """List all workspaces accessible to the authenticated user.

    Returns a JSON array of workspace objects with id, name, and role.
    """
    try:
        workspaces = await list_workspaces_for_request()
        return _json(workspaces)
    except ValueError as e:
        raise ToolError(str(e)) from e
    except Exception as e:
        logger.error("Failed to list workspaces", error=str(e))
        raise ToolError(f"Failed to list workspaces: {e}") from None


# ---------------------------------------------------------------------------
# Workflow CRUD tools
# ---------------------------------------------------------------------------


@mcp.tool()
async def create_workflow(
    workspace_id: str,
    title: str,
    description: str = "",
    definition_yaml: str | None = None,
) -> str | TextContent:
    """Create a new workflow in a workspace.

    If definition_yaml is provided, creates a fully-defined workflow from YAML.
    Otherwise creates a blank workflow with just a title and description.

    Args:
        workspace_id: The workspace ID (from list_workspaces).
        title: Workflow title (3-100 characters).
        description: Optional workflow description (up to 1000 characters).
        definition_yaml: Optional YAML string defining the full workflow (actions,
            triggers, entrypoint). When provided, title/description in the YAML
            take precedence. The YAML must follow the ExternalWorkflowDefinition
            format with a top-level 'definition' key containing title, description,
            entrypoint, actions, and optionally triggers.

    Returns JSON with the new workflow's id, title, description, and status.
    """

    try:
        _, role = await _resolve_workspace_role(workspace_id)

        if definition_yaml:
            # Parse YAML and create workflow from external definition
            try:
                external_defn_data = yaml.safe_load(definition_yaml)
            except yaml.YAMLError as e:
                raise ToolError(f"Invalid YAML: {e}") from e

            # If YAML has no top-level 'definition' key, wrap it
            if "definition" not in external_defn_data:
                external_defn_data = {"definition": external_defn_data}

            # Apply title/description overrides if not in the YAML
            defn = external_defn_data.get("definition", {})
            if "title" not in defn:
                defn["title"] = title
            if "description" not in defn and description:
                defn["description"] = description

            # Auto-generate layout if not provided or empty
            layout_data = external_defn_data.get("layout")
            if not layout_data:
                actions = defn.get("actions", [])
                if actions:
                    layout_data = _auto_generate_layout(actions)
                    external_defn_data["layout"] = layout_data

            # Extract layout into position params for atomic creation
            trigger_position, viewport, action_positions = _extract_layout_positions(
                layout_data
            )

            async with WorkflowsManagementService.with_session(role=role) as svc:
                workflow = await svc.create_workflow_from_external_definition(
                    external_defn_data,
                    trigger_position=trigger_position,
                    viewport=viewport,
                    action_positions=action_positions,
                )

                return TextContent(
                    type="text",
                    text=_json(
                        {
                            "id": str(workflow.id),
                            "title": workflow.title,
                            "description": workflow.description,
                            "status": workflow.status,
                        }
                    ),
                    annotations=Annotations.model_validate(
                        {
                            "audience": ["user", "assistant"],
                            "priority": 0.7,
                            "layout_applied": trigger_position is not None
                            or bool(action_positions),
                        }
                    ),
                )
        else:
            async with WorkflowsManagementService.with_session(role=role) as svc:
                workflow = await svc.create_workflow(
                    WorkflowCreate(title=title, description=description or None)
                )
                return _json(
                    {
                        "id": str(workflow.id),
                        "title": workflow.title,
                        "description": workflow.description,
                        "status": workflow.status,
                    }
                )
    except ToolError:
        raise
    except ValueError as e:
        raise ToolError(str(e)) from e
    except Exception as e:
        logger.error("Failed to create workflow", error=str(e))
        raise ToolError(f"Failed to create workflow: {e}") from None


@mcp.tool()
async def get_workflow(
    workspace_id: str,
    workflow_id: str,
) -> str:
    """Get details of a specific workflow including its full YAML definition.

    Args:
        workspace_id: The workspace ID.
        workflow_id: The workflow ID (short or full format).

    Returns JSON with workflow metadata (id, title, description, status, version)
    and a 'definition_yaml' field containing the full workflow definition in YAML
    format (definition, layout, schedules, and case_trigger). The YAML can be
    modified and used with update_workflow's definition_yaml parameter.
    """

    try:
        _, role = await _resolve_workspace_role(workspace_id)
        wf_id = WorkflowUUID.new(workflow_id)

        async with WorkflowsManagementService.with_session(role=role) as svc:
            workflow = await svc.get_workflow(wf_id)
            if not workflow:
                raise ToolError(f"Workflow {workflow_id} not found")

            payload: dict[str, Any] = {
                "layout": {
                    "trigger": {
                        "x": workflow.trigger_position_x,
                        "y": workflow.trigger_position_y,
                    },
                    "viewport": {
                        "x": workflow.viewport_x,
                        "y": workflow.viewport_y,
                        "zoom": workflow.viewport_zoom,
                    },
                    "actions": [
                        {
                            "ref": action.ref,
                            "x": action.position_x,
                            "y": action.position_y,
                        }
                        for action in sorted(
                            workflow.actions, key=lambda action: action.ref
                        )
                    ],
                },
                "schedules": [
                    schedule.model_dump(
                        mode="json",
                        exclude={
                            "id",
                            "workspace_id",
                            "workflow_id",
                            "created_at",
                            "updated_at",
                        },
                    )
                    for schedule in ScheduleRead.list_adapter().validate_python(
                        workflow.schedules
                    )
                ],
            }

            try:
                case_trigger = await CaseTriggersService(
                    svc.session, role=role
                ).get_case_trigger(wf_id)
                payload["case_trigger"] = (
                    {
                        "status": case_trigger.status,
                        "event_types": case_trigger.event_types,
                        "tag_filters": case_trigger.tag_filters,
                    }
                    if is_case_trigger_configured(
                        status=case_trigger.status,
                        event_types=case_trigger.event_types,
                        tag_filters=case_trigger.tag_filters,
                    )
                    else None
                )
            except TracecatNotFoundError:
                payload["case_trigger"] = None
            except Exception as e:
                logger.warning(
                    "Could not load case trigger for workflow",
                    workflow_id=workflow_id,
                    error=str(e),
                )
                payload["case_trigger"] = None

            try:
                dsl = await svc.build_dsl_from_workflow(workflow)
                payload["definition"] = dsl.model_dump(mode="json", exclude_none=True)
            except Exception as e:
                logger.warning(
                    "Could not build DSL for workflow",
                    workflow_id=workflow_id,
                    error=str(e),
                )
                payload["definition_error"] = (
                    "Failed to build workflow definition. Check server logs for details."
                )

            definition_yaml = yaml.dump(
                payload,
                indent=2,
                sort_keys=False,
            )

            return _json(
                {
                    "id": str(workflow.id),
                    "title": workflow.title,
                    "description": workflow.description,
                    "status": workflow.status,
                    "version": workflow.version,
                    "alias": workflow.alias,
                    "entrypoint": workflow.entrypoint,
                    "definition_yaml": definition_yaml,
                }
            )
    except ToolError:
        raise
    except ValueError as e:
        raise ToolError(str(e)) from e
    except Exception as e:
        logger.error("Failed to get workflow", error=str(e))
        raise ToolError(f"Failed to get workflow: {e}") from None


@mcp.tool()
async def update_workflow(
    workspace_id: str,
    workflow_id: str,
    title: str | None = None,
    description: str | None = None,
    status: str | None = None,
    alias: str | None = None,
    error_handler: str | None = None,
    definition_yaml: str | None = None,
    update_mode: Literal["replace", "patch"] = "patch",
) -> str:
    """Update a workflow's properties.

    Args:
        workspace_id: The workspace ID.
        workflow_id: The workflow ID.
        title: New title (3-100 characters, optional).
        description: New description (optional).
        status: New status - "online" or "offline" (optional).
        alias: New alias for the workflow (optional).
        error_handler: Error handler workflow alias (optional).
        definition_yaml: Optional workflow YAML payload. Supports:
            - definition (DSL)
            - layout (trigger/action/viewport positions)
            - schedules
            - case_trigger
        update_mode: "patch" to apply provided sections only, or "replace" to
            replace provided state sections with YAML values.

    Returns a confirmation message.
    """

    try:
        _, role = await _resolve_workspace_role(workspace_id)
        wf_id = WorkflowUUID.new(workflow_id)

        update_kwargs: dict[str, Any] = {}
        if title is not None:
            update_kwargs["title"] = title
        if description is not None:
            update_kwargs["description"] = description
        if status is not None:
            update_kwargs["status"] = status
        if alias is not None:
            update_kwargs["alias"] = alias
        if error_handler is not None:
            update_kwargs["error_handler"] = error_handler
        update_params = WorkflowUpdate(**update_kwargs)

        async with WorkflowsManagementService.with_session(role=role) as svc:
            workflow = await svc.get_workflow(wf_id)
            if workflow is None:
                raise ToolError(f"Workflow {workflow_id} not found")

            yaml_payload = (
                _parse_workflow_yaml_payload(definition_yaml)
                if definition_yaml is not None
                else None
            )

            # Auto-generate layout when definition is provided but layout is missing/empty
            if (
                yaml_payload is not None
                and yaml_payload.definition is not None
                and (yaml_payload.layout is None or not yaml_payload.layout.actions)
            ):
                raw = yaml.safe_load(definition_yaml) if definition_yaml else {}
                defn_raw = raw.get("definition", raw) if isinstance(raw, dict) else {}
                actions_raw = defn_raw.get("actions", [])
                if actions_raw:
                    auto_layout = _auto_generate_layout(actions_raw)
                    yaml_payload.layout = MCPWorkflowLayout.model_validate(auto_layout)

            # Extract action positions from layout for use during action creation
            _update_action_positions: dict[str, tuple[float, float]] | None = None
            if yaml_payload is not None and yaml_payload.layout is not None:
                _, _, _update_action_positions = _extract_layout_positions(
                    yaml_payload.layout.model_dump()
                )

            if yaml_payload is not None and yaml_payload.definition is not None:
                await _replace_workflow_definition_from_dsl(
                    service=svc,
                    workflow_id=wf_id,
                    dsl=yaml_payload.definition,
                    action_positions=_update_action_positions,
                )
                await svc.session.refresh(workflow, ["actions"])

            if yaml_payload is not None and yaml_payload.layout is not None:
                await svc.session.refresh(workflow, ["actions"])
                _apply_layout_to_workflow(workflow=workflow, layout=yaml_payload.layout)
                for action in workflow.actions:
                    svc.session.add(action)

            offline_schedule_ids: list[uuid.UUID] = []
            if yaml_payload is not None and yaml_payload.schedules is not None:
                schedule_service = WorkflowSchedulesService(svc.session, role=role)
                offline_schedule_ids = await _replace_workflow_schedules(
                    service=schedule_service,
                    workflow_id=wf_id,
                    schedules=yaml_payload.schedules,
                )

            if yaml_payload is not None and yaml_payload.case_trigger is not None:
                case_trigger_service = CaseTriggersService(svc.session, role=role)
                await _apply_case_trigger_payload(
                    service=case_trigger_service,
                    workflow_id=wf_id,
                    case_trigger_payload=yaml_payload.case_trigger,
                    update_mode=update_mode,
                )

            if update_kwargs:
                for key, value in update_params.model_dump(exclude_unset=True).items():
                    setattr(workflow, key, value)
            svc.session.add(workflow)
            await svc.session.commit()
            await svc.session.refresh(workflow)

            if offline_schedule_ids:
                schedule_service = WorkflowSchedulesService(svc.session, role=role)
                for schedule_id in offline_schedule_ids:
                    await schedule_service.update_schedule(
                        schedule_id,
                        ScheduleUpdate(status="offline"),
                    )

            return _json(
                {
                    "message": f"Workflow {workflow_id} updated successfully",
                    "mode": update_mode,
                }
            )
    except ValueError as e:
        raise ToolError(str(e)) from e
    except Exception as e:
        logger.error("Failed to update workflow", error=str(e))
        raise ToolError(f"Failed to update workflow: {e}") from None


@mcp.tool()
async def list_workflows(
    workspace_id: str,
    status: str | None = None,
    limit: int = 50,
    search: str | None = None,
) -> str:
    """List workflows in a workspace."""

    try:
        _, role = await _resolve_workspace_role(workspace_id)
        limit = max(1, min(limit, 200))
        async with WorkflowsManagementService.with_session(role=role) as svc:
            page = await svc.list_workflows(CursorPaginationParams(limit=limit))
            workflows: list[dict[str, Any]] = []
            for workflow, latest_defn, _trigger_summary in page.items:
                if status and workflow.status != status:
                    continue
                if search:
                    needle = search.lower()
                    if (
                        needle not in workflow.title.lower()
                        and needle not in (workflow.description or "").lower()
                    ):
                        continue
                workflows.append(
                    {
                        "id": str(workflow.id),
                        "title": workflow.title,
                        "description": workflow.description,
                        "status": workflow.status,
                        "version": workflow.version,
                        "alias": workflow.alias,
                        "latest_definition_version": (
                            latest_defn.version if latest_defn else None
                        ),
                    }
                )
                if len(workflows) >= limit:
                    break
            return _json(workflows)
    except ValueError as e:
        raise ToolError(str(e)) from e
    except Exception as e:
        logger.error("Failed to list workflows", error=str(e))
        raise ToolError(f"Failed to list workflows: {e}") from None


@mcp.tool()
async def list_workflow_tree(
    workspace_id: str,
    path: str = "/",
    depth: int = 1,
    include_workflows: bool = True,
) -> str:
    """List workflow folders and workflows under a path.

    Args:
        workspace_id: The workspace ID.
        path: Folder path to list from. Use "/" for the workspace root.
        depth: Number of folder levels to traverse. Use 1 for direct children
            only. Use 0 for unlimited depth.
        include_workflows: Whether to include workflows alongside folders.

    Returns JSON with the normalized root path and a flat list of items.
    """

    try:
        if depth < 0:
            raise ToolError("depth must be >= 0")

        _, role = await _resolve_workspace_role(workspace_id)
        root_path = _normalize_folder_path_arg(path)

        async with WorkflowFolderService.with_session(role=role) as svc:
            queue: deque[tuple[str, int]] = deque([(root_path, 1)])
            items: list[dict[str, Any]] = []

            while queue:
                current_path, current_depth = queue.popleft()
                for item in await svc.get_directory_items(
                    current_path, order_by="desc"
                ):
                    payload = item.model_dump(mode="json")
                    if payload["type"] == "folder":
                        items.append(
                            {
                                "type": "folder",
                                "path": payload["path"],
                                "name": payload["name"],
                                "depth": current_depth,
                            }
                        )
                        if depth == 0 or current_depth < depth:
                            queue.append((payload["path"], current_depth + 1))
                    elif include_workflows:
                        items.append(
                            {
                                "type": "workflow",
                                "workflow_id": payload["id"],
                                "title": payload["title"],
                                "alias": payload["alias"],
                                "status": payload["status"],
                                "folder_path": current_path,
                                "depth": current_depth,
                                "tags": payload.get("tags") or [],
                            }
                        )

            return _json(
                {
                    "root_path": root_path,
                    "depth": "unlimited" if depth == 0 else depth,
                    "items": items,
                }
            )
    except ToolError:
        raise
    except ValueError as e:
        raise ToolError(str(e)) from e
    except Exception as e:
        logger.error("Failed to list workflow tree", error=str(e))
        raise ToolError(f"Failed to list workflow tree: {e}") from None


@mcp.tool()
async def create_workflow_folder(
    workspace_id: str,
    path: str,
    parents: bool = False,
) -> str:
    """Create a workflow folder by absolute path.

    Args:
        workspace_id: The workspace ID.
        path: Absolute folder path to create, e.g. "/security/detections/".
        parents: When true, create missing parent folders as needed.

    Returns JSON describing the resulting folder and any created paths.
    """

    try:
        _, role = await _resolve_workspace_role(workspace_id)
        normalized_path = _normalize_folder_path_arg(path, allow_root=False)
        parts = [part for part in normalized_path.strip("/").split("/") if part]

        async with WorkflowFolderService.with_session(role=role) as svc:
            if not parents:
                parent_parts = parts[:-1]
                parent_path = f"/{'/'.join(parent_parts)}/" if parent_parts else "/"
                if existing := await svc.get_folder_by_path(normalized_path):
                    folder = existing
                    created_paths = []
                else:
                    folder = await svc.create_folder(
                        name=parts[-1], parent_path=parent_path
                    )
                    created_paths = [normalized_path]
            else:
                current_path = "/"
                created_paths: list[str] = []
                folder = None
                for part in parts:
                    next_path = (
                        f"{current_path}{part}/" if current_path != "/" else f"/{part}/"
                    )
                    if existing := await svc.get_folder_by_path(next_path):
                        folder = existing
                    else:
                        folder = await svc.create_folder(
                            name=part,
                            parent_path=current_path,
                        )
                        created_paths.append(next_path)
                    current_path = next_path

                if folder is None:
                    raise ToolError(f"Failed to create folder {normalized_path}")

            return _json(
                {
                    "path": normalized_path,
                    "folder_id": str(folder.id),
                    "created_paths": created_paths,
                    "already_existed": not created_paths,
                }
            )
    except ToolError:
        raise
    except ValueError as e:
        raise ToolError(str(e)) from e
    except Exception as e:
        logger.error("Failed to create workflow folder", error=str(e))
        raise ToolError(f"Failed to create workflow folder: {e}") from None


@mcp.tool()
async def move_workflows(
    workspace_id: str,
    workflow_ids: list[str],
    destination_path: str = "/",
    dry_run: bool = False,
) -> str:
    """Move workflows into or out of a folder.

    This tool is best-effort and non-atomic. If one workflow fails to move,
    the remaining workflow moves still proceed.

    Args:
        workspace_id: The workspace ID.
        workflow_ids: Workflow IDs in short or full format.
        destination_path: Destination folder path, or "/" for root.
        dry_run: When true, validate and report the move without mutating state.

    Returns JSON with moved workflows and per-workflow errors.
    """

    try:
        if not workflow_ids:
            raise ToolError("workflow_ids must not be empty")

        _, role = await _resolve_workspace_role(workspace_id)
        normalized_destination = _normalize_folder_path_arg(destination_path)

        async with WorkflowFolderService.with_session(role=role) as folder_svc:
            folder = None
            if normalized_destination != "/":
                folder = await folder_svc.get_folder_by_path(normalized_destination)
                if folder is None:
                    raise ToolError(f"Folder {normalized_destination} not found")

            validated: list[dict[str, str]] = []
            errors: list[dict[str, str]] = []
            for raw_workflow_id in workflow_ids:
                try:
                    workflow_uuid = WorkflowUUID.new(raw_workflow_id)
                except ValueError as e:
                    errors.append({"workflow_id": raw_workflow_id, "error": str(e)})
                    continue

                statement = select(Workflow.id, Workflow.title).where(
                    Workflow.workspace_id == folder_svc.workspace_id,
                    Workflow.id == workflow_uuid,
                )
                result = await folder_svc.session.execute(statement)
                if row := result.one_or_none():
                    validated.append(
                        {
                            "workflow_id": str(row.id),
                            "title": row.title,
                        }
                    )
                else:
                    errors.append(
                        {
                            "workflow_id": raw_workflow_id,
                            "error": f"Workflow {raw_workflow_id} not found",
                        }
                    )

            if dry_run:
                return _json(
                    {
                        "destination_path": normalized_destination,
                        "requested_count": len(workflow_ids),
                        "movable_count": len(validated),
                        "movable_workflows": validated,
                        "errors": errors,
                    }
                )

            moved: list[dict[str, str]] = []
            for workflow_info in validated:
                try:
                    workflow_uuid = WorkflowUUID.new(workflow_info["workflow_id"])
                    await folder_svc.move_workflow(workflow_uuid, folder)
                    moved.append(workflow_info)
                except Exception as e:
                    await folder_svc.session.rollback()
                    errors.append(
                        {
                            "workflow_id": workflow_info["workflow_id"],
                            "error": str(e),
                        }
                    )

            return _json(
                {
                    "destination_path": normalized_destination,
                    "requested_count": len(workflow_ids),
                    "moved_count": len(moved),
                    "moved_workflows": moved,
                    "errors": errors,
                }
            )
    except ToolError:
        raise
    except ValueError as e:
        raise ToolError(str(e)) from e
    except Exception as e:
        logger.error("Failed to move workflows", error=str(e))
        raise ToolError(f"Failed to move workflows: {e}") from None


@mcp.tool()
async def list_actions(
    workspace_id: str,
    query: str | None = None,
    namespace: str | None = None,
    limit: int = 50,
) -> str:
    """Search or browse available actions and return compact context metadata.

    Supports three usage modes:
    - **Search**: provide `query` to search by name/description across all namespaces.
      Example: list_actions(workspace_id, query="send message")
    - **Browse namespace**: provide `namespace` without `query` to list all actions
      in a namespace. Example: list_actions(workspace_id, namespace="tools.slack")
    - **Browse all**: omit both to list all available actions.

    Common namespaces: `core`, `tools.slack`, `tools.crowdstrike`, `tools.okta`, `ai`.

    Args:
        workspace_id: The workspace ID (from list_workspaces).
        query: Optional search string to match against action names and descriptions.
        namespace: Optional namespace prefix filter (e.g. "tools.slack").
        limit: Maximum number of results (1-200, default 50).

    Returns JSON array of objects with fields:
    - action_name: Fully qualified name (e.g. "tools.slack.post_message")
    - description: One-line description of the action
    - configured: Whether required secrets are present in the workspace
    - missing_requirements: List of missing secret names/keys (if any)
    """

    try:
        _, role = await _resolve_workspace_role(workspace_id)
        limit = max(1, min(limit, 200))
        workspace_inventory = await _load_secret_inventory(role)
        async with RegistryActionsService.with_session(role=role) as svc:
            if query:
                entries = await svc.search_actions_from_index(query, limit=limit)
            else:
                entries = await svc.list_actions_from_index(namespace=namespace)
            items: list[dict[str, Any]] = []
            for entry, _ in entries:
                action_name = f"{entry.namespace}.{entry.name}"
                if query and namespace and not entry.namespace.startswith(namespace):
                    continue
                indexed = await svc.get_action_from_index(action_name)
                if indexed is None:
                    continue
                secrets = svc.aggregate_secrets_from_manifest(
                    indexed.manifest, action_name
                )
                requirements = _secrets_to_requirements(secrets)
                configured, missing = _evaluate_configuration(
                    requirements, workspace_inventory
                )
                items.append(
                    {
                        "action_name": action_name,
                        "description": entry.description,
                        "configured": configured,
                        "missing_requirements": missing,
                    }
                )
            return _json(items[:limit])
    except ValueError as e:
        raise ToolError(str(e)) from e
    except Exception as e:
        logger.error("Failed to list actions", error=str(e))
        raise ToolError(f"Failed to list actions: {e}") from None


@mcp.tool()
async def get_action_context(workspace_id: str, action_name: str) -> str:
    """Get full schema and configuration context for a single action.

    Use this after discovering an action via `list_actions` to get the complete
    parameter schema needed to write the `args:` block in a workflow definition.

    Example action names: "core.http_request", "tools.slack.post_message",
    "core.script.run_python", "core.transform.reshape".

    Args:
        workspace_id: The workspace ID (from list_workspaces).
        action_name: Fully qualified action name (e.g. "tools.slack.post_message").

    Returns JSON with fields:
    - action_name: The action name
    - description: What the action does
    - parameters_json_schema: JSON Schema for the action's args (map these to the
      YAML `args:` block in a workflow action definition)
    - required_secrets: List of secrets the action needs ({name, required_keys, optional_keys})
    - configured: Whether all required secrets are present
    - missing_requirements: List of missing secret names/keys
    - examples: Example args payload based on the schema
    """

    try:
        _, role = await _resolve_workspace_role(workspace_id)
        workspace_inventory = await _load_secret_inventory(role)
        async with RegistryActionsService.with_session(role=role) as svc:
            indexed = await svc.get_action_from_index(action_name)
            if indexed is None:
                raise ToolError(f"Action {action_name} not found")
            tool = await create_tool_from_registry(action_name, indexed)
            secrets = svc.aggregate_secrets_from_manifest(indexed.manifest, action_name)
            requirements = _secrets_to_requirements(secrets)
            configured, missing = _evaluate_configuration(
                requirements, workspace_inventory
            )
            schema = tool.parameters_json_schema
            return _json(
                {
                    "action_name": action_name,
                    "description": tool.description,
                    "parameters_json_schema": schema,
                    "required_secrets": requirements,
                    "configured": configured,
                    "missing_requirements": missing,
                    "examples": [_build_example_from_schema(schema)],
                }
            )
    except ToolError:
        raise
    except ValueError as e:
        raise ToolError(str(e)) from e
    except Exception as e:
        logger.error("Failed to get action context", error=str(e))
        raise ToolError(f"Failed to get action context: {e}") from None


@mcp.tool()
async def get_workflow_authoring_context(
    workspace_id: str,
    action_names_json: str | None = None,
    query: str | None = None,
) -> str:
    """Get compact workflow authoring context for selected actions.

    Returns everything needed to write a workflow definition: action schemas,
    available secrets, and workspace variables. Use this before calling
    `create_workflow` or `update_workflow`.

    Two input modes (provide one or neither):
    - **By name**: pass `action_names_json` as a JSON array of action names,
      e.g. '["core.http_request", "tools.slack.post_message"]'
    - **By search**: pass `query` to search for actions by name/description

    Args:
        workspace_id: The workspace ID (from list_workspaces).
        action_names_json: JSON array of fully qualified action names.
        query: Search string to find actions by name or description.

    Returns JSON with sections:
    - actions: Array of action contexts (schema, secrets, examples for each action)
    - variable_hints: Available workspace variables ({name, keys, environment})
    - secret_hints: Available workspace secrets ({name, keys, environment})
    - notes: Additional context about the response
    """

    try:
        _, role = await _resolve_workspace_role(workspace_id)
        action_names_raw = _parse_json_arg(action_names_json, "action_names_json")
        action_names: list[str] = []
        if action_names_raw is not None:
            if not isinstance(action_names_raw, list) or not all(
                isinstance(item, str) for item in action_names_raw
            ):
                raise ToolError("action_names_json must be a JSON array of strings")
            action_names = action_names_raw

        workspace_inventory = await _load_secret_inventory(role)
        action_contexts: list[dict[str, Any]] = []
        async with RegistryActionsService.with_session(role=role) as registry_svc:
            if not action_names and query:
                entries = await registry_svc.search_actions_from_index(query, limit=20)
                action_names = [
                    f"{entry.namespace}.{entry.name}" for entry, _ in entries
                ]
            for action_name in action_names:
                indexed = await registry_svc.get_action_from_index(action_name)
                if indexed is None:
                    continue
                tool = await create_tool_from_registry(action_name, indexed)
                requirements = _secrets_to_requirements(
                    registry_svc.aggregate_secrets_from_manifest(
                        indexed.manifest, action_name
                    )
                )
                configured, missing = _evaluate_configuration(
                    requirements, workspace_inventory
                )
                action_contexts.append(
                    {
                        "action_name": action_name,
                        "description": tool.description,
                        "parameters_json_schema": tool.parameters_json_schema,
                        "required_secrets": requirements,
                        "configured": configured,
                        "missing_requirements": missing,
                        "examples": [
                            _build_example_from_schema(tool.parameters_json_schema)
                        ],
                    }
                )

        async with VariablesService.with_session(role=role) as var_svc:
            variables = await var_svc.list_variables(
                environment=DEFAULT_SECRETS_ENVIRONMENT
            )
            variable_hints = [
                {
                    "name": var.name,
                    "keys": sorted(var.values.keys()),
                    "environment": var.environment,
                }
                for var in variables
            ]

        secret_hints: list[dict[str, Any]] = []
        for secret_name, keys in workspace_inventory.items():
            secret_hints.append(
                {
                    "name": secret_name,
                    "keys": sorted(keys),
                    "environment": DEFAULT_SECRETS_ENVIRONMENT,
                }
            )

        return _json(
            {
                "actions": action_contexts,
                "variable_hints": variable_hints,
                "secret_hints": secret_hints,
                "notes": [
                    "configured means required secret names and required key names exist in the default environment",
                ],
            }
        )
    except ToolError:
        raise
    except ValueError as e:
        raise ToolError(str(e)) from e
    except Exception as e:
        logger.error("Failed to build workflow authoring context", error=str(e))
        raise ToolError(f"Failed to build workflow authoring context: {e}") from None


# ---------------------------------------------------------------------------
# Validation and publishing tools
# ---------------------------------------------------------------------------


@mcp.tool()
async def validate_workflow(
    workspace_id: str,
    workflow_id: str,
) -> str:
    """Validate a workflow's draft state.

    Checks that the workflow DSL is structurally sound and that arguments are valid.

    Args:
        workspace_id: The workspace ID.
        workflow_id: The workflow ID.

    Returns JSON with valid (bool) and any errors.
    """

    try:
        _, role = await _resolve_workspace_role(workspace_id)
        wf_id = WorkflowUUID.new(workflow_id)

        async with WorkflowsManagementService.with_session(role=role) as svc:
            workflow = await svc.get_workflow(wf_id)
            if not workflow:
                raise ToolError(f"Workflow {workflow_id} not found")

            # Tier 1: Build DSL
            errors: list[dict[str, Any]] = []
            dsl: DSLInput | None = None
            try:
                dsl = await svc.build_dsl_from_workflow(workflow)
            except TracecatValidationError as e:
                errors.append({"type": "dsl", "message": str(e)})
            except ValidationError as e:
                errors.append({"type": "dsl", "message": str(e)})

            if errors or dsl is None:
                return _json({"valid": False, "errors": errors})

            # Tier 2: Semantic validation
            val_results = await validate_dsl(session=svc.session, dsl=dsl, role=role)
            if val_results:
                for vr in val_results:
                    errors.append(_validation_result_payload(vr))
                return _json({"valid": False, "errors": errors})

            return _json({"valid": True, "errors": []})
    except ToolError:
        raise
    except ValueError as e:
        raise ToolError(str(e)) from e
    except BaseException as e:
        msg = str(e)
        if isinstance(e, BaseExceptionGroup):
            msgs = [str(exc) for exc in e.exceptions]
            msg = "; ".join(msgs)
        logger.error("Failed to validate workflow", error=msg)
        return _json(
            {
                "valid": False,
                "errors": [
                    {
                        "type": "internal",
                        "message": "An internal error occurred during validation. Check server logs for details.",
                    }
                ],
            }
        )


@mcp.tool()
async def validate_template_action(
    workspace_id: str,
    template_yaml: str | None = None,
    check_db: bool = False,
) -> str:
    """Validate a template action YAML payload.

    Validates YAML parsing, template schema correctness, step action references,
    argument schemas, and expression references.

    Args:
        workspace_id: The workspace ID.
        template_yaml: Full template action YAML content.
        check_db: When True, also resolve missing actions from registry DB.
            Defaults to False for local-only validation.

    Returns JSON with valid (bool), action_name (if available), and any errors.
    """

    try:
        _, role = await _resolve_workspace_role(workspace_id)
        action_name: str | None = None

        if template_yaml is None:
            raise ToolError("template_yaml is required")

        try:
            raw_template = yaml.safe_load(template_yaml)
        except yaml.YAMLError as exc:
            return _json(
                {
                    "valid": False,
                    "action_name": action_name,
                    "errors": [
                        {
                            "type": "yaml_error",
                            "message": str(exc),
                        }
                    ],
                }
            )

        try:
            template = TemplateAction.model_validate(raw_template)
            action_name = template.definition.action
        except ValidationError as exc:
            return _json(
                {
                    "valid": False,
                    "action_name": action_name,
                    "errors": [
                        {
                            "type": "schema_validation_error",
                            "message": "Template action schema validation failed",
                            "details": [
                                {
                                    "type": err.get("type"),
                                    "msg": err.get("msg"),
                                    "loc": list(err.get("loc", ())),
                                }
                                for err in exc.errors(include_url=False)
                            ],
                        }
                    ],
                }
            )
        except TracecatValidationError as exc:
            return _json(
                {
                    "valid": False,
                    "action_name": action_name,
                    "errors": [
                        {
                            "type": "schema_validation_error",
                            "message": str(exc),
                        }
                    ],
                }
            )

        repo = Repository(role=role)
        repo.init(include_base=True, include_templates=True)
        repo.register_template_action(template, origin="mcp")
        bound_action = repo.store[template.definition.action]

        async with RegistryActionsService.with_session(role=role) as svc:
            errs = await validate_template_action_impl(
                bound_action,
                repo,
                check_db=check_db,
                ra_service=svc,
            )

        return _json(
            {
                "valid": len(errs) == 0,
                "action_name": action_name,
                "errors": [err.model_dump(mode="json") for err in errs],
            }
        )
    except ToolError:
        raise
    except ValueError as exc:
        raise ToolError(str(exc)) from exc
    except BaseException as exc:
        msg = str(exc)
        if isinstance(exc, BaseExceptionGroup):
            msgs = [str(err) for err in exc.exceptions]
            msg = "; ".join(msgs)
        logger.error("Failed to validate template action", error=msg)
        return _json(
            {
                "valid": False,
                "action_name": None,
                "errors": [
                    {
                        "type": "internal",
                        "message": "An internal error occurred during validation. Check server logs for details.",
                    }
                ],
            }
        )


@mcp.tool()
async def publish_workflow(
    workspace_id: str,
    workflow_id: str,
) -> str:
    """Publish (commit) a workflow, creating a new versioned definition.

    This validates the workflow, freezes registry dependencies, and creates a
    new workflow definition version.

    Args:
        workspace_id: The workspace ID.
        workflow_id: The workflow ID.

    Returns JSON with workflow_id, status, message, version, and any errors.
    """

    try:
        _, role = await _resolve_workspace_role(workspace_id)
        wf_id = WorkflowUUID.new(workflow_id)

        async with WorkflowsManagementService.with_session(role=role) as svc:
            session = svc.session
            workflow = await svc.get_workflow(wf_id)
            if not workflow:
                raise ToolError(f"Workflow {workflow_id} not found")

            # Tier 1: Build DSL
            construction_errors: list[dict[str, Any]] = []
            dsl: DSLInput | None = None
            try:
                dsl = await svc.build_dsl_from_workflow(workflow)
            except TracecatValidationError as e:
                construction_errors.append(
                    {
                        "type": "dsl",
                        "status": "error",
                        "message": str(e),
                    }
                )
            except ValidationError as e:
                construction_errors.append(
                    {
                        "type": "dsl",
                        "status": "error",
                        "message": str(e),
                    }
                )

            if construction_errors:
                return _json(
                    {
                        "workflow_id": workflow_id,
                        "status": "failure",
                        "message": f"DSL construction failed with {len(construction_errors)} errors",
                        "errors": construction_errors,
                    }
                )

            if dsl is None:
                raise ToolError("DSL should be defined if no construction errors")

            # Tier 2: Semantic validation
            val_errors = await validate_dsl(session=session, dsl=dsl, role=role)
            if val_errors:
                return _json(
                    {
                        "workflow_id": workflow_id,
                        "status": "failure",
                        "message": f"{len(val_errors)} validation error(s)",
                        "errors": [_validation_result_payload(vr) for vr in val_errors],
                    }
                )

            # Phase 1: Resolve registry lock
            lock_service = RegistryLockService(session, role)
            action_names = {action.action for action in dsl.actions}
            registry_lock = await lock_service.resolve_lock_with_bindings(action_names)
            workflow.registry_lock = registry_lock.model_dump()

            # Phase 2: Create workflow definition
            defn_service = WorkflowDefinitionsService(session, role=role)
            defn = await defn_service.create_workflow_definition(
                wf_id,
                dsl,
                alias=workflow.alias,
                registry_lock=registry_lock,
                commit=False,
            )

            # Phase 3: Update workflow version
            workflow.version = defn.version
            session.add(workflow)
            session.add(defn)
            await session.commit()
            await session.refresh(workflow)
            await session.refresh(defn)

            return _json(
                {
                    "workflow_id": workflow_id,
                    "status": "success",
                    "message": "Workflow published successfully",
                    "version": defn.version,
                }
            )
    except ToolError:
        raise
    except ValueError as e:
        raise ToolError(str(e)) from e
    except BaseException as e:
        msg = str(e)
        if isinstance(e, BaseExceptionGroup):
            msgs = [str(exc) for exc in e.exceptions]
            msg = "; ".join(msgs)
        logger.error("Failed to publish workflow", error=msg)
        raise ToolError(f"Failed to publish workflow: {msg}") from None


# ---------------------------------------------------------------------------
# Execution tools
# ---------------------------------------------------------------------------


@mcp.tool()
async def run_draft_workflow(
    workspace_id: str,
    workflow_id: str,
    inputs: str | None = None,
    title: str | None = None,
    description: str | None = None,
) -> str:
    """Run a workflow from its current draft state (without publishing).

    Optionally update the workflow's title/description before running.

    Args:
        workspace_id: The workspace ID.
        workflow_id: The workflow ID.
        inputs: Optional JSON string of trigger inputs.
        title: Optional new title to set before running.
        description: Optional new description to set before running.

    Returns JSON with workflow_id, execution_id, and a message.
    """

    try:
        _, role = await _resolve_workspace_role(workspace_id)
        wf_id = WorkflowUUID.new(workflow_id)

        # Optionally update workflow first
        if title or description:
            async with WorkflowsManagementService.with_session(role=role) as svc:
                await svc.update_workflow(
                    wf_id,
                    WorkflowUpdate(title=title, description=description),
                )

        # Build DSL from draft
        async with WorkflowsManagementService.with_session(role=role) as svc:
            workflow = await svc.get_workflow(wf_id)
            if not workflow:
                raise ToolError(f"Workflow {workflow_id} not found")
            try:
                dsl_input = await svc.build_dsl_from_workflow(workflow)
            except (TracecatValidationError, ValidationError) as e:
                raise ToolError(f"Draft workflow has validation errors: {e}") from e

        # Validate and parse trigger inputs before dispatch
        payload = _validate_and_parse_trigger_inputs(dsl_input, inputs)
        exec_service = await WorkflowExecutionsService.connect(role=role)
        response = await exec_service.create_draft_workflow_execution_wait_for_start(
            dsl=dsl_input,
            wf_id=wf_id,
            payload=payload,
        )
        return _json(
            {
                "workflow_id": str(response["wf_id"]),
                "execution_id": str(response["wf_exec_id"]),
                "message": response["message"],
            }
        )
    except ToolError:
        raise
    except ValueError as e:
        raise ToolError(str(e)) from e
    except BaseException as e:
        msg = str(e)
        if isinstance(e, BaseExceptionGroup):
            msgs = [str(exc) for exc in e.exceptions]
            msg = "; ".join(msgs)
        logger.error("Failed to run draft workflow", error=msg)
        raise ToolError(f"Failed to run draft workflow: {msg}") from None


@mcp.tool()
async def run_published_workflow(
    workspace_id: str,
    workflow_id: str,
    inputs: str | None = None,
) -> str:
    """Run the latest published version of a workflow.

    The workflow must have been published (committed) at least once.

    Args:
        workspace_id: The workspace ID.
        workflow_id: The workflow ID.
        inputs: Optional JSON string of trigger inputs.

    Returns JSON with workflow_id, execution_id, and a message.
    """

    try:
        ws_id, role = await _resolve_workspace_role(workspace_id)
        wf_id = WorkflowUUID.new(workflow_id)

        # Fetch latest workflow definition scoped to the caller's workspace
        async with get_async_session_context_manager() as session:
            result = await session.execute(
                select(WorkflowDefinition)
                .where(
                    WorkflowDefinition.workflow_id == wf_id,
                    WorkflowDefinition.workspace_id == ws_id,
                )
                .order_by(WorkflowDefinition.version.desc())
            )
            defn = result.scalars().first()
            if not defn:
                raise ToolError(
                    f"No published definition found for workflow {workflow_id}. "
                    "Publish the workflow first using publish_workflow."
                )

            dsl_input = DSLInput(**defn.content)
            registry_lock = (
                RegistryLock.model_validate(defn.registry_lock)
                if defn.registry_lock
                else None
            )

        # Validate and parse trigger inputs before dispatch
        payload = _validate_and_parse_trigger_inputs(dsl_input, inputs)
        exec_service = await WorkflowExecutionsService.connect(role=role)
        response = await exec_service.create_workflow_execution_wait_for_start(
            dsl=dsl_input,
            wf_id=wf_id,
            payload=payload,
            registry_lock=registry_lock,
        )
        return _json(
            {
                "workflow_id": str(response["wf_id"]),
                "execution_id": str(response["wf_exec_id"]),
                "message": response["message"],
            }
        )
    except ToolError:
        raise
    except ValueError as e:
        raise ToolError(str(e)) from e
    except BaseException as e:
        msg = str(e)
        if isinstance(e, BaseExceptionGroup):
            msgs = [str(exc) for exc in e.exceptions]
            msg = "; ".join(msgs)
        logger.error("Failed to run published workflow", error=msg)
        raise ToolError(f"Failed to run published workflow: {msg}") from None


@mcp.tool()
async def list_workflow_executions(
    workspace_id: str,
    workflow_id: str,
    limit: int = 20,
) -> str:
    """List recent executions for a workflow.

    Use this to see run history, check which runs succeeded or failed, and
    find execution IDs for deeper inspection with get_workflow_execution.

    Args:
        workspace_id: The workspace ID.
        workflow_id: The workflow ID.
        limit: Maximum number of executions to return (1-100, default 20).

    Returns JSON array of execution objects with id, run_id, status,
    start_time, close_time, trigger_type, and execution_type.
    """

    try:
        _, role = await _resolve_workspace_role(workspace_id)
        wf_id = WorkflowUUID.new(workflow_id)
        limit = max(1, min(limit, 100))

        exec_service = await WorkflowExecutionsService.connect(role=role)
        executions = await exec_service.list_executions(workflow_id=wf_id, limit=limit)
        items: list[dict[str, Any]] = []
        for execution in executions:
            trigger_type = None
            execution_type = None
            try:
                trigger_type = get_trigger_type_from_search_attr(
                    execution.typed_search_attributes, execution.id
                )
                execution_type = get_execution_type_from_search_attr(
                    execution.typed_search_attributes
                )
            except Exception:
                pass
            items.append(
                {
                    "id": execution.id,
                    "run_id": execution.run_id,
                    "status": _format_temporal_status(execution.status),
                    "start_time": str(execution.start_time),
                    "close_time": (
                        str(execution.close_time) if execution.close_time else None
                    ),
                    "trigger_type": str(trigger_type) if trigger_type else None,
                    "execution_type": str(execution_type) if execution_type else None,
                }
            )
        return _json(items)
    except ValueError as e:
        raise ToolError(str(e)) from e
    except Exception as e:
        logger.error("Failed to list workflow executions", error=str(e))
        raise ToolError(f"Failed to list workflow executions: {e}") from None


@mcp.tool()
async def get_workflow_execution(
    workspace_id: str,
    execution_id: str,
) -> str:
    """Get status and details of a specific workflow execution.

    Returns execution metadata (status, timing) and a compact event timeline
    showing each action's status, timing, and any errors. Use this to debug
    failed runs or check the progress of running workflows.

    Args:
        workspace_id: The workspace ID.
        execution_id: The workflow execution ID (returned by run_* tools or
            list_workflow_executions).

    Returns JSON with execution metadata (id, run_id, status, start_time,
    close_time) and an events array with per-action status, timing, inputs,
    results, and errors.
    """

    try:
        _, role = await _resolve_workspace_role(workspace_id)

        # Verify the execution's workflow belongs to this workspace
        try:
            wf_id, _ = exec_id_to_parts(execution_id)
        except ValueError as e:
            raise ToolError(f"Invalid execution ID: {e}") from e
        async with WorkflowsManagementService.with_session(role=role) as mgmt_svc:
            workflow = await mgmt_svc.get_workflow(wf_id)
        if workflow is None:
            raise ToolError(
                f"Execution {execution_id} not found in workspace {workspace_id}"
            )

        exec_service = await WorkflowExecutionsService.connect(role=role)
        execution = await exec_service.get_execution(execution_id)
        if execution is None:
            raise ToolError(f"Execution {execution_id} not found")

        trigger_type = None
        execution_type = None
        try:
            trigger_type = get_trigger_type_from_search_attr(
                execution.typed_search_attributes, execution.id
            )
            execution_type = get_execution_type_from_search_attr(
                execution.typed_search_attributes
            )
        except Exception:
            pass

        # Get compact event history for action-level details
        compact_events = await exec_service.list_workflow_execution_events_compact(
            execution_id
        )

        events_payload: list[dict[str, Any]] = []
        for event in compact_events:
            event_data: dict[str, Any] = {
                "action_ref": event.action_ref,
                "action_name": event.action_name,
                "status": str(event.status),
                "schedule_time": str(event.schedule_time),
                "start_time": str(event.start_time) if event.start_time else None,
                "close_time": str(event.close_time) if event.close_time else None,
            }
            if event.action_error is not None:
                error_info: dict[str, Any] = {
                    "message": event.action_error.message,
                }
                if event.action_error.cause:
                    error_info["cause"] = event.action_error.cause
                event_data["error"] = error_info
            if event.action_result is not None:
                try:
                    result_str = json.dumps(event.action_result, default=str)
                    if len(result_str) > 2000:
                        event_data["result_truncated"] = result_str[:2000] + "..."
                    else:
                        event_data["result"] = event.action_result
                except (TypeError, ValueError):
                    event_data["result"] = str(event.action_result)[:2000]
            events_payload.append(event_data)

        return _json(
            {
                "id": execution.id,
                "run_id": execution.run_id,
                "status": _format_temporal_status(execution.status),
                "start_time": str(execution.start_time),
                "close_time": (
                    str(execution.close_time) if execution.close_time else None
                ),
                "trigger_type": str(trigger_type) if trigger_type else None,
                "execution_type": str(execution_type) if execution_type else None,
                "history_length": execution.history_length,
                "events": events_payload,
            }
        )
    except ToolError:
        raise
    except ValueError as e:
        raise ToolError(str(e)) from e
    except Exception as e:
        logger.error("Failed to get workflow execution", error=str(e))
        raise ToolError(f"Failed to get workflow execution: {e}") from None


# ---------------------------------------------------------------------------
# Webhook tools
# ---------------------------------------------------------------------------


@mcp.tool()
async def get_webhook(
    workspace_id: str,
    workflow_id: str,
) -> str:
    """Get webhook configuration for a workflow.

    Args:
        workspace_id: The workspace ID.
        workflow_id: The workflow ID.

    Returns JSON with the webhook configuration (id, secret, status, methods,
    url, entrypoint_ref, allowlisted_cidrs, filters, api_key).
    """

    try:
        _, role = await _resolve_workspace_role(workspace_id)
        wf_id = WorkflowUUID.new(workflow_id)

        async with get_async_session_context_manager() as session:
            webhook = await webhook_service.get_webhook(
                session=session,
                workspace_id=role.workspace_id,
                workflow_id=wf_id,
            )
            if webhook is None:
                raise ToolError(f"Webhook not found for workflow {workflow_id}")
            webhook_read = WebhookRead.model_validate(webhook, from_attributes=True)
            return _json(webhook_read.model_dump(mode="json"))
    except ToolError:
        raise
    except ValueError as e:
        raise ToolError(str(e)) from e
    except Exception as e:
        logger.error("Failed to get webhook", error=str(e))
        raise ToolError(f"Failed to get webhook: {e}") from None


@mcp.tool()
async def update_webhook(
    workspace_id: str,
    workflow_id: str,
    status: str | None = None,
    methods: str | None = None,
    entrypoint_ref: str | None = None,
    allowlisted_cidrs: str | None = None,
) -> str:
    """Update webhook configuration for a workflow.

    Args:
        workspace_id: The workspace ID.
        workflow_id: The workflow ID.
        status: Enum string: `"online"` or `"offline"`.
        methods: JSON array of uppercase HTTP methods, e.g.
            '["GET","POST"]'. Schema: list[str].
        entrypoint_ref: Entrypoint action ref.
        allowlisted_cidrs: JSON array of CIDR strings, e.g.
            '["10.0.0.0/8","192.168.1.0/24"]'. Schema: list[str].

    Returns a confirmation message.
    """

    try:
        _, role = await _resolve_workspace_role(workspace_id)
        wf_id = WorkflowUUID.new(workflow_id)

        update_kwargs: dict[str, Any] = {}
        if status is not None:
            update_kwargs["status"] = status
        if methods is not None:
            update_kwargs["methods"] = _parse_json_arg(methods, "methods")
        if entrypoint_ref is not None:
            update_kwargs["entrypoint_ref"] = entrypoint_ref
        if allowlisted_cidrs is not None:
            update_kwargs["allowlisted_cidrs"] = _parse_json_arg(
                allowlisted_cidrs, "allowlisted_cidrs"
            )

        update_params = WebhookUpdate(**update_kwargs)

        async with get_async_session_context_manager() as session:
            webhook = await webhook_service.get_webhook(
                session=session,
                workspace_id=role.workspace_id,
                workflow_id=wf_id,
            )
            if webhook is None:
                raise ToolError(f"Webhook not found for workflow {workflow_id}")

            for key, value in update_params.model_dump(exclude_unset=True).items():
                setattr(webhook, key, value)

            session.add(webhook)
            await session.commit()
        return _json(
            {"message": f"Webhook for workflow {workflow_id} updated successfully"}
        )
    except ToolError:
        raise
    except ValueError as e:
        raise ToolError(str(e)) from e
    except Exception as e:
        logger.error("Failed to update webhook", error=str(e))
        raise ToolError(f"Failed to update webhook: {e}") from None


# ---------------------------------------------------------------------------
# Case trigger tools
# ---------------------------------------------------------------------------


@mcp.tool()
async def get_case_trigger(
    workspace_id: str,
    workflow_id: str,
) -> str:
    """Get case trigger configuration for a workflow.

    Args:
        workspace_id: The workspace ID.
        workflow_id: The workflow ID.

    Returns JSON with the case trigger (id, workflow_id, status, event_types,
    tag_filters).
    """

    try:
        _, role = await _resolve_workspace_role(workspace_id)
        wf_id = WorkflowUUID.new(workflow_id)

        async with CaseTriggersService.with_session(role=role) as svc:
            case_trigger = await svc.get_case_trigger(wf_id)
            ct_read = CaseTriggerRead.model_validate(case_trigger, from_attributes=True)
            return _json(ct_read.model_dump(mode="json"))
    except TracecatNotFoundError as e:
        raise ToolError(str(e)) from e
    except ValueError as e:
        raise ToolError(str(e)) from e
    except Exception as e:
        logger.error("Failed to get case trigger", error=str(e))
        raise ToolError(f"Failed to get case trigger: {e}") from None


@mcp.tool()
async def update_case_trigger(
    workspace_id: str,
    workflow_id: str,
    status: str | None = None,
    event_types: str | None = None,
    tag_filters: str | None = None,
) -> str:
    """Update an existing case trigger for a workflow.

    Args:
        workspace_id: The workspace ID.
        workflow_id: The workflow ID.
        status: Enum string: `"online"` or `"offline"`.
        event_types: JSON array of case event type strings using underscores.
            Schema: list[str]. Valid values are the `CaseEventType` enum values
            documented in the shared MCP instructions.
        tag_filters: JSON array of tag ref strings, e.g.
            '["malware","phishing"]'. Schema: list[str].

    Returns a confirmation message.
    """
    try:
        _, role = await _resolve_workspace_role(workspace_id)
        wf_id = WorkflowUUID.new(workflow_id)

        parsed_event_types = _parse_json_arg(event_types, "event_types")
        parsed_tag_filters = _parse_json_arg(tag_filters, "tag_filters")

        update_params = CaseTriggerUpdate(
            status=status,  # pyright: ignore[reportArgumentType]
            event_types=parsed_event_types,
            tag_filters=parsed_tag_filters,
        )

        async with CaseTriggersService.with_session(role=role) as svc:
            await svc.update_case_trigger(
                wf_id, update_params, create_missing_tags=True
            )
        return _json(
            {
                "message": (
                    f"Case trigger for workflow {workflow_id} updated successfully"
                )
            }
        )
    except TracecatNotFoundError as e:
        raise ToolError(str(e)) from e
    except ToolError:
        raise
    except ValueError as e:
        raise ToolError(str(e)) from e
    except Exception as e:
        logger.error("Failed to update case trigger", error=str(e))
        raise ToolError(f"Failed to update case trigger: {e}") from None


# ---------------------------------------------------------------------------
# Workflow tag tools
# ---------------------------------------------------------------------------


@mcp.tool()
async def list_workflow_tags(workspace_id: str) -> str:
    """List workflow tag definitions in a workspace.

    Returns a JSON array of tag objects with `id`, `name`, `ref`, and `color`.
    """

    try:
        _, role = await _resolve_workspace_role(workspace_id)
        async with TagsService.with_session(role=role) as svc:
            tags = await svc.list_tags()
            return _json([_workflow_tag_payload(tag) for tag in tags])
    except ValueError as e:
        raise ToolError(str(e)) from e
    except Exception as e:
        logger.error("Failed to list workflow tags", error=str(e))
        raise ToolError(f"Failed to list workflow tags: {e}") from None


@mcp.tool()
async def create_workflow_tag(
    workspace_id: str,
    name: str,
    color: str | None = None,
) -> str:
    """Create a workflow tag definition.

    Args:
        workspace_id: The workspace ID.
        name: Tag display name.
        color: Optional hex color string such as `"#ff0000"`.

    Returns JSON with the created tag's `id`, `name`, `ref`, and `color`.
    """

    try:
        _, role = await _resolve_workspace_role(workspace_id)
        async with TagsService.with_session(role=role) as svc:
            tag = await svc.create_tag(TagCreate(name=name, color=color))
            return _json(_workflow_tag_payload(tag))
    except ValueError as e:
        raise ToolError(str(e)) from e
    except Exception as e:
        logger.error("Failed to create workflow tag", error=str(e))
        raise ToolError(f"Failed to create workflow tag: {e}") from None


@mcp.tool()
async def update_workflow_tag(
    workspace_id: str,
    tag_id: str,
    name: str | None = None,
    color: str | None = None,
) -> str:
    """Update a workflow tag definition.

    Args:
        workspace_id: The workspace ID.
        tag_id: Tag UUID from `list_workflow_tags`. Tag refs are also accepted.
        name: Optional new tag name.
        color: Optional new hex color string such as `"#ff0000"`.

    Returns JSON with the updated tag's `id`, `name`, `ref`, and `color`.
    """

    try:
        _, role = await _resolve_workspace_role(workspace_id)
        async with TagsService.with_session(role=role) as svc:
            tag = await svc.get_tag_by_ref_or_id(tag_id)
            updated = await svc.update_tag(
                tag,
                _build_tag_update_params(name=name, color=color),
            )
            return _json(_workflow_tag_payload(updated))
    except NoResultFound:
        raise ToolError(f"Workflow tag {tag_id!r} not found") from None
    except ValueError as e:
        raise ToolError(str(e)) from e
    except Exception as e:
        logger.error("Failed to update workflow tag", error=str(e))
        raise ToolError(f"Failed to update workflow tag: {e}") from None


@mcp.tool()
async def delete_workflow_tag(workspace_id: str, tag_id: str) -> str:
    """Delete a workflow tag definition.

    Args:
        workspace_id: The workspace ID.
        tag_id: Tag UUID from `list_workflow_tags`. Tag refs are also accepted.

    Returns a confirmation message.
    """

    try:
        _, role = await _resolve_workspace_role(workspace_id)
        async with TagsService.with_session(role=role) as svc:
            tag = await svc.get_tag_by_ref_or_id(tag_id)
            await svc.delete_tag(tag)
            return _json({"message": f"Workflow tag {tag_id} deleted successfully"})
    except NoResultFound:
        raise ToolError(f"Workflow tag {tag_id!r} not found") from None
    except ValueError as e:
        raise ToolError(str(e)) from e
    except Exception as e:
        logger.error("Failed to delete workflow tag", error=str(e))
        raise ToolError(f"Failed to delete workflow tag: {e}") from None


@mcp.tool()
async def list_tags_for_workflow(workspace_id: str, workflow_id: str) -> str:
    """List tags attached to a workflow.

    Returns a JSON array of tag objects with `id`, `name`, `ref`, and `color`.
    """

    try:
        _, role = await _resolve_workspace_role(workspace_id)
        wf_id = WorkflowUUID.new(workflow_id)
        async with WorkflowTagsService.with_session(role=role) as svc:
            tags = await svc.list_tags_for_workflow(wf_id)
            return _json([_workflow_tag_payload(tag) for tag in tags])
    except ValueError as e:
        raise ToolError(str(e)) from e
    except Exception as e:
        logger.error("Failed to list workflow tags for workflow", error=str(e))
        raise ToolError(f"Failed to list workflow tags for workflow: {e}") from None


@mcp.tool()
async def add_workflow_tag(
    workspace_id: str,
    workflow_id: str,
    tag_id: str,
) -> str:
    """Attach an existing workflow tag definition to a workflow.

    Args:
        workspace_id: The workspace ID.
        workflow_id: Workflow ID.
        tag_id: Workflow tag UUID from `list_workflow_tags`.

    Returns a confirmation message.
    """

    try:
        _, role = await _resolve_workspace_role(workspace_id)
        wf_id = WorkflowUUID.new(workflow_id)
        parsed_tag_id = uuid.UUID(tag_id)
        async with WorkflowTagsService.with_session(role=role) as svc:
            await svc.add_workflow_tag(wf_id, parsed_tag_id)
            return _json(
                {"message": (f"Workflow tag {tag_id} added to workflow {workflow_id}")}
            )
    except ValueError as e:
        raise ToolError(str(e)) from e
    except Exception as e:
        logger.error("Failed to add workflow tag", error=str(e))
        raise ToolError(f"Failed to add workflow tag: {e}") from None


@mcp.tool()
async def remove_workflow_tag(
    workspace_id: str,
    workflow_id: str,
    tag_id: str,
) -> str:
    """Remove a workflow tag association from a workflow.

    Args:
        workspace_id: The workspace ID.
        workflow_id: Workflow ID.
        tag_id: Workflow tag UUID from `list_workflow_tags`.

    Returns a confirmation message.
    """

    try:
        _, role = await _resolve_workspace_role(workspace_id)
        wf_id = WorkflowUUID.new(workflow_id)
        parsed_tag_id = uuid.UUID(tag_id)
        async with WorkflowTagsService.with_session(role=role) as svc:
            wf_tag = await svc.get_workflow_tag(wf_id, parsed_tag_id)
            await svc.remove_workflow_tag(wf_tag)
            return _json(
                {
                    "message": (
                        f"Workflow tag {tag_id} removed from workflow {workflow_id}"
                    )
                }
            )
    except NoResultFound:
        raise ToolError(
            f"Workflow tag {tag_id!r} is not attached to workflow {workflow_id}"
        ) from None
    except ValueError as e:
        raise ToolError(str(e)) from e
    except Exception as e:
        logger.error("Failed to remove workflow tag", error=str(e))
        raise ToolError(f"Failed to remove workflow tag: {e}") from None


# ---------------------------------------------------------------------------
# Case tag tools
# ---------------------------------------------------------------------------


@mcp.tool()
async def list_case_tags(workspace_id: str) -> str:
    """List case tag definitions in a workspace.

    Returns a JSON array of tag objects with `id`, `name`, `ref`, and `color`.
    """

    try:
        _, role = await _resolve_workspace_role(workspace_id)
        async with CaseTagsService.with_session(role=role) as svc:
            tags = await svc.list_workspace_tags()
            return _json([_case_tag_payload(tag) for tag in tags])
    except ValueError as e:
        raise ToolError(str(e)) from e
    except Exception as e:
        logger.error("Failed to list case tags", error=str(e))
        raise ToolError(f"Failed to list case tags: {e}") from None


@mcp.tool()
async def create_case_tag(
    workspace_id: str,
    name: str,
    color: str | None = None,
) -> str:
    """Create a case tag definition.

    Args:
        workspace_id: The workspace ID.
        name: Tag display name.
        color: Optional hex color string such as `"#ff0000"`.

    Returns JSON with the created tag's `id`, `name`, `ref`, and `color`.
    """

    try:
        _, role = await _resolve_workspace_role(workspace_id)
        async with CaseTagsService.with_session(role=role) as svc:
            tag = await svc.create_tag(TagCreate(name=name, color=color))
            return _json(_case_tag_payload(tag))
    except ValueError as e:
        raise ToolError(str(e)) from e
    except Exception as e:
        logger.error("Failed to create case tag", error=str(e))
        raise ToolError(f"Failed to create case tag: {e}") from None


@mcp.tool()
async def update_case_tag(
    workspace_id: str,
    tag_id: str,
    name: str | None = None,
    color: str | None = None,
) -> str:
    """Update a case tag definition.

    Args:
        workspace_id: The workspace ID.
        tag_id: Case tag UUID or ref from `list_case_tags`.
        name: Optional new tag name.
        color: Optional new hex color string such as `"#ff0000"`.

    Returns JSON with the updated tag's `id`, `name`, `ref`, and `color`.
    """

    try:
        _, role = await _resolve_workspace_role(workspace_id)
        async with CaseTagsService.with_session(role=role) as svc:
            tag = await svc.get_tag_by_ref_or_id(tag_id)
            updated = await svc.update_tag(
                tag,
                _build_tag_update_params(name=name, color=color),
            )
            return _json(_case_tag_payload(updated))
    except NoResultFound:
        raise ToolError(f"Case tag {tag_id!r} not found") from None
    except ValueError as e:
        raise ToolError(str(e)) from e
    except Exception as e:
        logger.error("Failed to update case tag", error=str(e))
        raise ToolError(f"Failed to update case tag: {e}") from None


@mcp.tool()
async def delete_case_tag(workspace_id: str, tag_id: str) -> str:
    """Delete a case tag definition.

    Args:
        workspace_id: The workspace ID.
        tag_id: Case tag UUID or ref from `list_case_tags`.

    Returns a confirmation message.
    """

    try:
        _, role = await _resolve_workspace_role(workspace_id)
        async with CaseTagsService.with_session(role=role) as svc:
            tag = await svc.get_tag_by_ref_or_id(tag_id)
            await svc.delete_tag(tag)
            return _json({"message": f"Case tag {tag_id} deleted successfully"})
    except NoResultFound:
        raise ToolError(f"Case tag {tag_id!r} not found") from None
    except ValueError as e:
        raise ToolError(str(e)) from e
    except Exception as e:
        logger.error("Failed to delete case tag", error=str(e))
        raise ToolError(f"Failed to delete case tag: {e}") from None


@mcp.tool()
async def list_tags_for_case(workspace_id: str, case_id: str) -> str:
    """List tags attached to a case.

    Returns a JSON array of tag objects with `id`, `name`, `ref`, and `color`.
    """

    try:
        _, role = await _resolve_workspace_role(workspace_id)
        parsed_case_id = uuid.UUID(case_id)
        async with CaseTagsService.with_session(role=role) as svc:
            tags = await svc.list_tags_for_case(parsed_case_id)
            return _json([_case_tag_payload(tag) for tag in tags])
    except ValueError as e:
        raise ToolError(str(e)) from e
    except Exception as e:
        logger.error("Failed to list case tags for case", error=str(e))
        raise ToolError(f"Failed to list case tags for case: {e}") from None


@mcp.tool()
async def add_case_tag(
    workspace_id: str,
    case_id: str,
    tag_identifier: str,
) -> str:
    """Attach a case tag to a case.

    Args:
        workspace_id: The workspace ID.
        case_id: Case UUID.
        tag_identifier: Case tag UUID, ref, or free-form name that resolves to an
            existing tag definition. Resolution order is UUID, then exact ref, then
            slugified free-form name. Create the tag first with `create_case_tag`
            if needed.

    Returns JSON for the added tag with `id`, `name`, `ref`, and `color`.
    """

    try:
        _, role = await _resolve_workspace_role(workspace_id)
        parsed_case_id = uuid.UUID(case_id)
        async with CaseTagsService.with_session(role=role) as svc:
            tag = await svc.add_case_tag(parsed_case_id, tag_identifier)
            return _json(_case_tag_payload(tag))
    except NoResultFound as e:
        raise ToolError(str(e)) from e
    except ValueError as e:
        raise ToolError(str(e)) from e
    except Exception as e:
        logger.error("Failed to add case tag", error=str(e))
        raise ToolError(f"Failed to add case tag: {e}") from None


@mcp.tool()
async def remove_case_tag(
    workspace_id: str,
    case_id: str,
    tag_identifier: str,
) -> str:
    """Remove a case tag association from a case.

    Args:
        workspace_id: The workspace ID.
        case_id: Case UUID.
        tag_identifier: Case tag UUID, ref, or free-form name. Resolution order is
            UUID, then exact ref, then slugified free-form name.

    Returns a confirmation message.
    """

    try:
        _, role = await _resolve_workspace_role(workspace_id)
        parsed_case_id = uuid.UUID(case_id)
        async with CaseTagsService.with_session(role=role) as svc:
            await svc.remove_case_tag(parsed_case_id, tag_identifier)
            return _json(
                {"message": (f"Case tag {tag_identifier} removed from case {case_id}")}
            )
    except NoResultFound as e:
        raise ToolError(str(e)) from e
    except ValueError as e:
        raise ToolError(str(e)) from e
    except Exception as e:
        logger.error("Failed to remove case tag", error=str(e))
        raise ToolError(f"Failed to remove case tag: {e}") from None


# ---------------------------------------------------------------------------
# Case field tools
# ---------------------------------------------------------------------------


@mcp.tool()
async def list_case_fields(workspace_id: str) -> str:
    """List case field definitions in a workspace.

    Returns a JSON array of field objects with `id`, `type`, `description`,
    `nullable`, `default`, `reserved`, and `options`.
    """

    try:
        _, role = await _resolve_workspace_role(workspace_id)
        async with CaseFieldsService.with_session(role=role) as svc:
            columns = await svc.list_fields()
            field_schema = await svc.get_field_schema()
            return _json(
                [
                    _case_field_payload(column, field_schema=field_schema)
                    for column in columns
                ]
            )
    except ValueError as e:
        raise ToolError(str(e)) from e
    except Exception as e:
        logger.error("Failed to list case fields", error=str(e))
        raise ToolError(f"Failed to list case fields: {e}") from None


@mcp.tool()
async def create_case_field(
    workspace_id: str,
    name: str,
    type: str,
    options: str | None = None,
) -> str:
    """Create a case field definition.

    Args:
        workspace_id: The workspace ID.
        name: Field name / column id. Schema: string matching
            `^[a-zA-Z_][a-zA-Z0-9_]*$`.
        type: Uppercase SqlType value: TEXT, INTEGER, NUMERIC, DATE, BOOLEAN,
            TIMESTAMP, TIMESTAMPTZ, JSONB, UUID, SELECT, or MULTI_SELECT.
        options: Optional JSON array string of strings, e.g.
            '["low","medium","high"]'. Required for SELECT and MULTI_SELECT,
            and invalid for all other field types. Schema: list[str].

    Returns a confirmation message.
    """

    try:
        _, role = await _resolve_workspace_role(workspace_id)
        parsed_options = _parse_json_arg(options, "options")
        parsed_type = _parse_sql_type_arg(type)
        async with CaseFieldsService.with_session(role=role) as svc:
            await svc.create_field(
                CaseFieldCreate(name=name, type=parsed_type, options=parsed_options)
            )
            return _json({"message": f"Case field {name} created successfully"})
    except ToolError:
        raise
    except ValueError as e:
        raise ToolError(str(e)) from e
    except Exception as e:
        logger.error("Failed to create case field", error=str(e))
        raise ToolError(f"Failed to create case field: {e}") from None


@mcp.tool()
async def update_case_field(
    workspace_id: str,
    field_id: str,
    name: str | None = None,
    type: str | None = None,
    options: str | None = None,
) -> str:
    """Update a case field definition.

    Args:
        workspace_id: The workspace ID.
        field_id: Existing field id from `list_case_fields` (field name, not UUID).
        name: Optional new field name. Schema: string matching
            `^[a-zA-Z_][a-zA-Z0-9_]*$`.
        type: Optional uppercase SqlType value.
        options: Optional JSON array string of strings. Use `[]` to clear select
            options. Schema: list[str].

    Returns a confirmation message.
    """

    try:
        _, role = await _resolve_workspace_role(workspace_id)
        options_provided = options is not None
        parsed_options = _parse_json_arg(options, "options")
        parsed_type = _parse_sql_type_arg(type) if type is not None else None
        async with CaseFieldsService.with_session(role=role) as svc:
            await svc.update_field(
                field_id,
                _build_case_field_update_params(
                    name=name,
                    type=parsed_type,
                    options=parsed_options,
                    options_provided=options_provided,
                ),
            )
            return _json({"message": f"Case field {field_id} updated successfully"})
    except ToolError:
        raise
    except ValueError as e:
        raise ToolError(str(e)) from e
    except Exception as e:
        logger.error("Failed to update case field", error=str(e))
        raise ToolError(f"Failed to update case field: {e}") from None


@mcp.tool()
async def delete_case_field(workspace_id: str, field_id: str) -> str:
    """Delete a case field definition.

    Args:
        workspace_id: The workspace ID.
        field_id: Existing field id from `list_case_fields` (field name, not UUID).

    Returns a confirmation message.
    """

    try:
        _, role = await _resolve_workspace_role(workspace_id)
        async with CaseFieldsService.with_session(role=role) as svc:
            await svc.delete_field(field_id)
            return _json({"message": f"Case field {field_id} deleted successfully"})
    except ValueError as e:
        raise ToolError(str(e)) from e
    except Exception as e:
        logger.error("Failed to delete case field", error=str(e))
        raise ToolError(f"Failed to delete case field: {e}") from None


# ---------------------------------------------------------------------------
# Tables, cases, variables, and secrets metadata tools
# ---------------------------------------------------------------------------


@mcp.tool()
async def list_tables(workspace_id: str) -> str:
    """List workspace tables."""

    try:
        _, role = await _resolve_workspace_role(workspace_id)
        async with TablesService.with_session(role=role) as svc:
            tables = await svc.list_tables()
            return _json(
                [{"id": str(table.id), "name": table.name} for table in tables]
            )
    except ValueError as e:
        raise ToolError(str(e)) from e
    except Exception as e:
        logger.error("Failed to list tables", error=str(e))
        raise ToolError(f"Failed to list tables: {e}") from None


@mcp.tool()
async def create_table(
    workspace_id: str,
    name: str,
    columns_json: str | None = None,
) -> str:
    """Create a table with optional columns.

    Args:
        workspace_id: The workspace ID.
        name: Table name.
        columns_json: Optional JSON array of column definitions. Each column
            object schema is:
            `{"name": str, "type": SqlType, "nullable": bool?, "default": any?,`
            ` "options": list[str]?}`.
            Column type must be UPPERCASE — one of: TEXT, INTEGER, NUMERIC,
            DATE, BOOLEAN, TIMESTAMP, TIMESTAMPTZ, JSONB, UUID, SELECT,
            MULTI_SELECT. `options` are only valid for SELECT or MULTI_SELECT.
            Example:
            `'[{"name":"severity","type":"SELECT","options":["low","high"]}]'`.

    Returns JSON with the new table's id and name.
    """

    try:
        _, role = await _resolve_workspace_role(workspace_id)
        columns = _parse_json_arg(columns_json, "columns_json") or []
        params = TableCreate(name=name, columns=columns)
        async with TablesService.with_session(role=role) as svc:
            table = await svc.create_table(params)
            return _json({"id": str(table.id), "name": table.name})
    except ToolError:
        raise
    except ValueError as e:
        raise ToolError(str(e)) from e
    except Exception as e:
        logger.error("Failed to create table", error=str(e))
        raise ToolError(f"Failed to create table: {e}") from None


@mcp.tool()
async def get_table(workspace_id: str, table_id: str) -> str:
    """Get table definition and index metadata."""

    try:
        _, role = await _resolve_workspace_role(workspace_id)
        async with TablesService.with_session(role=role) as svc:
            table = await svc.get_table(uuid.UUID(table_id))
            index_columns = await svc.get_index(table)
            return _json(
                {
                    "id": str(table.id),
                    "name": table.name,
                    "columns": [
                        {
                            "id": str(column.id),
                            "name": column.name,
                            "type": SqlType(column.type).value,
                            "nullable": column.nullable,
                            "default": column.default,
                            "is_index": column.name in index_columns,
                            "options": column.options,
                        }
                        for column in table.columns
                    ],
                }
            )
    except ValueError as e:
        raise ToolError(str(e)) from e
    except Exception as e:
        logger.error("Failed to get table", error=str(e))
        raise ToolError(f"Failed to get table: {e}") from None


@mcp.tool()
async def update_table(
    workspace_id: str,
    table_id: str,
    name: str | None = None,
) -> str:
    """Update table metadata."""

    try:
        _, role = await _resolve_workspace_role(workspace_id)
        async with TablesService.with_session(role=role) as svc:
            table = await svc.get_table(uuid.UUID(table_id))
            updated = await svc.update_table(table, TableUpdate(name=name))
            return _json({"id": str(updated.id), "name": updated.name})
    except ValueError as e:
        raise ToolError(str(e)) from e
    except Exception as e:
        logger.error("Failed to update table", error=str(e))
        raise ToolError(f"Failed to update table: {e}") from None


@mcp.tool()
async def insert_table_row(
    workspace_id: str,
    table_id: str,
    row_json: str,
    upsert: bool = False,
) -> str:
    """Insert a table row."""

    try:
        row_data = _parse_json_arg(row_json, "row_json")
        if not isinstance(row_data, dict):
            raise ToolError("row_json must decode to a JSON object")
        _, role = await _resolve_workspace_role(workspace_id)
        async with TablesService.with_session(role=role) as svc:
            table = await svc.get_table(uuid.UUID(table_id))
            row = await svc.insert_row(
                table, TableRowInsert(data=row_data, upsert=upsert)
            )
            return _json(row)
    except ToolError:
        raise
    except ValueError as e:
        raise ToolError(str(e)) from e
    except Exception as e:
        logger.error("Failed to insert table row", error=str(e))
        raise ToolError(f"Failed to insert table row: {e}") from None


@mcp.tool()
async def update_table_row(
    workspace_id: str,
    table_id: str,
    row_id: str,
    row_json: str,
) -> str:
    """Update a table row."""

    try:
        row_data = _parse_json_arg(row_json, "row_json")
        if not isinstance(row_data, dict):
            raise ToolError("row_json must decode to a JSON object")
        _, role = await _resolve_workspace_role(workspace_id)
        async with TablesService.with_session(role=role) as svc:
            table = await svc.get_table(uuid.UUID(table_id))
            row = await svc.update_row(table, uuid.UUID(row_id), row_data)
            return _json(row)
    except ToolError:
        raise
    except ValueError as e:
        raise ToolError(str(e)) from e
    except Exception as e:
        logger.error("Failed to update table row", error=str(e))
        raise ToolError(f"Failed to update table row: {e}") from None


@mcp.tool()
async def search_table_rows(
    workspace_id: str,
    table_id: str,
    search_term: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> str:
    """Search rows in a table."""

    try:
        _, role = await _resolve_workspace_role(workspace_id)
        limit = max(1, min(limit, 1000))
        _ = max(0, offset)  # offset reserved for future cursor support
        async with TablesService.with_session(role=role) as svc:
            table = await svc.get_table(uuid.UUID(table_id))
            page = await svc.search_rows(
                table,
                search_term=search_term,
                limit=limit,
            )
            return _json(page.items)
    except ValueError as e:
        raise ToolError(str(e)) from e
    except Exception as e:
        logger.error("Failed to search table rows", error=str(e))
        raise ToolError(f"Failed to search table rows: {e}") from None


@mcp.tool()
async def import_csv(
    workspace_id: str,
    csv_content: str,
    table_name: str | None = None,
) -> str:
    """Create a new table from CSV text with auto-inferred schema.

    Args:
        workspace_id: The workspace ID.
        csv_content: Raw CSV text (with header row).
        table_name: Optional table name (auto-generated if omitted).

    Returns JSON with table id, name, rows_inserted, and column_mapping
    (original header name -> normalized column name).
    """

    try:
        _, role = await _resolve_workspace_role(workspace_id)
        async with TablesService.with_session(role=role) as svc:
            table, rows_inserted, inferred_columns = await svc.import_table_from_csv(
                contents=csv_content.encode(),
                table_name=table_name,
            )
            column_mapping = {col.original_name: col.name for col in inferred_columns}
            return _json(
                {
                    "id": str(table.id),
                    "name": table.name,
                    "rows_inserted": rows_inserted,
                    "column_mapping": column_mapping,
                }
            )
    except ValueError as e:
        raise ToolError(str(e)) from e
    except Exception as e:
        logger.error("Failed to import CSV", error=str(e))
        raise ToolError(f"Failed to import CSV: {e}") from None


@mcp.tool()
async def export_csv(
    workspace_id: str,
    table_id: str,
    include_header: bool = True,
) -> str:
    """Export table data as CSV text.

    Args:
        workspace_id: The workspace ID.
        table_id: The table ID.
        include_header: Whether to include a header row (default True).

    Returns the CSV text as a string. System columns (id, created_at,
    updated_at) are excluded from the export.
    """

    SYSTEM_COLUMNS = {"id", "created_at", "updated_at"}

    try:
        _, role = await _resolve_workspace_role(workspace_id)
        async with TablesService.with_session(role=role) as svc:
            table = await svc.get_table(uuid.UUID(table_id))
            columns = [c.name for c in table.columns if c.name not in SYSTEM_COLUMNS]
            if not columns:
                return ""

            output = StringIO()
            writer = csv.DictWriter(output, fieldnames=columns, extrasaction="ignore")
            if include_header:
                writer.writeheader()

            cursor: str | None = None
            while True:
                page = await svc.search_rows(
                    table,
                    limit=config.TRACECAT__LIMIT_CURSOR_MAX,
                    cursor=cursor,
                )
                for row in page.items:
                    writer.writerow(row)
                if not page.has_more or page.next_cursor is None:
                    break
                cursor = page.next_cursor

            return output.getvalue()
    except ValueError as e:
        raise ToolError(str(e)) from e
    except Exception as e:
        logger.error("Failed to export CSV", error=str(e))
        raise ToolError(f"Failed to export CSV: {e}") from None


@mcp.tool()
async def list_variables(
    workspace_id: str,
    environment: str = DEFAULT_SECRETS_ENVIRONMENT,
) -> str:
    """List workspace variables."""

    try:
        _, role = await _resolve_workspace_role(workspace_id)
        async with VariablesService.with_session(role=role) as svc:
            variables = await svc.list_variables(environment=environment)
            return _json(
                [
                    {
                        "id": str(variable.id),
                        "name": variable.name,
                        "description": variable.description,
                        "environment": variable.environment,
                        "keys": sorted(variable.values.keys()),
                    }
                    for variable in variables
                ]
            )
    except ValueError as e:
        raise ToolError(str(e)) from e
    except Exception as e:
        logger.error("Failed to list variables", error=str(e))
        raise ToolError(f"Failed to list variables: {e}") from None


@mcp.tool()
async def get_variable(
    workspace_id: str,
    variable_name: str,
    environment: str = DEFAULT_SECRETS_ENVIRONMENT,
) -> str:
    """Get a workspace variable."""

    try:
        _, role = await _resolve_workspace_role(workspace_id)
        async with VariablesService.with_session(role=role) as svc:
            variable = await svc.get_variable_by_name(
                variable_name, environment=environment
            )
            return _json(
                {
                    "id": str(variable.id),
                    "name": variable.name,
                    "description": variable.description,
                    "environment": variable.environment,
                    "values": variable.values,
                }
            )
    except ValueError as e:
        raise ToolError(str(e)) from e
    except Exception as e:
        logger.error("Failed to get variable", error=str(e))
        raise ToolError(f"Failed to get variable: {e}") from None


@mcp.tool()
async def list_secrets_metadata(
    workspace_id: str,
    environment: str = DEFAULT_SECRETS_ENVIRONMENT,
) -> str:
    """List secret metadata without secret values."""

    try:
        _, role = await _resolve_workspace_role(workspace_id)
        result: list[dict[str, Any]] = []
        async with SecretsService.with_session(role=role) as svc:
            workspace_secrets = await svc.list_secrets()
            for secret in workspace_secrets:
                if secret.environment != environment:
                    continue
                keys = [kv.key for kv in svc.decrypt_keys(secret.encrypted_keys)]
                result.append(
                    {
                        "id": str(secret.id),
                        "name": secret.name,
                        "type": secret.type,
                        "environment": secret.environment,
                        "keys": keys,
                        "tags": secret.tags,
                    }
                )
            return _json(result)
    except ToolError:
        raise
    except ValueError as e:
        raise ToolError(str(e)) from e
    except Exception as e:
        logger.error("Failed to list secrets metadata", error=str(e))
        raise ToolError(f"Failed to list secrets metadata: {e}") from None


@mcp.tool()
async def get_secret_metadata(
    workspace_id: str,
    secret_name: str,
    environment: str = DEFAULT_SECRETS_ENVIRONMENT,
) -> str:
    """Get secret metadata by name without secret values."""

    try:
        _, role = await _resolve_workspace_role(workspace_id)
        async with SecretsService.with_session(role=role) as svc:
            try:
                secret = await svc.get_secret_by_name(
                    secret_name, environment=environment
                )
            except TracecatNotFoundError:
                raise ToolError(f"Secret {secret_name!r} not found") from None
            return _json(
                {
                    "id": str(secret.id),
                    "name": secret.name,
                    "type": secret.type,
                    "environment": secret.environment,
                    "keys": [kv.key for kv in svc.decrypt_keys(secret.encrypted_keys)],
                    "tags": secret.tags,
                }
            )
    except ToolError:
        raise
    except ValueError as e:
        raise ToolError(str(e)) from e
    except Exception as e:
        logger.error("Failed to get secret metadata", error=str(e))
        raise ToolError(f"Failed to get secret metadata: {e}") from None


async def _build_agent_preset_authoring_context(role: Any) -> dict[str, Any]:
    """Build authoring context for MCP preset creation."""
    async with AgentManagementService.with_session(role=role) as svc:
        models = await svc.list_models()
        default_model = await svc.get_default_model()
        provider_status_org = await svc.get_providers_status()
        provider_status_workspace = await svc.get_workspace_providers_status()

    async with VariablesService.with_session(role=role) as svc:
        variables = await svc.list_variables(environment=DEFAULT_SECRETS_ENVIRONMENT)

    workspace_inventory = await _load_secret_inventory(role)
    models_by_provider: dict[str, list[str]] = defaultdict(list)
    for model_name, model in sorted(models.items(), key=lambda item: item[0]):
        provider = cast(str, model.model_dump(mode="json")["provider"])
        models_by_provider[provider].append(model_name)
    agent_credentials = {
        "providers": [
            {
                "provider": provider,
                "configured_org": provider_status_org.get(provider, False),
                "configured_workspace": provider_status_workspace.get(provider, False),
                "ready_for_agent_presets": provider_status_workspace.get(
                    provider, False
                ),
                "models": model_names,
            }
            for provider, model_names in sorted(models_by_provider.items())
        ],
        "default_model_workspace_ready": (
            provider_status_workspace.get(
                cast(str, models[default_model].model_dump(mode="json")["provider"]),
                False,
            )
            if default_model in models
            else None
        ),
        "notes": [
            "Agent preset sessions require workspace-scoped credentials for the selected provider.",
            "configured_org may still be useful for other agent flows, but it is not sufficient for run_agent_preset.",
        ],
    }

    return {
        "default_model": default_model,
        "models": [
            model.model_dump(mode="json")
            for _, model in sorted(models.items(), key=lambda item: item[0])
        ],
        "provider_status_org": provider_status_org,
        "provider_status_workspace": provider_status_workspace,
        "agent_credentials": agent_credentials,
        "workspace_variables": [
            {
                "name": variable.name,
                "keys": sorted(variable.values.keys()),
                "environment": variable.environment,
            }
            for variable in variables
        ],
        "workspace_secret_hints": [
            {
                "name": secret_name,
                "keys": sorted(keys),
                "environment": DEFAULT_SECRETS_ENVIRONMENT,
            }
            for secret_name, keys in sorted(workspace_inventory.items())
        ],
        "integrations": await _build_integrations_inventory(role),
        "output_type_context": _build_output_type_context(),
        "notes": [
            "provider_status_org describes organization-scoped model credentials.",
            "provider_status_workspace describes workspace-scoped model credentials used by some agent flows.",
        ],
    }


@mcp.tool()
async def list_integrations(workspace_id: str) -> str:
    """List workspace integrations useful for workflow and preset authoring."""

    try:
        _, role = await _resolve_workspace_role(workspace_id)
        return _json(await _build_integrations_inventory(role))
    except ToolError:
        raise
    except ValueError as e:
        raise ToolError(str(e)) from e
    except Exception as e:
        logger.error("Failed to list integrations", error=str(e))
        raise ToolError(f"Failed to list integrations: {e}") from None


@mcp.tool()
async def get_agent_preset_authoring_context(workspace_id: str) -> str:
    """Get models, integrations, output_type guidance, and other preset authoring context."""

    try:
        _, role = await _resolve_workspace_role(workspace_id)
        return _json(await _build_agent_preset_authoring_context(role))
    except ToolError:
        raise
    except ValueError as e:
        raise ToolError(str(e)) from e
    except Exception as e:
        logger.error("Failed to build agent preset authoring context", error=str(e))
        raise ToolError(
            f"Failed to build agent preset authoring context: {e}"
        ) from None


@mcp.tool()
async def create_agent_preset(
    workspace_id: str,
    name: str,
    slug: str | None = None,
    description: str | None = None,
    instructions: str | None = None,
    model_name: str | None = None,
    model_provider: str | None = None,
    base_url: str | None = None,
    output_type: OutputType | None = None,
    actions: list[str] | None = None,
    namespaces: list[str] | None = None,
    tool_approvals: dict[str, bool] | None = None,
    mcp_integration_ids: list[str] | None = None,
    retries: int | None = None,
    enable_internet_access: bool | None = None,
) -> str:
    """Create an agent preset in the selected workspace."""

    try:
        _, role = await _resolve_workspace_role(workspace_id)
        (
            resolved_model_name,
            resolved_model_provider,
        ) = await _resolve_agent_preset_model(
            role,
            model_name=model_name,
            model_provider=model_provider,
        )
        create_data: dict[str, Any] = {
            "name": name,
            "model_name": resolved_model_name,
            "model_provider": resolved_model_provider,
        }
        optional_fields = {
            "slug": slug,
            "description": description,
            "instructions": instructions,
            "base_url": base_url,
            "output_type": output_type,
            "actions": actions,
            "namespaces": namespaces,
            "tool_approvals": tool_approvals,
            "mcp_integrations": mcp_integration_ids,
            "retries": retries,
            "enable_internet_access": enable_internet_access,
        }
        create_data.update(
            {
                field: value
                for field, value in optional_fields.items()
                if value is not None
            }
        )
        params = AgentPresetCreate.model_validate(create_data)
        async with AgentPresetService.with_session(role=role) as svc:
            preset = await svc.create_preset(params)
        return _json(AgentPresetRead.model_validate(preset).model_dump(mode="json"))
    except ToolError:
        raise
    except ValidationError as e:
        raise ToolError(str(e)) from e
    except TracecatValidationError as e:
        raise ToolError(str(e)) from e
    except Exception as e:
        logger.error("Failed to create agent preset", error=str(e))
        raise ToolError(f"Failed to create agent preset: {e}") from None


def _parse_iso8601_duration(duration_str: str) -> timedelta:
    """Parse a simple ISO 8601 duration string into a timedelta.

    Supports formats like PT1H, PT30M, P1D, PT1H30M, P1DT12H, etc.
    """
    pattern = r"P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?"
    match = re.fullmatch(pattern, duration_str)
    if not match:
        raise ValueError(f"Invalid ISO 8601 duration: {duration_str}")

    days = int(match.group(1) or 0)
    hours = int(match.group(2) or 0)
    minutes = int(match.group(3) or 0)
    seconds = int(match.group(4) or 0)
    return timedelta(days=days, hours=hours, minutes=minutes, seconds=seconds)


# ── Agent Presets ────────────────────────────────────────────────────────────


async def _collect_agent_response(
    session_id: uuid.UUID,
    workspace_id: uuid.UUID,
    timeout: float,
    last_id: str,
) -> str:
    """Poll Redis agent stream and return text output or pending approval state."""
    stream = await AgentStream.new(session_id, workspace_id)
    text_parts: list[str] = []
    approval_items: dict[str, dict[str, Any]] = {}

    async def _not_disconnected() -> bool:
        return False

    try:
        async with asyncio.timeout(timeout):
            async for event in stream._stream_events(
                _not_disconnected, last_id=last_id
            ):
                match event:
                    case StreamEnd():
                        break
                    case StreamError(error=err):
                        raise ToolError(f"Agent error: {err}")
                    case StreamDelta(event=delta) if isinstance(
                        delta, UnifiedStreamEvent
                    ):
                        if delta.type == StreamEventType.TEXT_DELTA and delta.text:
                            text_parts.append(delta.text)
                        elif delta.type == StreamEventType.APPROVAL_REQUEST:
                            for item in delta.approval_items or []:
                                approval_items[item.id] = {
                                    "tool_call_id": item.id,
                                    "tool_name": item.name,
                                    "args": item.input,
                                }
    except TimeoutError:
        raise ToolError(f"Agent response timed out after {timeout}s") from None

    if approval_items:
        return _json(
            {
                "status": "awaiting_approval",
                "session_id": str(session_id),
                "items": list(approval_items.values()),
                "partial_output": "".join(text_parts) or None,
            }
        )

    return "".join(text_parts) or "(no output)"


@mcp.tool()
async def list_agent_presets(workspace_id: str) -> str:
    """List agent presets in a workspace with their capabilities.

    Returns each preset's slug, name, description, model, instructions,
    configured actions (tools), and namespaces.
    """
    try:
        _, role = await _resolve_workspace_role(workspace_id)
        async with AgentPresetService.with_session(role=role) as svc:
            presets = await svc.list_presets()
        return _json(
            [
                {
                    "id": str(p.id),
                    "slug": p.slug,
                    "name": p.name,
                    "description": p.description,
                    "model_name": p.model_name,
                    "model_provider": p.model_provider,
                    "instructions": p.instructions,
                    "actions": p.actions,
                    "namespaces": p.namespaces,
                }
                for p in presets
            ]
        )
    except ToolError:
        raise
    except Exception as e:
        logger.error("Failed to list agent presets", error=str(e))
        raise ToolError(f"Failed to list agent presets: {e}") from None


@mcp.tool()
async def run_agent_preset(
    workspace_id: str,
    preset_slug: str,
    prompt: str,
    preset_version: int | None = None,
    timeout_seconds: int = 120,
) -> str:
    """Run an agent preset with a prompt and return text or approval status.

    Creates an ephemeral session, triggers the agent workflow, and waits
    for the response. The agent has access to all tools configured on the preset.

    Args:
        workspace_id: The workspace ID (from list_workspaces).
        preset_slug: Slug of the agent preset to run (from list_agent_presets).
        prompt: The user prompt to send to the agent.
        preset_version: Optional preset version number to pin.
        timeout_seconds: Max seconds to wait for response (default 120, max 300).

    Returns:
        Plain text agent response, or a JSON object with
        ``status="awaiting_approval"`` when a tool call is pending review.
    """
    try:
        _, role = await _resolve_workspace_role(workspace_id)
        timeout = min(max(timeout_seconds, 10), 300)

        # Resolve preset
        async with AgentPresetService.with_session(role=role) as svc:
            preset = await svc.get_preset_by_slug(preset_slug)
            if not preset:
                raise ToolError(f"Agent preset '{preset_slug}' not found")
            version = await svc.resolve_agent_preset_version(
                slug=preset_slug,
                preset_version=preset_version,
            )

        # Create ephemeral session and run turn
        async with AgentSessionService.with_session(role=role) as svc:
            session = await svc.create_session(
                AgentSessionCreate(
                    title=f"MCP: {prompt[:50]}",
                    entity_type=AgentSessionEntity.AGENT_PRESET,
                    entity_id=preset.id,
                    agent_preset_id=preset.id,
                    agent_preset_version_id=version.id,
                )
            )
            # BasicChatRequest is handled at runtime by run_turn's match statement
            # but not included in the ChatRequest type alias
            await svc.run_turn(
                session.id, cast(ChatRequest, BasicChatRequest(message=prompt))
            )

        # Collect text from Redis stream
        if role.workspace_id is None:
            raise ToolError("Workspace ID is required")
        start_id = session.last_stream_id or "0-0"
        return await _collect_agent_response(
            session.id,
            role.workspace_id,
            timeout,
            start_id,
        )
    except ToolError:
        raise
    except Exception as e:
        logger.error("Failed to run agent preset", error=str(e))
        raise ToolError(f"Failed to run agent preset: {e}") from None
