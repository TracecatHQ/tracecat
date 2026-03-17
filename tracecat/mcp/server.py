"""Standalone MCP server for Tracecat workflow management.

Exposes workflow operations to external MCP clients (Claude Desktop, Cursor, etc.).
Users authenticate via their existing Tracecat OIDC login.
"""

from __future__ import annotations

import asyncio
import base64
import csv
import hashlib
import json
import re
import uuid
from collections import defaultdict, deque
from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from io import StringIO
from pathlib import PurePosixPath
from typing import Any, Literal, cast, get_args

import orjson
import sqlalchemy as sa
import yaml
from fastmcp import Context, FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.server.middleware.error_handling import ErrorHandlingMiddleware
from fastmcp.server.middleware.logging import LoggingMiddleware
from fastmcp.server.middleware.rate_limiting import RateLimitingMiddleware
from google.protobuf.json_format import MessageToDict
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)
from redis.asyncio import Redis as AsyncRedis
from slugify import slugify
from sqlalchemy import delete, select
from sqlalchemy.exc import NoResultFound
from temporalio.client import WorkflowExecutionStatus
from tracecat_registry import RegistryOAuthSecret, RegistrySecret

from tracecat import config
from tracecat.agent.common.stream_types import StreamEventType, UnifiedStreamEvent
from tracecat.agent.preset.schemas import (
    AgentPresetCreate,
    AgentPresetRead,
    AgentPresetUpdate,
)
from tracecat.agent.preset.service import AgentPresetService
from tracecat.agent.schemas import ModelSelection
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
    get_token_identity,
    list_workspaces_for_request,
    resolve_role_for_request,
)
from tracecat.mcp.config import (
    TRACECAT_MCP__FILE_TRANSFER_URL_EXPIRY_SECONDS,
    TRACECAT_MCP__RATE_LIMIT_BURST,
    TRACECAT_MCP__RATE_LIMIT_RPS,
)
from tracecat.mcp.middleware import (
    MCPInputSizeLimitMiddleware,
    MCPTimeoutMiddleware,
    WatchtowerMonitorMiddleware,
    get_mcp_client_id,
)
from tracecat.mcp.schemas import (
    MCPPaginatedResponse,
    MCPTruncationInfo,
    MCPTruncationSummary,
)
from tracecat.pagination import CursorPaginatedResponse, CursorPaginationParams
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
from tracecat.storage import blob
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
    source_id: str | None,
    model_name: str | None,
    model_provider: str | None,
) -> ModelSelection:
    """Resolve explicit or default model inputs for preset creation."""
    async with AgentManagementService.with_session(role=role) as svc:
        list_models = getattr(svc, "list_models", None)
        provider_status_org: dict[str, bool] | None = None
        if get_provider_status := getattr(svc, "get_providers_status", None):
            provider_status_org = await get_provider_status()
        if (
            source_id is not None
            or model_name is not None
            or model_provider is not None
        ):
            if not model_name or not model_provider:
                raise ToolError(
                    "model_name and model_provider must both be provided when setting an explicit model"
                )
            parsed_source_id: uuid.UUID | None = None
            if source_id is not None:
                try:
                    parsed_source_id = uuid.UUID(source_id)
                except ValueError as exc:
                    raise ToolError("Invalid source_id") from exc
            if callable(list_models):
                models = await _list_authoring_models(
                    svc, workspace_id=role.workspace_id
                )
                matches = [
                    item
                    for item in models
                    if item["model_name"] == model_name
                    and item["model_provider"] == model_provider
                    and (
                        parsed_source_id is None
                        or item.get("source_id") == str(parsed_source_id)
                    )
                ]
                if not matches:
                    source_hint = f" for source_id '{source_id}'" if source_id else ""
                    raise ToolError(f"Model '{model_name}' not found{source_hint}")
                if len(matches) > 1:
                    raise ToolError(
                        f"Model '{model_provider}/{model_name}' is ambiguous across multiple sources. "
                        "Pass source_id to select the desired source-backed model."
                    )
                selection = ModelSelection.model_validate(matches[0])
            else:
                model_config = await svc.get_model_config(model_name)
                actual_provider = str(model_config.provider)
                if actual_provider != model_provider:
                    raise ToolError(
                        f"Model '{model_name}' belongs to provider '{actual_provider}', not '{model_provider}'"
                    )
                selection = ModelSelection(
                    source_id=parsed_source_id,
                    model_provider=model_provider,
                    model_name=model_name,
                )
        else:
            if not (default_model := await svc.get_default_model()):
                raise ToolError(
                    "No default model configured for this organization. Set one before creating a preset without explicit model fields."
                )
            if isinstance(default_model, str):
                selection = ModelSelection(
                    source_id=None,
                    model_provider=str(
                        (await svc.get_model_config(default_model)).provider
                    ),
                    model_name=default_model,
                )
            else:
                selection = ModelSelection(
                    source_id=default_model.source_id,
                    model_provider=default_model.model_provider,
                    model_name=default_model.model_name,
                )
            if callable(list_models) and role.workspace_id is not None:
                models = await _list_authoring_models(
                    svc, workspace_id=role.workspace_id
                )
                matches = [
                    item
                    for item in models
                    if _authoring_model_matches_selection(item, selection)
                ]
                if not matches:
                    raise ToolError(
                        f"Model {selection.model_provider}/{selection.model_name} is not enabled"
                    )
            elif require_enabled := getattr(
                svc, "require_enabled_model_selection", None
            ):
                try:
                    await require_enabled(selection, workspace_id=role.workspace_id)
                except TracecatNotFoundError as exc:
                    raise ToolError(str(exc)) from exc
        if selection.source_id is not None:
            return selection
        if (
            provider_status_org is not None
            and selection.model_provider in provider_status_org
            and not provider_status_org[selection.model_provider]
        ):
            raise ToolError(
                f"Credentials for provider '{selection.model_provider}' are not configured"
            )
        return selection


async def _list_authoring_models(
    svc: Any, *, workspace_id: uuid.UUID | None
) -> list[dict[str, Any]]:
    list_models = svc.list_models
    try:
        raw_models = await list_models(workspace_id=workspace_id)
    except TypeError:
        raw_models = await list_models()

    normalized: list[dict[str, Any]] = []
    if isinstance(raw_models, dict):
        for fallback_name, model in sorted(raw_models.items()):
            payload = model.model_dump(mode="json")
            model_name = str(
                payload.get("model_name") or payload.get("name") or fallback_name
            )
            model_provider = str(
                payload.get("model_provider") or payload.get("provider") or ""
            )
            source_id = payload.get("source_id")
            normalized.append(
                {
                    **payload,
                    "name": payload.get("name") or model_name,
                    "provider": payload.get("provider") or model_provider,
                    "model_name": model_name,
                    "model_provider": model_provider,
                    "source_id": str(source_id) if source_id is not None else None,
                }
            )
        return normalized

    for model in raw_models:
        payload = model.model_dump(mode="json")
        model_name = str(payload.get("model_name") or payload.get("name") or "")
        model_provider = str(
            payload.get("model_provider") or payload.get("provider") or ""
        )
        source_id = payload.get("source_id")
        normalized.append(
            {
                **payload,
                "name": payload.get("name") or model_name,
                "provider": payload.get("provider") or model_provider,
                "model_name": model_name,
                "model_provider": model_provider,
                "source_id": str(source_id) if source_id is not None else None,
            }
        )
    normalized.sort(
        key=lambda item: (
            str(item.get("source_name") or ""),
            str(item["model_provider"]),
            str(item["model_name"]),
            str(item.get("source_id") or ""),
        )
    )
    return normalized


def _authoring_model_matches_selection(
    model: dict[str, Any], selection: ModelSelection
) -> bool:
    """Check whether an authoring model payload matches a model selection."""
    return (
        str(model.get("model_provider")) == selection.model_provider
        and str(model.get("model_name")) == selection.model_name
        and model.get("source_id")
        == (str(selection.source_id) if selection.source_id is not None else None)
    )


def _default_model_selection_from_authoring_models(
    default_model: object | None,
    models: list[dict[str, Any]],
) -> tuple[str | None, ModelSelection | None]:
    if default_model is None:
        return None, None
    if isinstance(default_model, str):
        matches = [
            model for model in models if str(model.get("model_name")) == default_model
        ]
        if len(matches) == 1:
            return default_model, ModelSelection.model_validate(matches[0])
        return default_model, None
    if callable(model_dump := getattr(default_model, "model_dump", None)):
        default_model = model_dump(mode="json")
    try:
        selection = ModelSelection.model_validate(default_model)
    except ValidationError:
        return None, None
    else:
        return selection.model_name, selection


def _compact_authoring_model(model: dict[str, Any]) -> dict[str, Any]:
    """Project authoring models to the minimal selection fields."""
    compact = {
        "name": str(model.get("name") or model["model_name"]),
        "provider": str(model.get("provider") or model["model_provider"]),
        "model_name": str(model["model_name"]),
        "model_provider": str(model["model_provider"]),
        "source_id": model.get("source_id"),
        "source_name": model.get("source_name"),
    }
    if source_type := model.get("source_type"):
        compact["source_type"] = source_type
    return compact


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


class WorkflowFileOperation(StrEnum):
    CREATE = "create"
    UPDATE = "update"


class WorkflowFileArtifact(BaseModel):
    artifact_id: uuid.UUID
    organization_id: uuid.UUID
    workspace_id: uuid.UUID
    client_id: str
    session_id: str
    operation: WorkflowFileOperation
    relative_path: str
    folder_path: str | None = Field(default=None)
    blob_key: str
    workflow_id: uuid.UUID | None = Field(default=None)
    update_mode: Literal["replace", "patch"] = Field(default="patch")
    expires_at: datetime
    used: bool = Field(default=False)
    sha256: str | None = Field(default=None)


class TemplateFileArtifact(BaseModel):
    artifact_id: uuid.UUID
    organization_id: uuid.UUID
    workspace_id: uuid.UUID
    client_id: str
    session_id: str
    relative_path: str
    blob_key: str
    expires_at: datetime
    used: bool = Field(default=False)
    sha256: str | None = Field(default=None)


_WORKFLOW_FILE_ARTIFACT_KEY_PREFIX = "mcp:workflow-artifacts"
_TEMPLATE_FILE_ARTIFACT_KEY_PREFIX = "mcp:template-artifacts"
_WORKFLOW_FILE_ALLOWED_EXTENSIONS = {".yaml", ".yml"}
_WORKFLOW_FILE_WARNING = (
    "Workflow file tools use staged blob transfers for remote MCP clients. "
    "Download URLs and upload artifacts are short-lived and bound to the "
    "requesting client/session."
)
_TEMPLATE_FILE_WARNING = (
    "Template validation only supports staged uploads for remote MCP clients. "
    "Local filesystem paths are not supported."
)
_CSV_FILE_WARNING = (
    "CSV exports are delivered through staged blob downloads for remote MCP "
    "clients. Local filesystem export/import paths are not supported."
)
_INLINE_WORKFLOW_YAML_MAX_BYTES = 128 * 1024
_workflow_artifact_redis: AsyncRedis | None = None


def _mcp_file_transfer_ttl_seconds() -> int:
    """Return the TTL for staged MCP file transfer URLs and artifacts."""
    return TRACECAT_MCP__FILE_TRANSFER_URL_EXPIRY_SECONDS


def _inline_workflow_yaml_max_bytes() -> int:
    """Return the maximum inline workflow YAML size."""
    return _INLINE_WORKFLOW_YAML_MAX_BYTES


def _get_workflow_artifact_redis() -> AsyncRedis:
    """Get the Redis client used for workflow file artifact metadata."""
    global _workflow_artifact_redis
    if _workflow_artifact_redis is None:
        _workflow_artifact_redis = AsyncRedis.from_url(config.REDIS_URL)
    return _workflow_artifact_redis


def _workflow_artifact_redis_key(artifact_id: uuid.UUID | str) -> str:
    """Build the Redis key for a workflow file artifact."""
    return f"{_WORKFLOW_FILE_ARTIFACT_KEY_PREFIX}:{artifact_id}"


def _template_artifact_redis_key(artifact_id: uuid.UUID | str) -> str:
    """Build the Redis key for a template file artifact."""
    return f"{_TEMPLATE_FILE_ARTIFACT_KEY_PREFIX}:{artifact_id}"


def _current_mcp_client_id() -> str:
    """Return the current MCP client id when available."""
    try:
        return get_token_identity().client_id or "anonymous"
    except ValueError:
        return "anonymous"


def _get_context_session_id(ctx: Context | None) -> str:
    """Return the current MCP session id."""
    if ctx is None:
        raise ToolError("Workflow file tools require MCP context")
    return ctx.session_id


def _get_context_transport(ctx: Context | None) -> str:
    """Return the current transport name, defaulting to streamable-http."""
    if ctx is None or ctx.transport is None:
        return "streamable-http"
    return ctx.transport


def _require_remote_mcp_context(ctx: Context | None, *, tool_name: str) -> None:
    """Require the tool to be called over Tracecat's remote MCP transport."""
    if ctx is None:
        raise ToolError(f"{tool_name} requires MCP context")
    if _get_context_transport(ctx) != "streamable-http":
        raise ToolError(
            f"{tool_name} is only supported for remote streamable-http MCP clients"
        )


def _workflow_file_bucket() -> str:
    """Return the bucket used for staged workflow file blobs."""
    return config.TRACECAT__BLOB_STORAGE_BUCKET_WORKFLOW


def _template_file_bucket() -> str:
    """Return the bucket used for staged template file blobs."""
    return config.TRACECAT__BLOB_STORAGE_BUCKET_WORKFLOW


def _compute_sha256(content: bytes) -> str:
    """Compute the SHA-256 digest for the given bytes."""
    return hashlib.sha256(content).hexdigest()


def _normalize_workflow_file_relative_path(relative_path: str) -> str:
    """Validate and normalize a relative workflow file path."""
    raw = relative_path.replace("\\", "/").strip()
    if not raw:
        raise ToolError("relative_path is required")
    if raw.startswith("/"):
        raise ToolError("relative_path must be relative")

    path = PurePosixPath(raw)
    parts = path.parts
    if not parts:
        raise ToolError("relative_path must include a file name")
    if any(part in {"", ".", ".."} for part in parts):
        raise ToolError("relative_path cannot contain empty, '.' or '..' segments")
    if any(":" in part for part in parts):
        raise ToolError("relative_path cannot contain ':' segments")

    if path.suffix.lower() not in _WORKFLOW_FILE_ALLOWED_EXTENSIONS:
        raise ToolError("Workflow file path must end with .yaml or .yml")
    return path.as_posix()


def _infer_folder_path_from_relative_path(relative_path: str) -> str | None:
    """Infer the Tracecat workflow folder path from a relative file path."""
    path = PurePosixPath(relative_path)
    parent_parts = path.parts[:-1]
    if not parent_parts:
        return None
    return f"/{'/'.join(parent_parts)}/"


def _folder_path_to_relative_dir(folder_path: str | None) -> str:
    """Convert a materialized folder path to a relative directory string."""
    if not folder_path or folder_path == "/":
        return ""
    return folder_path.strip("/")


def _build_workflow_file_name(title: str, workflow_id: WorkflowUUID) -> str:
    """Build a stable local file name for a workflow."""
    title_slug = slugify(title, separator="-") or "workflow"
    return f"{title_slug}--{workflow_id.short()}.yaml"


def _build_workflow_relative_path(
    title: str,
    workflow_id: WorkflowUUID,
    folder_path: str | None,
) -> str:
    """Build the relative workflow file path from folder path and workflow metadata."""
    file_name = _build_workflow_file_name(title, workflow_id)
    if folder_dir := _folder_path_to_relative_dir(folder_path):
        return PurePosixPath(folder_dir, file_name).as_posix()
    return file_name


def _workflow_file_blob_key(
    workspace_id: uuid.UUID,
    session_id: str,
    artifact_id: uuid.UUID,
    file_name: str,
) -> str:
    """Build the blob storage key for a staged workflow file."""
    return f"{workspace_id}/mcp/workflow-files/{session_id}/{artifact_id}/{file_name}"


def _workflow_file_artifact_expires_at() -> datetime:
    """Return the expiry timestamp for a staged workflow artifact."""
    return datetime.now(UTC) + timedelta(seconds=_mcp_file_transfer_ttl_seconds())


def _workflow_file_artifact_remaining_seconds(expires_at: datetime) -> int:
    """Return the remaining TTL in seconds for an artifact."""
    remaining = int((expires_at - datetime.now(UTC)).total_seconds())
    return max(remaining, 1)


async def _store_workflow_file_artifact(artifact: WorkflowFileArtifact) -> None:
    """Persist workflow file artifact metadata in Redis."""
    redis = _get_workflow_artifact_redis()
    payload = orjson.dumps(artifact.model_dump(mode="json"))
    await redis.set(
        _workflow_artifact_redis_key(artifact.artifact_id),
        payload,
        ex=_workflow_file_artifact_remaining_seconds(artifact.expires_at),
    )


async def _load_workflow_file_artifact(
    artifact_id: str,
) -> WorkflowFileArtifact | None:
    """Load workflow file artifact metadata from Redis."""
    redis = _get_workflow_artifact_redis()
    raw = await redis.get(_workflow_artifact_redis_key(artifact_id))
    if raw is None:
        return None
    return WorkflowFileArtifact.model_validate(orjson.loads(raw))


async def _store_template_file_artifact(artifact: TemplateFileArtifact) -> None:
    """Persist template file artifact metadata in Redis."""
    redis = _get_workflow_artifact_redis()
    payload = orjson.dumps(artifact.model_dump(mode="json"))
    await redis.set(
        _template_artifact_redis_key(artifact.artifact_id),
        payload,
        ex=_workflow_file_artifact_remaining_seconds(artifact.expires_at),
    )


async def _load_template_file_artifact(
    artifact_id: str,
) -> TemplateFileArtifact | None:
    """Load template file artifact metadata from Redis."""
    redis = _get_workflow_artifact_redis()
    raw = await redis.get(_template_artifact_redis_key(artifact_id))
    if raw is None:
        return None
    return TemplateFileArtifact.model_validate(orjson.loads(raw))


async def _update_template_file_artifact(artifact: TemplateFileArtifact) -> None:
    """Update an existing template file artifact in Redis."""
    await _store_template_file_artifact(artifact)


async def _update_workflow_file_artifact(artifact: WorkflowFileArtifact) -> None:
    """Update an existing workflow file artifact in Redis."""
    await _store_workflow_file_artifact(artifact)


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


async def _get_workflow_folder_path(
    *,
    role: Any,
    session: Any,
    workflow: Any,
) -> str | None:
    """Load the materialized folder path for a workflow."""
    folder_id = getattr(workflow, "folder_id", None)
    if folder_id is None:
        return None
    folder = await WorkflowFolderService(session, role=role).get_folder(folder_id)
    return None if folder is None else folder.path


async def _build_workflow_yaml_envelope(
    *,
    role: Any,
    service: WorkflowsManagementService,
    workflow: Any,
    workflow_id: str,
    draft: bool,
) -> dict[str, Any]:
    """Build the MCP workflow YAML envelope for a workflow."""
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
                for action in sorted(workflow.actions, key=lambda action: action.ref)
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
            service.session, role=role
        ).get_case_trigger(WorkflowUUID.new(workflow.id))
        payload["case_trigger"] = {
            "status": case_trigger.status,
            "event_types": case_trigger.event_types,
            "tag_filters": case_trigger.tag_filters,
        }
    except TracecatNotFoundError:
        payload["case_trigger"] = None
    except Exception as e:
        logger.warning(
            "Could not load case trigger for workflow",
            workflow_id=workflow_id,
            error=str(e),
        )
        payload["case_trigger"] = None

    if draft:
        try:
            dsl = await service.build_dsl_from_workflow(workflow)
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
    else:
        definition_service = WorkflowDefinitionsService(service.session, role=role)
        if (
            defn := await definition_service.get_definition_by_workflow_id(
                WorkflowUUID.new(workflow.id)
            )
        ) is None:
            raise ToolError(
                f"No published definition found for workflow {workflow_id}. "
                "Publish the workflow before exporting with draft=False."
            )
        payload["version"] = defn.version
        payload["definition"] = DSLInput.model_validate(defn.content).model_dump(
            mode="json", exclude_none=True
        )

    return payload


def _serialize_workflow_yaml_envelope(payload: dict[str, Any]) -> str:
    """Serialize the workflow envelope to YAML."""
    return yaml.dump(payload, indent=2, sort_keys=False)


async def _build_inline_workflow_response(
    *,
    role: Any,
    service: WorkflowsManagementService,
    workflow: Workflow,
    workflow_id: str,
    draft: bool,
) -> dict[str, Any]:
    """Build the optional inline workflow YAML response payload."""
    wf_id = WorkflowUUID.new(workflow.id)
    folder_path = await _get_workflow_folder_path(
        role=role,
        session=service.session,
        workflow=workflow,
    )
    relative_path = _build_workflow_relative_path(
        workflow.title,
        wf_id,
        folder_path,
    )
    yaml_payload = await _build_workflow_yaml_envelope(
        role=role,
        service=service,
        workflow=workflow,
        workflow_id=workflow_id,
        draft=draft,
    )
    definition_yaml = _serialize_workflow_yaml_envelope(yaml_payload)
    definition_size_bytes = len(definition_yaml.encode("utf-8"))
    inline_limit_bytes = _inline_workflow_yaml_max_bytes()
    if definition_size_bytes > inline_limit_bytes:
        return {
            "definition_transport": "staged_required",
            "definition_size_bytes": definition_size_bytes,
            "inline_limit_bytes": inline_limit_bytes,
            "suggested_relative_path": relative_path,
        }
    return {
        "definition_transport": "inline",
        "definition_size_bytes": definition_size_bytes,
        "inline_limit_bytes": inline_limit_bytes,
        "definition_yaml": definition_yaml,
    }


async def _ensure_workflow_folder(
    *,
    role: Any,
    session: Any,
    folder_path: str | None,
) -> Any | None:
    """Ensure the materialized workflow folder path exists."""
    if folder_path is None or folder_path == "/":
        return None

    folder_service = WorkflowFolderService(session, role=role)
    if existing := await folder_service.get_folder_by_path(folder_path):
        return existing

    current_path = "/"
    created_folder = None
    for segment in folder_path.strip("/").split("/"):
        next_path = (
            f"{current_path}{segment}/" if current_path != "/" else f"/{segment}/"
        )
        if existing := await folder_service.get_folder_by_path(next_path):
            created_folder = existing
            current_path = next_path
            continue
        created_folder = await folder_service.create_folder(
            segment,
            parent_path=current_path,
            commit=True,
        )
        current_path = created_folder.path
    return created_folder


async def _assign_workflow_to_folder(
    *,
    role: Any,
    session: Any,
    workflow_id: WorkflowUUID,
    folder_path: str | None,
) -> None:
    """Move a workflow to the inferred folder path."""
    folder_service = WorkflowFolderService(session, role=role)
    folder = await _ensure_workflow_folder(
        role=role, session=session, folder_path=folder_path
    )
    await folder_service.move_workflow(workflow_id, folder)


def _build_workflow_file_payload(
    *,
    workflow: Any,
    relative_path: str,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the common response payload for workflow file tools."""
    payload: dict[str, Any] = {
        "workflow_id": str(workflow.id),
        "title": workflow.title,
        "suggested_relative_path": relative_path,
    }
    if extra:
        payload.update(extra)
    return payload


def _parse_uploaded_text_file(content: bytes, *, label: str) -> tuple[str, str]:
    """Validate uploaded text file bytes and decode to UTF-8 text."""
    if not content:
        raise ToolError(f"Uploaded {label} is empty")
    if len(content) > config.TRACECAT__MAX_FILE_SIZE_BYTES:
        raise ToolError(f"Uploaded {label} exceeds the maximum allowed size")
    try:
        return content.decode("utf-8"), _compute_sha256(content)
    except UnicodeDecodeError as exc:
        raise ToolError(f"Uploaded {label} must be UTF-8 encoded") from exc


def _parse_uploaded_workflow_yaml(content: bytes) -> tuple[str, str]:
    """Validate uploaded workflow file bytes and decode to text."""
    return _parse_uploaded_text_file(content, label="workflow file")


def _parse_workflow_file_artifact_id(artifact_id: str) -> uuid.UUID:
    """Validate a workflow file artifact id."""
    try:
        return uuid.UUID(artifact_id)
    except ValueError as exc:
        raise ToolError("Invalid artifact_id") from exc


async def _require_workflow_file_artifact(
    *,
    artifact_id: str,
    role: Any,
    ctx: Context | None,
    operation: WorkflowFileOperation,
    workflow_id: WorkflowUUID | None = None,
) -> WorkflowFileArtifact:
    """Load and authorize a workflow file artifact."""
    _parse_workflow_file_artifact_id(artifact_id)
    artifact = await _load_workflow_file_artifact(artifact_id)
    if artifact is None:
        raise ToolError("Workflow file artifact not found or expired")
    if artifact.used:
        raise ToolError("Workflow file artifact has already been consumed")
    if artifact.expires_at <= datetime.now(UTC):
        raise ToolError("Workflow file artifact has expired")
    if artifact.operation != operation:
        raise ToolError("Workflow file artifact operation does not match this tool")
    if artifact.workspace_id != role.workspace_id:
        raise ToolError("Workflow file artifact is not valid for this workspace")
    if artifact.organization_id != role.organization_id:
        raise ToolError("Workflow file artifact is not valid for this organization")

    current_client_id = _current_mcp_client_id()
    if artifact.client_id != current_client_id:
        raise ToolError("Workflow file artifact is not valid for this MCP client")
    if artifact.session_id != _get_context_session_id(ctx):
        raise ToolError("Workflow file artifact is not valid for this MCP session")
    if workflow_id is not None and artifact.workflow_id != workflow_id:
        raise ToolError("Workflow file artifact is not valid for this workflow")
    return artifact


async def _consume_workflow_file_artifact(
    *,
    artifact: WorkflowFileArtifact,
    sha256: str,
) -> None:
    """Mark a workflow file artifact as used."""
    artifact.used = True
    artifact.sha256 = sha256
    await _update_workflow_file_artifact(artifact)


async def _require_template_file_artifact(
    *,
    artifact_id: str,
    role: Any,
    ctx: Context | None,
) -> TemplateFileArtifact:
    """Load and authorize a template file artifact."""
    _parse_workflow_file_artifact_id(artifact_id)
    artifact = await _load_template_file_artifact(artifact_id)
    if artifact is None:
        raise ToolError("Template file artifact not found or expired")
    if artifact.used:
        raise ToolError("Template file artifact has already been consumed")
    if artifact.expires_at <= datetime.now(UTC):
        raise ToolError("Template file artifact has expired")
    if artifact.workspace_id != role.workspace_id:
        raise ToolError("Template file artifact is not valid for this workspace")
    if artifact.organization_id != role.organization_id:
        raise ToolError("Template file artifact is not valid for this organization")
    if artifact.client_id != _current_mcp_client_id():
        raise ToolError("Template file artifact is not valid for this MCP client")
    if artifact.session_id != _get_context_session_id(ctx):
        raise ToolError("Template file artifact is not valid for this MCP session")
    return artifact


async def _consume_template_file_artifact(
    *,
    artifact: TemplateFileArtifact,
    sha256: str,
) -> None:
    """Mark a template file artifact as used."""
    artifact.used = True
    artifact.sha256 = sha256
    await _update_template_file_artifact(artifact)


def _ensure_inline_workflow_yaml_size(definition_yaml: str) -> None:
    """Reject inline workflow YAML payloads that exceed the supported size."""
    size = len(definition_yaml.encode("utf-8"))
    limit = _inline_workflow_yaml_max_bytes()
    if size > limit:
        raise ToolError(
            "definition_yaml exceeds the inline workflow limit "
            f"({size} bytes > {limit} bytes); use prepare_workflow_file_upload "
            "with create_workflow_from_uploaded_file or "
            "update_workflow_from_uploaded_file instead"
        )


def _auto_generate_layout(
    actions: list[dict[str, Any]],
) -> dict[str, Any]:
    """Generate a top-down layout for workflow actions when none is provided.

    Walks the dependency graph to assign each action a depth (row), then
    spreads siblings horizontally. The trigger node sits at the top.
    """
    NODE_HEIGHT = 300  # vertical spacing between rows
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


def _build_import_data_from_workflow_yaml(
    *,
    definition_yaml: str,
    title: str | None = None,
    description: str | None = None,
) -> dict[str, Any]:
    """Parse workflow YAML into import data for workflow creation."""
    try:
        import_data = yaml.safe_load(definition_yaml)
    except yaml.YAMLError as exc:
        raise ToolError(f"Invalid YAML: {exc}") from exc
    normalized = _normalize_workflow_yaml_payload(import_data)

    definition = normalized.get("definition")
    if not isinstance(definition, dict):
        raise ToolError("Workflow definition YAML must include a definition object")
    if "title" not in definition and title is not None:
        definition["title"] = title
    if "description" not in definition and description:
        definition["description"] = description

    layout = normalized.get("layout")
    if not layout:
        actions = definition.get("actions", [])
        if actions:
            normalized["layout"] = _auto_generate_layout(actions)
    return normalized


async def _create_workflow_from_import_data(
    *,
    role: Any,
    import_data: dict[str, Any],
    use_workflow_id: bool = False,
) -> Workflow:
    """Create a workflow from normalized import data."""
    layout_data = import_data.get("layout")
    trigger_position, viewport, action_positions = _extract_layout_positions(
        layout_data
    )
    async with WorkflowsManagementService.with_session(role=role) as svc:
        return await svc.create_workflow_from_external_definition(
            import_data,
            use_workflow_id=use_workflow_id,
            trigger_position=trigger_position,
            viewport=viewport,
            action_positions=action_positions,
        )


async def _apply_workflow_yaml_update(
    *,
    role: Any,
    service: WorkflowsManagementService,
    workflow: Workflow,
    workflow_id: WorkflowUUID,
    update_params: WorkflowUpdate,
    yaml_payload: MCPWorkflowYamlPayload | None,
    definition_yaml: str | None,
    update_mode: Literal["replace", "patch"],
) -> None:
    """Apply workflow YAML sections and metadata updates."""
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

    update_action_positions: dict[str, tuple[float, float]] | None = None
    if yaml_payload is not None and yaml_payload.layout is not None:
        _, _, update_action_positions = _extract_layout_positions(
            yaml_payload.layout.model_dump()
        )

    if yaml_payload is not None and yaml_payload.definition is not None:
        await _replace_workflow_definition_from_dsl(
            service=service,
            workflow_id=workflow_id,
            dsl=yaml_payload.definition,
            action_positions=update_action_positions,
        )
        await service.session.refresh(workflow, ["actions"])

    if yaml_payload is not None and yaml_payload.layout is not None:
        await service.session.refresh(workflow, ["actions"])
        _apply_layout_to_workflow(workflow=workflow, layout=yaml_payload.layout)
        for action in workflow.actions:
            service.session.add(action)

    offline_schedule_ids: list[uuid.UUID] = []
    if yaml_payload is not None and yaml_payload.schedules is not None:
        schedule_service = WorkflowSchedulesService(service.session, role=role)
        offline_schedule_ids = await _replace_workflow_schedules(
            service=schedule_service,
            workflow_id=workflow_id,
            schedules=yaml_payload.schedules,
        )

    if yaml_payload is not None and yaml_payload.case_trigger is not None:
        case_trigger_service = CaseTriggersService(service.session, role=role)
        await _apply_case_trigger_payload(
            service=case_trigger_service,
            workflow_id=workflow_id,
            case_trigger_payload=yaml_payload.case_trigger,
            update_mode=update_mode,
        )

    update_data = update_params.model_dump(exclude_unset=True)
    if update_data:
        for key, value in update_data.items():
            setattr(workflow, key, value)
    service.session.add(workflow)
    await service.session.commit()
    await service.session.refresh(workflow)

    if offline_schedule_ids:
        schedule_service = WorkflowSchedulesService(service.session, role=role)
        for schedule_id in offline_schedule_ids:
            await schedule_service.update_schedule(
                schedule_id,
                ScheduleUpdate(status="offline"),
            )


async def _validate_template_action_text(
    *,
    role: Any,
    template_text: str,
    check_db: bool,
) -> str:
    """Validate a template action payload from YAML text."""
    action_name: str | None = None

    try:
        raw_template = yaml.safe_load(template_text)
    except yaml.YAMLError as exc:
        return _json(
            {
                "valid": False,
                "action_name": action_name,
                "errors": [{"type": "yaml_error", "message": str(exc)}],
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


def _build_table_csv_file_name(table_name: str, table_id: uuid.UUID) -> str:
    """Build a stable CSV export file name for a table."""
    slug = slugify(table_name, separator="-") or "table"
    return f"{slug}--{table_id.hex[:8]}.csv"


def _build_csv_export_payload(
    *,
    table: Any,
    relative_path: str,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the common response payload for CSV export tools."""
    payload: dict[str, Any] = {
        "table_id": str(table.id),
        "name": table.name,
        "suggested_relative_path": relative_path,
    }
    if extra:
        payload.update(extra)
    return payload


auth = create_mcp_auth()

_CASE_EVENT_TYPE_VALUES = [event_type.value for event_type in CaseEventType]
_CASE_EVENT_TYPE_VALUES_JSON = json.dumps(
    _CASE_EVENT_TYPE_VALUES, separators=(",", ":")
)

# ---------------------------------------------------------------------------
# Server instructions — sent to every MCP client on connection
# ---------------------------------------------------------------------------

_MCP_INSTRUCTIONS = f"""\
Tracecat workflow management server.

## MCP tool namespaces
All MCP tools are namespaced by resource type and exposed as \
`<namespace>_<tool_name>`:
- `workspaces_*` (e.g. `workspaces_list_workspaces`)
- `workflows_*` (workflow lifecycle, execution, tags, webhook/case-trigger config)
- `cases_*`, `tables_*`, `variables_*`, `secrets_*`, `integrations_*`, `agents_*`

Use `workspaces_list_workspaces` to discover available workspaces, then pass \
`workspace_id` to all other tools.

For readability, references below use unprefixed tool names (e.g. \
`create_workflow`), which correspond to namespaced MCP tools \
like `workflows_create_workflow`.

## Action namespaces
- `core.*` — built-in platform actions (core.http_request, core.transform.reshape, \
core.script.run_python, core.table.*, core.open_case, core.send_email, etc.)
- `core.transform.*` — data transforms (reshape, scatter, gather, filter, map)
- `tools.*` — third-party integrations (tools.slack.post_message, etc.)
- `ai.*` — AI/LLM actions:
  - `ai.action` — simple LLM call (no tools), supports `output_type` for structured output
  - `ai.agent` — full AI agent with tool calling via `actions` list
  - `ai.preset_agent` — run a saved agent preset by slug

Use `workflows_list_actions` to discover available actions. Use \
`workflows_get_action_context` or `workflows_get_workflow_authoring_context` \
to get parameter schemas for any action \
(including platform/interface actions like ai.agent, scatter, gather, etc.). \
Use `agents_get_agent_preset_authoring_context` before creating agent presets \
to inspect \
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
2. Use `create_workflow` to create a blank workflow shell when needed
3. Use inline `definition_yaml` on `create_workflow` / `update_workflow` for
small workflow edits
4. Use `get_workflow(include_definition_yaml=true)` when you want inline YAML
for a small workflow, or `get_workflow_file` / `prepare_workflow_file_upload`
plus the file-based workflow create/update tools for larger workflows
5. `validate_workflow` — check for structural and expression errors
6. `publish_workflow` — freeze a versioned snapshot
7. `run_published_workflow` or `run_draft_workflow` — execute it
8. `list_workflow_executions` — see run history, find execution IDs
9. `get_workflow_execution` — inspect execution status, per-action results/errors

## Workflow file tools
- {_WORKFLOW_FILE_WARNING}
- Inline workflow YAML is supported on `create_workflow` and `update_workflow`
for small payloads up to 128 KB.
- `get_workflow(include_definition_yaml=true)` returns inline `definition_yaml`
when the workflow fits within that limit; otherwise it returns
`definition_transport: "staged_required"` and a `suggested_relative_path`.
- `get_workflow_file` returns a short-lived download URL for remote `/mcp` clients.
- `prepare_workflow_file_upload` is required for remote `/mcp` workflow file uploads. It returns a short-lived upload URL and an opaque artifact id for the finalize create/update tools.

## Template and CSV file tools
- {_TEMPLATE_FILE_WARNING}
- `prepare_template_file_upload` is required for remote `/mcp` template validation uploads.
- {_CSV_FILE_WARNING}
- `export_csv` returns a short-lived download URL for remote `/mcp` clients.

## Agent preset authoring
1. `get_agent_preset_authoring_context` — inspect models, provider readiness, integrations, variables, and output_type options
2. `list_integrations` — inspect workspace MCP integrations and broader provider status
3. `list_actions` / `get_action_context` — choose preset tools and inspect arg schemas
4. `create_agent_preset` — create a reusable preset
5. `update_agent_preset` — revise an existing preset by slug when its prompts, tools, or model settings need to change
6. `list_agent_presets` — find reusable agents you can invoke by slug without loading their full prompts or tool configs
7. `get_agent_preset` / `run_agent_preset` — fetch a preset's full configuration when you need to inspect it, or run it once you know which slug to use

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


def _normalize_limit(
    limit: int | None,
    *,
    default: int,
    max_limit: int,
) -> int:
    """Clamp a requested MCP list limit to a safe range."""
    if limit is None:
        return default
    return max(config.TRACECAT__LIMIT_MIN, min(limit, max_limit))


def _pagination_fingerprint(tool_name: str, **filters: Any) -> str:
    """Build a stable fingerprint for cursor validation."""
    payload = {"tool_name": tool_name, "filters": filters}
    serialized = orjson.dumps(payload, option=orjson.OPT_SORT_KEYS)
    return hashlib.sha256(serialized).hexdigest()


def _encode_offset_cursor(offset: int, fingerprint: str) -> str:
    """Encode an in-memory pagination cursor."""
    payload = {"offset": offset, "fingerprint": fingerprint}
    serialized = orjson.dumps(payload, option=orjson.OPT_SORT_KEYS)
    return base64.urlsafe_b64encode(serialized).decode("ascii")


def _decode_offset_cursor(cursor: str, *, expected_fingerprint: str) -> int:
    """Decode an in-memory pagination cursor."""
    try:
        decoded = base64.urlsafe_b64decode(cursor.encode("ascii")).decode("utf-8")
        payload = json.loads(decoded)
    except Exception as e:
        raise ToolError("Invalid cursor format") from e

    if payload.get("fingerprint") != expected_fingerprint:
        raise ToolError(
            "Cursor no longer matches current filters. Retry without cursor."
        )

    offset = payload.get("offset")
    if not isinstance(offset, int) or offset < 0:
        raise ToolError("Invalid cursor format")
    return offset


def _paginate_items[T](
    items: Sequence[T],
    *,
    tool_name: str,
    limit: int,
    cursor: str | None = None,
    filters: dict[str, Any] | None = None,
) -> MCPPaginatedResponse[T]:
    """Paginate an in-memory collection with cursor validation."""
    normalized_items = list(items)
    fingerprint = _pagination_fingerprint(tool_name, **(filters or {}))
    start = (
        _decode_offset_cursor(cursor, expected_fingerprint=fingerprint)
        if cursor is not None
        else 0
    )
    end = start + limit
    next_cursor = (
        _encode_offset_cursor(end, fingerprint) if end < len(normalized_items) else None
    )
    prev_start = max(0, start - limit)
    prev_cursor = _encode_offset_cursor(prev_start, fingerprint) if start > 0 else None
    return MCPPaginatedResponse[T](
        items=normalized_items[start:end],
        next_cursor=next_cursor,
        prev_cursor=prev_cursor,
        has_more=next_cursor is not None,
        has_previous=start > 0,
    )


def _serialize_paginated_response[T](
    page: MCPPaginatedResponse[T] | CursorPaginatedResponse[T],
) -> dict[str, Any]:
    """Serialize a paginated response to a JSON-safe dict."""
    return page.model_dump(mode="json")


def _truncate_embedded_list[T](
    items: Sequence[T],
    *,
    limit: int,
) -> tuple[list[T], MCPTruncationInfo]:
    """Cap an embedded list and return truncation metadata."""
    normalized_items = list(items)
    returned = normalized_items[:limit]
    return returned, MCPTruncationInfo(
        limit=limit,
        total=len(normalized_items),
        returned=len(returned),
        truncated=len(normalized_items) > limit,
    )


def _truncate_named_sections(
    sections: dict[str, Sequence[Any]],
    *,
    limit: int,
) -> tuple[dict[str, list[Any]], MCPTruncationSummary]:
    """Cap multiple embedded collections and summarize truncation."""
    truncated_sections: dict[str, list[Any]] = {}
    summary = MCPTruncationSummary()
    for name, items in sections.items():
        truncated_items, info = _truncate_embedded_list(items, limit=limit)
        truncated_sections[name] = truncated_items
        summary.collections[name] = info
    return truncated_sections, summary


_MCP_EMBEDDED_COLLECTION_LIMIT = min(50, config.TRACECAT__LIMIT_CURSOR_MAX)


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


workspaces_mcp = FastMCP("tracecat-workspaces")
workflows_mcp = FastMCP("tracecat-workflows-tools")
cases_mcp = FastMCP("tracecat-cases")
tables_mcp = FastMCP("tracecat-tables")
variables_mcp = FastMCP("tracecat-variables")
secrets_mcp = FastMCP("tracecat-secrets")
integrations_mcp = FastMCP("tracecat-integrations")
agents_mcp = FastMCP("tracecat-agents")

mcp.mount(workspaces_mcp, namespace="workspaces")
mcp.mount(workflows_mcp, namespace="workflows")
mcp.mount(cases_mcp, namespace="cases")
mcp.mount(tables_mcp, namespace="tables")
mcp.mount(variables_mcp, namespace="variables")
mcp.mount(secrets_mcp, namespace="secrets")
mcp.mount(integrations_mcp, namespace="integrations")
mcp.mount(agents_mcp, namespace="agents")

_TOOL_NAMESPACE_BY_NAME: dict[str, str] = {
    "list_workspaces": "workspaces",
    "create_workflow": "workflows",
    "get_workflow": "workflows",
    "get_workflow_file": "workflows",
    "prepare_workflow_file_upload": "workflows",
    "create_workflow_from_uploaded_file": "workflows",
    "update_workflow_from_uploaded_file": "workflows",
    "update_workflow": "workflows",
    "list_workflows": "workflows",
    "list_workflow_tree": "workflows",
    "create_workflow_folder": "workflows",
    "move_workflows": "workflows",
    "list_actions": "workflows",
    "get_action_context": "workflows",
    "get_workflow_authoring_context": "workflows",
    "validate_workflow": "workflows",
    "prepare_template_file_upload": "workflows",
    "validate_template_action": "workflows",
    "publish_workflow": "workflows",
    "run_draft_workflow": "workflows",
    "run_published_workflow": "workflows",
    "list_workflow_executions": "workflows",
    "get_workflow_execution": "workflows",
    "get_webhook": "workflows",
    "update_webhook": "workflows",
    "get_case_trigger": "workflows",
    "update_case_trigger": "workflows",
    "list_workflow_tags": "workflows",
    "create_workflow_tag": "workflows",
    "update_workflow_tag": "workflows",
    "delete_workflow_tag": "workflows",
    "list_tags_for_workflow": "workflows",
    "add_workflow_tag": "workflows",
    "remove_workflow_tag": "workflows",
    "list_case_tags": "cases",
    "create_case_tag": "cases",
    "update_case_tag": "cases",
    "delete_case_tag": "cases",
    "list_tags_for_case": "cases",
    "add_case_tag": "cases",
    "remove_case_tag": "cases",
    "list_case_fields": "cases",
    "create_case_field": "cases",
    "update_case_field": "cases",
    "delete_case_field": "cases",
    "list_tables": "tables",
    "create_table": "tables",
    "get_table": "tables",
    "update_table": "tables",
    "insert_table_row": "tables",
    "update_table_row": "tables",
    "search_table_rows": "tables",
    "export_csv": "tables",
    "list_variables": "variables",
    "get_variable": "variables",
    "list_secrets_metadata": "secrets",
    "get_secret_metadata": "secrets",
    "list_integrations": "integrations",
    "get_agent_preset_authoring_context": "agents",
    "create_agent_preset": "agents",
    "update_agent_preset": "agents",
    "list_agent_presets": "agents",
    "get_agent_preset": "agents",
    "run_agent_preset": "agents",
}

_TOOL_NAMESPACE_SERVERS: dict[str, FastMCP] = {
    "workspaces": workspaces_mcp,
    "workflows": workflows_mcp,
    "cases": cases_mcp,
    "tables": tables_mcp,
    "variables": variables_mcp,
    "secrets": secrets_mcp,
    "integrations": integrations_mcp,
    "agents": agents_mcp,
}


def _namespaced_tool(*args: Any, **kwargs: Any) -> Callable[[Any], Any]:
    def decorator(func: Any) -> Any:
        try:
            namespace = _TOOL_NAMESPACE_BY_NAME[func.__name__]
        except KeyError as e:
            raise ValueError(
                f"Tool namespace mapping missing for function '{func.__name__}'"
            ) from e
        return _TOOL_NAMESPACE_SERVERS[namespace].tool(*args, **kwargs)(func)

    return decorator


mcp.tool = cast(Any, _namespaced_tool)


@mcp.tool()
async def list_workspaces(
    limit: int = config.TRACECAT__LIMIT_DEFAULT,
    cursor: str | None = None,
) -> str:
    """List all workspaces accessible to the authenticated user.

    Returns a JSON array of workspace objects with id, name, and role.
    """
    try:
        workspaces = await list_workspaces_for_request()
        page = _paginate_items(
            workspaces,
            tool_name="list_workspaces",
            limit=_normalize_limit(
                limit,
                default=config.TRACECAT__LIMIT_DEFAULT,
                max_limit=config.TRACECAT__LIMIT_CURSOR_MAX,
            ),
            cursor=cursor,
        )
        return _json(_serialize_paginated_response(page))
    except ValueError as e:
        raise ToolError(str(e)) from e
    except Exception as e:
        logger.error("Failed to list workspaces", error=str(e))
        raise ToolError(f"Failed to list workspaces: {e}") from None


@mcp.tool()
async def create_workflow(
    workspace_id: str,
    title: str,
    description: str = "",
    definition_yaml: str | None = None,
) -> str:
    """Create a new workflow in a workspace.

    Args:
        workspace_id: The workspace ID (from list_workspaces).
        title: Workflow title (3-100 characters).
        description: Optional workflow description (up to 1000 characters).
        definition_yaml: Optional inline workflow YAML for small workflows.

    Returns JSON with the new workflow's id, title, description, and status.
    """

    try:
        _, role = await _resolve_workspace_role(workspace_id)
        if definition_yaml is not None:
            _ensure_inline_workflow_yaml_size(definition_yaml)
            import_data = _build_import_data_from_workflow_yaml(
                definition_yaml=definition_yaml,
                title=title,
                description=description,
            )
            workflow = await _create_workflow_from_import_data(
                role=role,
                import_data=import_data,
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
    include_definition_yaml: bool = False,
) -> str:
    """Get metadata for a specific workflow.

    Args:
        workspace_id: The workspace ID.
        workflow_id: The workflow ID (short or full format).
        include_definition_yaml: When true, include inline YAML if small enough.

    Returns JSON with workflow metadata. Use get_workflow_file to retrieve the
    full workflow definition as a file or staged download.
    """

    try:
        _, role = await _resolve_workspace_role(workspace_id)
        wf_id = WorkflowUUID.new(workflow_id)

        async with WorkflowsManagementService.with_session(role=role) as svc:
            workflow = await svc.get_workflow(wf_id)
            if not workflow:
                raise ToolError(f"Workflow {workflow_id} not found")
            payload = {
                "id": str(workflow.id),
                "title": workflow.title,
                "description": workflow.description,
                "status": workflow.status,
                "version": workflow.version,
                "alias": workflow.alias,
                "entrypoint": workflow.entrypoint,
            }
            if include_definition_yaml:
                payload.update(
                    await _build_inline_workflow_response(
                        role=role,
                        service=svc,
                        workflow=workflow,
                        workflow_id=workflow_id,
                        draft=True,
                    )
                )
            return _json(payload)
    except ToolError:
        raise
    except ValueError as e:
        raise ToolError(str(e)) from e
    except Exception as e:
        logger.error("Failed to get workflow", error=str(e))
        raise ToolError(f"Failed to get workflow: {e}") from None


@mcp.tool()
async def get_workflow_file(
    workspace_id: str,
    workflow_id: str,
    draft: bool = True,
    ctx: Context | None = None,
) -> str:
    """Export a workflow to a staged download URL."""

    try:
        _require_remote_mcp_context(ctx, tool_name="get_workflow_file")
        _, role = await _resolve_workspace_role(workspace_id)
        wf_id = WorkflowUUID.new(workflow_id)

        async with WorkflowsManagementService.with_session(role=role) as svc:
            workflow = await svc.get_workflow(wf_id)
            if workflow is None:
                raise ToolError(f"Workflow {workflow_id} not found")

            folder_path = await _get_workflow_folder_path(
                role=role,
                session=svc.session,
                workflow=workflow,
            )
            relative_path = _build_workflow_relative_path(
                workflow.title,
                wf_id,
                folder_path,
            )
            yaml_payload = await _build_workflow_yaml_envelope(
                role=role,
                service=svc,
                workflow=workflow,
                workflow_id=workflow_id,
                draft=draft,
            )
            content = _serialize_workflow_yaml_envelope(yaml_payload)
            result_payload = _build_workflow_file_payload(
                workflow=workflow,
                relative_path=relative_path,
                extra={"draft": draft},
            )

        artifact_id = uuid.uuid4()
        expires_at = _workflow_file_artifact_expires_at()
        blob_key = _workflow_file_blob_key(
            role.workspace_id,
            _get_context_session_id(ctx),
            artifact_id,
            PurePosixPath(relative_path).name,
        )
        await blob.upload_file(
            content.encode("utf-8"),
            key=blob_key,
            bucket=_workflow_file_bucket(),
            content_type="application/yaml",
        )
        download_url = await blob.generate_presigned_download_url(
            key=blob_key,
            bucket=_workflow_file_bucket(),
            expiry=_mcp_file_transfer_ttl_seconds(),
            override_content_type="application/yaml",
        )
        result_payload.update(
            {
                "download_url": download_url,
                "expires_at": expires_at.isoformat(),
                "transport": _get_context_transport(ctx),
            }
        )
        return _json(result_payload)
    except ToolError:
        raise
    except ValueError as e:
        raise ToolError(str(e)) from e
    except Exception as e:
        logger.error("Failed to export workflow file", error=str(e))
        raise ToolError(f"Failed to export workflow file: {e}") from None


@mcp.tool()
async def prepare_workflow_file_upload(
    workspace_id: str,
    relative_path: str,
    operation: str,
    workflow_id: str | None = None,
    update_mode: str = "patch",
    ctx: Context | None = None,
) -> str:
    """Prepare a staged workflow file upload for remote `/mcp` clients.

    Clients typically save and upload files locally before handing them to the
    remote MCP upload URL returned by this tool.
    """

    try:
        _require_remote_mcp_context(ctx, tool_name="prepare_workflow_file_upload")
        _, role = await _resolve_workspace_role(workspace_id)
        workflow_operation = WorkflowFileOperation(operation)
        if update_mode not in {"replace", "patch"}:
            raise ToolError("update_mode must be 'replace' or 'patch'")
        normalized_relative_path = _normalize_workflow_file_relative_path(relative_path)
        target_workflow_id = (
            WorkflowUUID.new(workflow_id) if workflow_id is not None else None
        )
        if (
            workflow_operation is WorkflowFileOperation.UPDATE
            and target_workflow_id is None
        ):
            raise ToolError("workflow_id is required for update uploads")

        artifact_id = uuid.uuid4()
        expires_at = _workflow_file_artifact_expires_at()
        artifact = WorkflowFileArtifact(
            artifact_id=artifact_id,
            organization_id=role.organization_id,
            workspace_id=role.workspace_id,
            client_id=_current_mcp_client_id(),
            session_id=_get_context_session_id(ctx),
            operation=workflow_operation,
            relative_path=normalized_relative_path,
            folder_path=_infer_folder_path_from_relative_path(normalized_relative_path),
            blob_key=_workflow_file_blob_key(
                role.workspace_id,
                _get_context_session_id(ctx),
                artifact_id,
                PurePosixPath(normalized_relative_path).name,
            ),
            workflow_id=target_workflow_id,
            update_mode=cast(Literal["replace", "patch"], update_mode),
            expires_at=expires_at,
        )
        await _store_workflow_file_artifact(artifact)
        upload_url = await blob.generate_presigned_upload_url(
            key=artifact.blob_key,
            bucket=_workflow_file_bucket(),
            expiry=_mcp_file_transfer_ttl_seconds(),
            content_type="application/yaml",
        )
        return _json(
            {
                "artifact_id": str(artifact.artifact_id),
                "upload_url": upload_url,
                "expires_at": artifact.expires_at.isoformat(),
                "relative_path": artifact.relative_path,
                "folder_path": artifact.folder_path,
                "operation": artifact.operation.value,
                "workflow_id": (
                    str(artifact.workflow_id) if artifact.workflow_id else None
                ),
                "update_mode": artifact.update_mode,
            }
        )
    except ToolError:
        raise
    except ValueError as e:
        raise ToolError(str(e)) from e
    except Exception as e:
        logger.error("Failed to prepare workflow file upload", error=str(e))
        raise ToolError(f"Failed to prepare workflow file upload: {e}") from None


@mcp.tool()
async def create_workflow_from_uploaded_file(
    workspace_id: str,
    artifact_id: str,
    title: str | None = None,
    description: str = "",
    use_workflow_id: bool = False,
    ctx: Context | None = None,
) -> str:
    """Create a workflow from a previously staged workflow file upload."""

    try:
        _require_remote_mcp_context(ctx, tool_name="create_workflow_from_uploaded_file")
        _, role = await _resolve_workspace_role(workspace_id)
        artifact = await _require_workflow_file_artifact(
            artifact_id=artifact_id,
            role=role,
            ctx=ctx,
            operation=WorkflowFileOperation.CREATE,
        )
        if not await blob.file_exists(artifact.blob_key, _workflow_file_bucket()):
            raise ToolError("Uploaded workflow file was not found in staged storage")

        content = await blob.download_file(artifact.blob_key, _workflow_file_bucket())
        definition_yaml, sha256 = _parse_uploaded_workflow_yaml(content)
        import_data = _build_import_data_from_workflow_yaml(
            definition_yaml=definition_yaml,
            title=title,
            description=description,
        )
        workflow = await _create_workflow_from_import_data(
            role=role,
            import_data=import_data,
            use_workflow_id=use_workflow_id,
        )
        async with WorkflowsManagementService.with_session(role=role) as svc:
            await _assign_workflow_to_folder(
                role=role,
                session=svc.session,
                workflow_id=WorkflowUUID.new(workflow.id),
                folder_path=artifact.folder_path,
            )

        await _consume_workflow_file_artifact(artifact=artifact, sha256=sha256)
        return _json(
            {
                "id": str(workflow.id),
                "title": workflow.title,
                "description": workflow.description,
                "status": workflow.status,
                "folder_path": artifact.folder_path,
                "artifact_id": str(artifact.artifact_id),
            }
        )
    except ToolError:
        raise
    except ValueError as e:
        raise ToolError(str(e)) from e
    except Exception as e:
        logger.error("Failed to create workflow from uploaded file", error=str(e))
        raise ToolError(f"Failed to create workflow from uploaded file: {e}") from None


@mcp.tool()
async def update_workflow_from_uploaded_file(
    workspace_id: str,
    workflow_id: str,
    artifact_id: str,
    title: str | None = None,
    description: str | None = None,
    status: str | None = None,
    alias: str | None = None,
    error_handler: str | None = None,
    update_mode: str | None = None,
    ctx: Context | None = None,
) -> str:
    """Update a workflow from a previously staged workflow file upload."""

    try:
        _require_remote_mcp_context(ctx, tool_name="update_workflow_from_uploaded_file")
        _, role = await _resolve_workspace_role(workspace_id)
        wf_id = WorkflowUUID.new(workflow_id)
        artifact = await _require_workflow_file_artifact(
            artifact_id=artifact_id,
            role=role,
            ctx=ctx,
            operation=WorkflowFileOperation.UPDATE,
            workflow_id=wf_id,
        )
        effective_update_mode = artifact.update_mode
        if update_mode is not None:
            if update_mode not in {"replace", "patch"}:
                raise ToolError("update_mode must be 'replace' or 'patch'")
            if update_mode != artifact.update_mode:
                raise ToolError(
                    "update_mode does not match the prepared upload artifact"
                )
            effective_update_mode = cast(Literal["replace", "patch"], update_mode)
        if not await blob.file_exists(artifact.blob_key, _workflow_file_bucket()):
            raise ToolError("Uploaded workflow file was not found in staged storage")

        content = await blob.download_file(artifact.blob_key, _workflow_file_bucket())
        definition_yaml, sha256 = _parse_uploaded_workflow_yaml(content)
        yaml_payload = _parse_workflow_yaml_payload(definition_yaml)

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
            await _apply_workflow_yaml_update(
                role=role,
                service=svc,
                workflow=workflow,
                workflow_id=wf_id,
                update_params=update_params,
                yaml_payload=yaml_payload,
                definition_yaml=definition_yaml,
                update_mode=effective_update_mode,
            )
            await _assign_workflow_to_folder(
                role=role,
                session=svc.session,
                workflow_id=wf_id,
                folder_path=artifact.folder_path,
            )

        await _consume_workflow_file_artifact(artifact=artifact, sha256=sha256)
        return _json(
            {
                "message": f"Workflow {workflow_id} updated successfully",
                "mode": effective_update_mode,
                "folder_path": artifact.folder_path,
                "artifact_id": str(artifact.artifact_id),
            }
        )
    except ToolError:
        raise
    except ValueError as e:
        raise ToolError(str(e)) from e
    except Exception as e:
        logger.error("Failed to update workflow from uploaded file", error=str(e))
        raise ToolError(f"Failed to update workflow from uploaded file: {e}") from None


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
    """Update workflow metadata and optional inline YAML.

    Args:
        workspace_id: The workspace ID.
        workflow_id: The workflow ID.
        title: New title (3-100 characters, optional).
        description: New description (optional).
        status: New status - "online" or "offline" (optional).
        alias: New alias for the workflow (optional).
        error_handler: Error handler workflow alias (optional).
        definition_yaml: Optional inline workflow YAML for small workflows.
        update_mode: "patch" to update provided YAML sections, or "replace" to
            replace provided YAML state sections.
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
        if update_mode not in {"replace", "patch"}:
            raise ToolError("update_mode must be 'replace' or 'patch'")
        if definition_yaml is not None:
            _ensure_inline_workflow_yaml_size(definition_yaml)
        update_params = WorkflowUpdate(**update_kwargs)

        async with WorkflowsManagementService.with_session(role=role) as svc:
            workflow = await svc.get_workflow(wf_id)
            if workflow is None:
                raise ToolError(f"Workflow {workflow_id} not found")
            if definition_yaml is not None:
                yaml_payload = _parse_workflow_yaml_payload(definition_yaml)
                await _apply_workflow_yaml_update(
                    role=role,
                    service=svc,
                    workflow=workflow,
                    workflow_id=wf_id,
                    update_params=update_params,
                    yaml_payload=yaml_payload,
                    definition_yaml=definition_yaml,
                    update_mode=update_mode,
                )
                mode = update_mode
            else:
                for key, value in update_params.model_dump(exclude_unset=True).items():
                    setattr(workflow, key, value)
                svc.session.add(workflow)
                await svc.session.commit()
                await svc.session.refresh(workflow)
                mode = "metadata"

            return _json(
                {
                    "message": f"Workflow {workflow_id} updated successfully",
                    "mode": mode,
                }
            )
    except ToolError:
        raise
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
    cursor: str | None = None,
) -> str:
    """List workflows in a workspace."""

    try:
        _, role = await _resolve_workspace_role(workspace_id)
        limit = _normalize_limit(limit, default=50, max_limit=200)
        async with WorkflowsManagementService.with_session(role=role) as svc:
            page = await svc.list_workflows(
                CursorPaginationParams(limit=limit, cursor=cursor),
                status=status,
                search=search,
            )
            return _json(
                _serialize_paginated_response(
                    MCPPaginatedResponse[dict[str, Any]](
                        items=[
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
                            for workflow, latest_defn, _trigger_summary in page.items
                        ],
                        next_cursor=page.next_cursor,
                        prev_cursor=page.prev_cursor,
                        has_more=page.has_more,
                        has_previous=page.has_previous,
                    )
                )
            )
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
    limit: int = config.TRACECAT__LIMIT_DEFAULT,
    cursor: str | None = None,
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

            page = _paginate_items(
                items,
                tool_name="list_workflow_tree",
                limit=_normalize_limit(
                    limit,
                    default=config.TRACECAT__LIMIT_DEFAULT,
                    max_limit=config.TRACECAT__LIMIT_CURSOR_MAX,
                ),
                cursor=cursor,
                filters={
                    "path": root_path,
                    "depth": depth,
                    "include_workflows": include_workflows,
                },
            )
            payload = _serialize_paginated_response(page)
            payload["root_path"] = root_path
            payload["depth"] = "unlimited" if depth == 0 else depth
            return _json(payload)
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
    cursor: str | None = None,
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
        limit = _normalize_limit(limit, default=50, max_limit=200)
        workspace_inventory = await _load_secret_inventory(role)
        async with RegistryActionsService.with_session(role=role) as svc:
            if query:
                entries = await svc.search_actions_from_index(query, limit=None)
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
            page = _paginate_items(
                items,
                tool_name="list_actions",
                limit=limit,
                cursor=cursor,
                filters={"query": query, "namespace": namespace},
            )
            return _json(_serialize_paginated_response(page))
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

        truncated_sections, truncation = _truncate_named_sections(
            {
                "actions": action_contexts,
                "variable_hints": variable_hints,
                "secret_hints": secret_hints,
            },
            limit=_MCP_EMBEDDED_COLLECTION_LIMIT,
        )

        return _json(
            {
                "actions": truncated_sections["actions"],
                "variable_hints": truncated_sections["variable_hints"],
                "secret_hints": truncated_sections["secret_hints"],
                "notes": [
                    "configured means required secret names and required key names exist in the default environment",
                ],
                "truncation": truncation.model_dump(mode="json"),
            }
        )
    except ToolError:
        raise
    except ValueError as e:
        raise ToolError(str(e)) from e
    except Exception as e:
        logger.error("Failed to build workflow authoring context", error=str(e))
        raise ToolError(f"Failed to build workflow authoring context: {e}") from None


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
async def prepare_template_file_upload(
    workspace_id: str,
    relative_path: str,
    ctx: Context | None = None,
) -> str:
    """Prepare a staged template YAML upload for remote `/mcp` clients."""

    try:
        _require_remote_mcp_context(ctx, tool_name="prepare_template_file_upload")
        _, role = await _resolve_workspace_role(workspace_id)
        normalized_relative_path = _normalize_workflow_file_relative_path(relative_path)
        artifact_id = uuid.uuid4()
        expires_at = _workflow_file_artifact_expires_at()
        artifact = TemplateFileArtifact(
            artifact_id=artifact_id,
            organization_id=role.organization_id,
            workspace_id=role.workspace_id,
            client_id=_current_mcp_client_id(),
            session_id=_get_context_session_id(ctx),
            relative_path=normalized_relative_path,
            blob_key=(
                f"{role.workspace_id}/mcp/template-files/"
                f"{_get_context_session_id(ctx)}/{artifact_id}/"
                f"{PurePosixPath(normalized_relative_path).name}"
            ),
            expires_at=expires_at,
        )
        await _store_template_file_artifact(artifact)
        upload_url = await blob.generate_presigned_upload_url(
            key=artifact.blob_key,
            bucket=_template_file_bucket(),
            expiry=_mcp_file_transfer_ttl_seconds(),
            content_type="application/yaml",
        )
        return _json(
            {
                "artifact_id": str(artifact.artifact_id),
                "upload_url": upload_url,
                "expires_at": artifact.expires_at.isoformat(),
                "relative_path": artifact.relative_path,
            }
        )
    except ToolError:
        raise
    except ValueError as exc:
        raise ToolError(str(exc)) from exc
    except Exception as exc:
        logger.error("Failed to prepare template file upload", error=str(exc))
        raise ToolError(f"Failed to prepare template file upload: {exc}") from None


@mcp.tool()
async def validate_template_action(
    workspace_id: str,
    artifact_id: str,
    check_db: bool = False,
    ctx: Context | None = None,
) -> str:
    """Validate a template action YAML file.

    Validates YAML parsing, template schema correctness, step action references,
    argument schemas, and expression references.

    Args:
        workspace_id: The workspace ID.
        artifact_id: Uploaded template artifact id for remote `/mcp` clients.
        check_db: When True, also resolve missing actions from registry DB.
            Defaults to False for local-only validation.

    Returns JSON with valid (bool), action_name (if available), and any errors.
    """

    try:
        _require_remote_mcp_context(ctx, tool_name="validate_template_action")
        _, role = await _resolve_workspace_role(workspace_id)
        artifact = await _require_template_file_artifact(
            artifact_id=artifact_id,
            role=role,
            ctx=ctx,
        )
        if not await blob.file_exists(artifact.blob_key, _template_file_bucket()):
            raise ToolError("Uploaded template file was not found in staged storage")
        content = await blob.download_file(artifact.blob_key, _template_file_bucket())
        template_text, sha256 = _parse_uploaded_text_file(
            content,
            label="template file",
        )
        result = await _validate_template_action_text(
            role=role,
            template_text=template_text,
            check_db=check_db,
        )
        await _consume_template_file_artifact(artifact=artifact, sha256=sha256)
        return result
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
    cursor: str | None = None,
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
        limit = _normalize_limit(limit, default=20, max_limit=100)

        exec_service = await WorkflowExecutionsService.connect(role=role)
        executions = await exec_service.list_executions_paginated(
            pagination=CursorPaginationParams(limit=limit, cursor=cursor),
            workflow_id=wf_id,
        )
        items: list[dict[str, Any]] = []
        for execution in executions.items:
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
        return _json(
            _serialize_paginated_response(
                MCPPaginatedResponse[dict[str, Any]](
                    items=items,
                    next_cursor=executions.next_cursor,
                    prev_cursor=executions.prev_cursor,
                    has_more=executions.has_more,
                    has_previous=executions.has_previous,
                )
            )
        )
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


@mcp.tool()
async def list_workflow_tags(
    workspace_id: str,
    limit: int = config.TRACECAT__LIMIT_DEFAULT,
    cursor: str | None = None,
) -> str:
    """List workflow tag definitions in a workspace.

    Returns a JSON array of tag objects with `id`, `name`, `ref`, and `color`.
    """

    try:
        _, role = await _resolve_workspace_role(workspace_id)
        async with TagsService.with_session(role=role) as svc:
            tags = await svc.list_tags()
            page = _paginate_items(
                [_workflow_tag_payload(tag) for tag in tags],
                tool_name="list_workflow_tags",
                limit=_normalize_limit(
                    limit,
                    default=config.TRACECAT__LIMIT_DEFAULT,
                    max_limit=config.TRACECAT__LIMIT_CURSOR_MAX,
                ),
                cursor=cursor,
            )
            return _json(_serialize_paginated_response(page))
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
async def list_tags_for_workflow(
    workspace_id: str,
    workflow_id: str,
    limit: int = config.TRACECAT__LIMIT_DEFAULT,
    cursor: str | None = None,
) -> str:
    """List tags attached to a workflow.

    Returns a JSON array of tag objects with `id`, `name`, `ref`, and `color`.
    """

    try:
        _, role = await _resolve_workspace_role(workspace_id)
        wf_id = WorkflowUUID.new(workflow_id)
        async with WorkflowTagsService.with_session(role=role) as svc:
            tags = await svc.list_tags_for_workflow(wf_id)
            page = _paginate_items(
                [_workflow_tag_payload(tag) for tag in tags],
                tool_name="list_tags_for_workflow",
                limit=_normalize_limit(
                    limit,
                    default=config.TRACECAT__LIMIT_DEFAULT,
                    max_limit=config.TRACECAT__LIMIT_CURSOR_MAX,
                ),
                cursor=cursor,
                filters={"workflow_id": workflow_id},
            )
            return _json(_serialize_paginated_response(page))
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


@mcp.tool()
async def list_case_tags(
    workspace_id: str,
    limit: int = config.TRACECAT__LIMIT_DEFAULT,
    cursor: str | None = None,
) -> str:
    """List case tag definitions in a workspace.

    Returns a JSON array of tag objects with `id`, `name`, `ref`, and `color`.
    """

    try:
        _, role = await _resolve_workspace_role(workspace_id)
        async with CaseTagsService.with_session(role=role) as svc:
            tags = await svc.list_workspace_tags()
            page = _paginate_items(
                [_case_tag_payload(tag) for tag in tags],
                tool_name="list_case_tags",
                limit=_normalize_limit(
                    limit,
                    default=config.TRACECAT__LIMIT_DEFAULT,
                    max_limit=config.TRACECAT__LIMIT_CURSOR_MAX,
                ),
                cursor=cursor,
            )
            return _json(_serialize_paginated_response(page))
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
async def list_tags_for_case(
    workspace_id: str,
    case_id: str,
    limit: int = config.TRACECAT__LIMIT_DEFAULT,
    cursor: str | None = None,
) -> str:
    """List tags attached to a case.

    Returns a JSON array of tag objects with `id`, `name`, `ref`, and `color`.
    """

    try:
        _, role = await _resolve_workspace_role(workspace_id)
        parsed_case_id = uuid.UUID(case_id)
        async with CaseTagsService.with_session(role=role) as svc:
            tags = await svc.list_tags_for_case(parsed_case_id)
            page = _paginate_items(
                [_case_tag_payload(tag) for tag in tags],
                tool_name="list_tags_for_case",
                limit=_normalize_limit(
                    limit,
                    default=config.TRACECAT__LIMIT_DEFAULT,
                    max_limit=config.TRACECAT__LIMIT_CURSOR_MAX,
                ),
                cursor=cursor,
                filters={"case_id": case_id},
            )
            return _json(_serialize_paginated_response(page))
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


@mcp.tool()
async def list_case_fields(
    workspace_id: str,
    limit: int = config.TRACECAT__LIMIT_DEFAULT,
    cursor: str | None = None,
) -> str:
    """List case field definitions in a workspace.

    Returns a JSON array of field objects with `id`, `type`, `description`,
    `nullable`, `default`, `reserved`, and `options`.
    """

    try:
        _, role = await _resolve_workspace_role(workspace_id)
        async with CaseFieldsService.with_session(role=role) as svc:
            columns = await svc.list_fields()
            field_schema = await svc.get_field_schema()
            page = _paginate_items(
                [
                    _case_field_payload(column, field_schema=field_schema)
                    for column in columns
                ],
                tool_name="list_case_fields",
                limit=_normalize_limit(
                    limit,
                    default=config.TRACECAT__LIMIT_DEFAULT,
                    max_limit=config.TRACECAT__LIMIT_CURSOR_MAX,
                ),
                cursor=cursor,
            )
            return _json(_serialize_paginated_response(page))
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


@mcp.tool()
async def list_tables(
    workspace_id: str,
    limit: int = config.TRACECAT__LIMIT_DEFAULT,
    cursor: str | None = None,
) -> str:
    """List workspace tables."""

    try:
        _, role = await _resolve_workspace_role(workspace_id)
        async with TablesService.with_session(role=role) as svc:
            tables = await svc.list_tables()
            page = _paginate_items(
                [{"id": str(table.id), "name": table.name} for table in tables],
                tool_name="list_tables",
                limit=_normalize_limit(
                    limit,
                    default=config.TRACECAT__LIMIT_DEFAULT,
                    max_limit=config.TRACECAT__LIMIT_CURSOR_MAX,
                ),
                cursor=cursor,
            )
            return _json(_serialize_paginated_response(page))
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
    cursor: str | None = None,
) -> str:
    """Search rows in a table."""

    try:
        _, role = await _resolve_workspace_role(workspace_id)
        limit = _normalize_limit(limit, default=100, max_limit=1000)
        async with TablesService.with_session(role=role) as svc:
            table = await svc.get_table(uuid.UUID(table_id))
            page = await svc.search_rows(
                table,
                search_term=search_term,
                limit=limit,
                cursor=cursor,
            )
            return _json(_serialize_paginated_response(page))
    except ValueError as e:
        raise ToolError(str(e)) from e
    except Exception as e:
        logger.error("Failed to search table rows", error=str(e))
        raise ToolError(f"Failed to search table rows: {e}") from None


@mcp.tool()
async def export_csv(
    workspace_id: str,
    table_id: str,
    include_header: bool = True,
    ctx: Context | None = None,
) -> str:
    """Export table data as a staged download URL.

    Args:
        workspace_id: The workspace ID.
        table_id: The table ID.
        include_header: Whether to include a header row (default True).

    Returns file metadata and a staged download URL.
    """

    SYSTEM_COLUMNS = {"id", "created_at", "updated_at"}

    try:
        _require_remote_mcp_context(ctx, tool_name="export_csv")
        _, role = await _resolve_workspace_role(workspace_id)
        async with TablesService.with_session(role=role) as svc:
            table = await svc.get_table(uuid.UUID(table_id))
            columns = [c.name for c in table.columns if c.name not in SYSTEM_COLUMNS]
            relative_path = _build_table_csv_file_name(table.name, table.id)

            output = StringIO()
            writer = csv.DictWriter(output, fieldnames=columns, extrasaction="ignore")
            if include_header and columns:
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

            csv_text = output.getvalue()
            result_payload = _build_csv_export_payload(
                table=table,
                relative_path=relative_path,
            )

        artifact_id = uuid.uuid4()
        expires_at = _workflow_file_artifact_expires_at()
        blob_key = (
            f"{role.workspace_id}/mcp/table-csv/{_get_context_session_id(ctx)}/"
            f"{artifact_id}/{PurePosixPath(relative_path).name}"
        )
        await blob.upload_file(
            csv_text.encode("utf-8"),
            key=blob_key,
            bucket=_workflow_file_bucket(),
            content_type="text/csv",
        )
        download_url = await blob.generate_presigned_download_url(
            key=blob_key,
            bucket=_workflow_file_bucket(),
            expiry=_mcp_file_transfer_ttl_seconds(),
            override_content_type="text/csv",
        )
        result_payload.update(
            {
                "download_url": download_url,
                "expires_at": expires_at.isoformat(),
                "transport": _get_context_transport(ctx),
            }
        )
        return _json(result_payload)
    except ToolError:
        raise
    except ValueError as e:
        raise ToolError(str(e)) from e
    except Exception as e:
        logger.error("Failed to export CSV", error=str(e))
        raise ToolError(f"Failed to export CSV: {e}") from None


@mcp.tool()
async def list_variables(
    workspace_id: str,
    environment: str = DEFAULT_SECRETS_ENVIRONMENT,
    limit: int = config.TRACECAT__LIMIT_DEFAULT,
    cursor: str | None = None,
) -> str:
    """List workspace variables."""

    try:
        _, role = await _resolve_workspace_role(workspace_id)
        async with VariablesService.with_session(role=role) as svc:
            variables = await svc.list_variables(environment=environment)
            page = _paginate_items(
                [
                    {
                        "id": str(variable.id),
                        "name": variable.name,
                        "description": variable.description,
                        "environment": variable.environment,
                        "keys": sorted(variable.values.keys()),
                    }
                    for variable in variables
                ],
                tool_name="list_variables",
                limit=_normalize_limit(
                    limit,
                    default=config.TRACECAT__LIMIT_DEFAULT,
                    max_limit=config.TRACECAT__LIMIT_CURSOR_MAX,
                ),
                cursor=cursor,
                filters={"environment": environment},
            )
            return _json(_serialize_paginated_response(page))
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
    limit: int = config.TRACECAT__LIMIT_DEFAULT,
    cursor: str | None = None,
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
            page = _paginate_items(
                result,
                tool_name="list_secrets_metadata",
                limit=_normalize_limit(
                    limit,
                    default=config.TRACECAT__LIMIT_DEFAULT,
                    max_limit=config.TRACECAT__LIMIT_CURSOR_MAX,
                ),
                cursor=cursor,
                filters={"environment": environment},
            )
            return _json(_serialize_paginated_response(page))
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
        raw_models = await _list_authoring_models(svc, workspace_id=role.workspace_id)
        default_model = await svc.get_default_model()
        provider_status_org = await svc.get_providers_status()

    async with VariablesService.with_session(role=role) as svc:
        variables = await svc.list_variables(environment=DEFAULT_SECRETS_ENVIRONMENT)

    workspace_inventory = await _load_secret_inventory(role)
    models = [_compact_authoring_model(model) for model in raw_models]
    models_by_provider: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for model in models:
        models_by_provider[str(model["model_provider"])].append(model)
    default_model_name, default_model_selection = (
        _default_model_selection_from_authoring_models(default_model, models)
    )
    default_model_ready_for_agent_presets = (
        any(
            _authoring_model_matches_selection(model, default_model_selection)
            for model in models
        )
        if default_model_selection is not None
        else False
        if default_model_name is not None
        else None
    )
    if (
        default_model_ready_for_agent_presets
        and default_model_selection is not None
        and default_model_selection.source_id is None
    ):
        default_model_ready_for_agent_presets = provider_status_org.get(
            default_model_selection.model_provider, False
        )
    agent_credentials = {
        "providers": [
            {
                "provider": provider,
                "configured_org": provider_status_org.get(provider, False),
                "ready_for_agent_presets": provider_status_org.get(provider, False),
                "model_count": len(provider_models),
                "model_names": sorted(
                    {str(model["model_name"]) for model in provider_models}
                ),
            }
            for provider, provider_models in sorted(models_by_provider.items())
        ],
        "default_model_ready_for_agent_presets": (
            default_model_ready_for_agent_presets
        ),
        "notes": [
            "Agent preset sessions require organization credentials for the selected provider.",
            "ready_for_agent_presets matches organization credential status.",
            (
                "Configured providers may still have no preset-eligible models in this workspace."
                if any(provider_status_org.values()) and not models
                else None
            ),
        ],
    }
    agent_credentials["notes"] = [
        note for note in agent_credentials["notes"] if note is not None
    ]

    integrations = await _build_integrations_inventory(role)
    truncated_sections, truncation = _truncate_named_sections(
        {
            "models": models,
            "agent_credentials.providers": agent_credentials["providers"],
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
            "integrations.mcp_integrations": integrations["mcp_integrations"],
            "integrations.oauth_providers": integrations["oauth_providers"],
        },
        limit=_MCP_EMBEDDED_COLLECTION_LIMIT,
    )

    integrations["mcp_integrations"] = truncated_sections[
        "integrations.mcp_integrations"
    ]
    integrations["oauth_providers"] = truncated_sections["integrations.oauth_providers"]

    return {
        "default_model": default_model_name,
        "default_model_selection": (
            default_model_selection.model_dump(mode="json")
            if default_model_selection is not None
            else None
        ),
        "models": truncated_sections["models"],
        "provider_status_org": provider_status_org,
        "agent_credentials": {
            **agent_credentials,
            "providers": truncated_sections["agent_credentials.providers"],
        },
        "workspace_variables": truncated_sections["workspace_variables"],
        "workspace_secret_hints": truncated_sections["workspace_secret_hints"],
        "integrations": integrations,
        "output_type_context": _build_output_type_context(),
        "notes": [
            "provider_status_org describes organization-scoped model credentials.",
            "Agent preset credential readiness is determined by organization-scoped provider credentials.",
        ],
        "truncation": truncation.model_dump(mode="json"),
    }


@mcp.tool()
async def list_integrations(workspace_id: str) -> str:
    """List workspace integrations useful for workflow and preset authoring."""

    try:
        _, role = await _resolve_workspace_role(workspace_id)
        inventory = await _build_integrations_inventory(role)
        truncated_sections, truncation = _truncate_named_sections(
            {
                "mcp_integrations": inventory["mcp_integrations"],
                "oauth_providers": inventory["oauth_providers"],
            },
            limit=_MCP_EMBEDDED_COLLECTION_LIMIT,
        )
        inventory["mcp_integrations"] = truncated_sections["mcp_integrations"]
        inventory["oauth_providers"] = truncated_sections["oauth_providers"]
        inventory["truncation"] = truncation.model_dump(mode="json")
        return _json(inventory)
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
    source_id: str | None = None,
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
        selection = await _resolve_agent_preset_model(
            role,
            source_id=source_id,
            model_name=model_name,
            model_provider=model_provider,
        )
        create_data: dict[str, Any] = {
            "name": name,
            "model_name": selection.model_name,
            "model_provider": selection.model_provider,
            "source_id": selection.source_id,
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


@mcp.tool()
async def update_agent_preset(
    workspace_id: str,
    preset_slug: str,
    name: str | None = None,
    slug: str | None = None,
    description: str | None = None,
    instructions: str | None = None,
    source_id: str | None = None,
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
    """Update an existing agent preset in the selected workspace."""

    try:
        _, role = await _resolve_workspace_role(workspace_id)
        update_data: dict[str, Any] = {}
        optional_fields = {
            "name": name,
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
        update_data.update(
            {
                field: value
                for field, value in optional_fields.items()
                if value is not None
            }
        )
        if source_id is not None or model_name is not None or model_provider is not None:
            selection = await _resolve_agent_preset_model(
                role,
                source_id=source_id,
                model_name=model_name,
                model_provider=model_provider,
            )
            update_data["model_name"] = selection.model_name
            update_data["model_provider"] = selection.model_provider
            update_data["source_id"] = selection.source_id
        params = AgentPresetUpdate.model_validate(update_data)
        async with AgentPresetService.with_session(role=role) as svc:
            preset = await svc.get_preset_by_slug(preset_slug)
            if not preset:
                raise ToolError(f"Agent preset '{preset_slug}' not found")
            updated_preset = await svc.update_preset(preset, params)
        return _json(
            AgentPresetRead.model_validate(updated_preset).model_dump(mode="json")
        )
    except ToolError:
        raise
    except ValidationError as e:
        raise ToolError(str(e)) from e
    except TracecatValidationError as e:
        raise ToolError(str(e)) from e
    except Exception as e:
        logger.error(
            "Failed to update agent preset", error=str(e), preset_slug=preset_slug
        )
        raise ToolError(f"Failed to update agent preset: {e}") from None


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
async def list_agent_presets(
    workspace_id: str,
    limit: int = config.TRACECAT__LIMIT_DEFAULT,
    cursor: str | None = None,
) -> str:
    """List saved agent preset slugs and names.

    Use `get_agent_preset` for the full preset definition.
    """
    try:
        _, role = await _resolve_workspace_role(workspace_id)
        async with AgentPresetService.with_session(role=role) as svc:
            presets = await svc.list_presets()
        page = _paginate_items(
            [
                {
                    "slug": preset.slug,
                    "name": preset.name,
                }
                for preset in presets
            ],
            tool_name="list_agent_presets",
            limit=_normalize_limit(
                limit,
                default=config.TRACECAT__LIMIT_DEFAULT,
                max_limit=config.TRACECAT__LIMIT_CURSOR_MAX,
            ),
            cursor=cursor,
        )
        return _json(_serialize_paginated_response(page))
    except ToolError:
        raise
    except Exception as e:
        logger.error("Failed to list agent presets", error=str(e))
        raise ToolError(f"Failed to list agent presets: {e}") from None


@mcp.tool()
async def get_agent_preset(workspace_id: str, preset_slug: str) -> str:
    """Get the full configuration for a saved agent preset by slug."""
    try:
        _, role = await _resolve_workspace_role(workspace_id)
        async with AgentPresetService.with_session(role=role) as svc:
            preset = await svc.get_preset_by_slug(preset_slug)
        if not preset:
            raise ToolError(f"Agent preset '{preset_slug}' not found")
        return _json(AgentPresetRead.model_validate(preset).model_dump(mode="json"))
    except ToolError:
        raise
    except Exception as e:
        logger.error(
            "Failed to get agent preset", error=str(e), preset_slug=preset_slug
        )
        raise ToolError(f"Failed to get agent preset: {e}") from None


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
