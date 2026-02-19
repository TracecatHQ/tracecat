"""Internal tools for agent execution.

Internal tools are system-level tools that:
- Have direct database access (run on the trusted side)
- Are NOT part of the registry (not usable in workflows)
- Are authorized via JWT claims (allowed_internal_tools)
- Receive context from JWT claims (preset_id, workspace_id, etc.)

This module provides the builder assistant tools that help users
configure agent presets through natural language.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from typing import Any, TypedDict

from pydantic import BaseModel, Field
from tracecat_registry import RegistryOAuthSecret, RegistrySecret

from tracecat import config
from tracecat.agent.common.types import MCPToolDefinition
from tracecat.agent.preset.schemas import AgentPresetRead, AgentPresetUpdate
from tracecat.agent.session.schemas import (
    AgentSessionRead,
    AgentSessionReadWithMessages,
)
from tracecat.agent.tokens import InternalToolContext, MCPTokenClaims
from tracecat.auth.types import Role
from tracecat.authz.scopes import SERVICE_PRINCIPAL_SCOPES
from tracecat.contexts import ctx_role
from tracecat.exceptions import (
    TracecatValidationError,
)
from tracecat.logger import logger
from tracecat.registry.actions.service import RegistryActionsService
from tracecat.secrets.constants import DEFAULT_SECRETS_ENVIRONMENT

# Tool name constants - these are the canonical names for internal tools
BUILDER_INTERNAL_TOOL_NAMES = [
    "internal.builder.get_preset_summary",
    "internal.builder.list_available_tools",
    "internal.builder.update_preset",
    "internal.builder.list_sessions",
    "internal.builder.get_session",
]

# Registry actions that are bundled with the builder assistant
# These are standard registry tools, not internal tools
BUILDER_BUNDLED_ACTIONS = [
    "core.table.download",
    "core.table.get_table_metadata",
    "core.table.list_tables",
    "core.table.search_rows",
    "tools.exa.get_contents",
    "tools.exa.get_research",
    "tools.exa.list_research",
    "tools.exa.research",
]


# Type for internal tool handlers
InternalToolHandler = Callable[
    [dict[str, Any], MCPTokenClaims], Awaitable[dict[str, Any]]
]


class AgentToolSummary(TypedDict):
    """Summary of an agent tool."""

    action_id: str
    description: str
    configured: bool
    is_configured: bool
    missing_requirements: list[str]
    already_in_preset: bool
    is_already_in_preset: bool


class InternalToolError(Exception):
    """Raised when an internal tool execution fails."""


def _secrets_to_requirements(secrets: list[Any]) -> list[dict[str, Any]]:
    """Convert registry secret objects to simple requirement metadata."""
    requirements: list[dict[str, Any]] = []
    for secret in secrets:
        if isinstance(secret, RegistrySecret):
            requirements.append(
                {
                    "name": secret.name,
                    "required_keys": list(secret.keys or []),
                    "optional": secret.optional,
                }
            )
        elif isinstance(secret, RegistryOAuthSecret):
            requirements.append(
                {
                    "name": secret.name,
                    "required_keys": [secret.token_name],
                    "optional": secret.optional,
                }
            )
    return requirements


def _evaluate_configuration(
    requirements: list[dict[str, Any]],
    workspace_inventory: dict[str, set[str]],
    org_inventory: dict[str, set[str]],
) -> tuple[bool, list[str]]:
    """Evaluate whether required secret names/keys are configured."""
    missing: list[str] = []
    for requirement in requirements:
        secret_name = requirement["name"]
        required_keys = set(requirement["required_keys"])
        if not required_keys and requirement.get("optional", False):
            continue
        keys = workspace_inventory.get(secret_name)
        if keys is None:
            keys = org_inventory.get(secret_name)
        if keys is None:
            missing.append(f"missing secret: {secret_name}")
            continue
        for key in sorted(required_keys):
            if key not in keys:
                missing.append(f"missing key: {secret_name}.{key}")
    return len(missing) == 0, missing


async def _load_secret_inventory(
    role: Role,
) -> tuple[dict[str, set[str]], dict[str, set[str]]]:
    """Load workspace/org secret key inventories from default environment."""
    from tracecat.secrets.service import SecretsService

    async with SecretsService.with_session(role=role) as svc:
        workspace_inventory: dict[str, set[str]] = {}
        org_inventory: dict[str, set[str]] = {}

        workspace_secrets = await svc.list_secrets()
        for secret in workspace_secrets:
            if secret.environment != DEFAULT_SECRETS_ENVIRONMENT:
                continue
            workspace_inventory[secret.name] = {
                kv.key for kv in svc.decrypt_keys(secret.encrypted_keys)
            }

        org_secrets = await svc.list_org_secrets()
        for secret in org_secrets:
            if secret.environment != DEFAULT_SECRETS_ENVIRONMENT:
                continue
            org_inventory[secret.name] = {
                kv.key for kv in svc.decrypt_keys(secret.encrypted_keys)
            }

    return workspace_inventory, org_inventory


def _get_preset_id(context: InternalToolContext | None) -> uuid.UUID:
    """Extract preset_id from internal tool context."""
    if context is None or context.preset_id is None:
        raise InternalToolError("preset_id is required in internal_tool_context")
    return context.preset_id


def _build_role(claims: MCPTokenClaims) -> Role:
    """Build a Role from MCP token claims."""
    return Role(
        type="service",
        service_id="tracecat-mcp",
        workspace_id=claims.workspace_id,
        organization_id=claims.organization_id,
        user_id=claims.user_id,
        scopes=SERVICE_PRINCIPAL_SCOPES["tracecat-mcp"],
    )


async def _get_action_configuration(
    svc: RegistryActionsService,
    action_name: str,
    workspace_inventory: dict[str, set[str]],
    org_inventory: dict[str, set[str]],
) -> tuple[bool, list[str]]:
    """Return (configured, missing_requirements) for an action."""
    indexed = await svc.get_action_from_index(action_name)
    if indexed is None:
        return False, [f"action not found: {action_name}"]

    secrets = svc.aggregate_secrets_from_manifest(indexed.manifest, action_name)
    requirements = _secrets_to_requirements(secrets)
    return _evaluate_configuration(requirements, workspace_inventory, org_inventory)


# -----------------------------------------------------------------------------
# Internal Tool Handlers
# -----------------------------------------------------------------------------


async def get_preset_summary(
    args: dict[str, Any], claims: MCPTokenClaims
) -> dict[str, Any]:
    """Return the latest configuration for this agent preset, including tools and approval rules."""
    from tracecat.agent.preset.service import AgentPresetService

    preset_id = _get_preset_id(claims.internal_tool_context)
    role = _build_role(claims)
    ctx_role.set(role)

    async with AgentPresetService.with_session(role=role) as service:
        preset = await service.get_preset(preset_id)
        if not preset:
            raise InternalToolError(f"Agent preset with ID '{preset_id}' not found")

    return AgentPresetRead.model_validate(preset).model_dump(mode="json")


async def list_available_tools(
    args: dict[str, Any], claims: MCPTokenClaims
) -> dict[str, Any]:
    """Return the list of available actions in the registry index."""
    from tracecat.agent.preset.service import AgentPresetService

    query = args.get("query", "")
    if not query or len(query) < 1:
        raise InternalToolError("query parameter is required (1-100 characters)")
    if len(query) > 100:
        raise InternalToolError("query parameter must be at most 100 characters")

    preset_id = _get_preset_id(claims.internal_tool_context)
    role = _build_role(claims)
    ctx_role.set(role)
    workspace_inventory, org_inventory = await _load_secret_inventory(role)
    preset_action_set: set[str] = set()

    async with AgentPresetService.with_session(role=role) as preset_service:
        preset = await preset_service.get_preset(preset_id)
        if preset:
            preset_action_set = set(preset.actions or [])

    # Search using registry index instead of RegistryAction table
    async with RegistryActionsService.with_session(role=role) as svc:
        entries = await svc.search_actions_from_index(query)
        logger.info(
            "Listed available actions from index",
            query_term=query,
            count=len(entries),
        )
        tools: list[AgentToolSummary] = []
        for entry, _ in entries:
            action_name = f"{entry.namespace}.{entry.name}"
            configured, missing_requirements = await _get_action_configuration(
                svc,
                action_name,
                workspace_inventory,
                org_inventory,
            )
            tools.append(
                AgentToolSummary(
                    action_id=action_name,
                    description=entry.description,
                    configured=configured,
                    is_configured=configured,
                    missing_requirements=missing_requirements,
                    already_in_preset=action_name in preset_action_set,
                    is_already_in_preset=action_name in preset_action_set,
                )
            )
        return {"tools": tools}


async def update_preset(args: dict[str, Any], claims: MCPTokenClaims) -> dict[str, Any]:
    """Patch selected fields on the agent preset and return the updated record.

    Only include fields you want to change - omit unchanged fields so they remain untouched.
    Supported fields include:
    - `instructions`: system prompt text.
    - `actions`: list of allowed tool/action identifiers.
    - `namespaces`: optional namespaces to scope dynamic tool discovery.
    - `tool_approvals`: map of `{tool_name: bool}` where `true` means auto-run with no approval and `false` requires manual approval.
    """
    from tracecat.agent.preset.service import AgentPresetService

    preset_id = _get_preset_id(claims.internal_tool_context)
    role = _build_role(claims)
    ctx_role.set(role)

    # Validate and parse the update params
    try:
        params = AgentPresetUpdate.model_validate(args)
    except Exception as e:
        raise InternalToolError(f"Invalid update parameters: {e}") from e

    if not params.model_fields_set:
        raise InternalToolError("Provide at least one field to update.")

    async with AgentPresetService.with_session(role=role) as service:
        preset = await service.get_preset(preset_id)
        if not preset:
            raise InternalToolError(f"Agent preset with ID '{preset_id}' not found")

        if params.actions is not None:
            current_actions = set(preset.actions or [])
            proposed_actions = set(params.actions)
            added_actions = sorted(proposed_actions - current_actions)
            if added_actions:
                workspace_inventory, org_inventory = await _load_secret_inventory(role)
                async with RegistryActionsService.with_session(
                    role=role
                ) as registry_svc:
                    unconfigured: list[dict[str, Any]] = []
                    for action_name in added_actions:
                        configured, missing = await _get_action_configuration(
                            registry_svc,
                            action_name,
                            workspace_inventory,
                            org_inventory,
                        )
                        if not configured:
                            unconfigured.append(
                                {"action": action_name, "missing_requirements": missing}
                            )
                if unconfigured:
                    raise InternalToolError(
                        "Cannot add unconfigured tools. Missing requirements: "
                        + ", ".join(
                            f"{item['action']} ({'; '.join(item['missing_requirements'])})"
                            for item in unconfigured
                        )
                    )
        try:
            updated = await service.update_preset(preset, params)
        except TracecatValidationError as error:
            # Surface builder validation issues as errors that the agent can retry
            raise InternalToolError(str(error)) from error

    return AgentPresetRead.model_validate(updated).model_dump(mode="json")


async def list_sessions(args: dict[str, Any], claims: MCPTokenClaims) -> dict[str, Any]:
    """List agent sessions where this agent preset is being used by end users."""
    from tracecat.agent.session.service import AgentSessionService
    from tracecat.agent.session.types import AgentSessionEntity

    preset_id = _get_preset_id(claims.internal_tool_context)
    role = _build_role(claims)
    ctx_role.set(role)

    limit = args.get("limit", 50)
    if not isinstance(limit, int) or limit < 1 or limit > 100:
        limit = 50

    try:
        async with AgentSessionService.with_session(role=role) as service:
            if service.role.user_id is None:
                raise InternalToolError(
                    "Unable to list sessions: authentication required."
                )
            sessions = await service.list_sessions(
                created_by=service.role.user_id,
                entity_type=AgentSessionEntity.AGENT_PRESET,
                entity_id=preset_id,
                limit=limit,
            )
            return {
                "sessions": [
                    AgentSessionRead.model_validate(
                        session, from_attributes=True
                    ).model_dump(mode="json")
                    for session in sessions
                ]
            }
    except InternalToolError:
        raise
    except Exception as e:
        logger.error("Failed to list sessions", error=str(e), preset_id=str(preset_id))
        raise InternalToolError("Unable to list sessions at this time.") from e


async def get_session(args: dict[str, Any], claims: MCPTokenClaims) -> dict[str, Any]:
    """Get the full message history and metadata for a specific agent session."""
    from tracecat.agent.session.service import AgentSessionService

    preset_id = _get_preset_id(claims.internal_tool_context)
    role = _build_role(claims)
    ctx_role.set(role)

    session_id_str = args.get("session_id")
    if not session_id_str:
        raise InternalToolError("session_id parameter is required")

    try:
        session_id = uuid.UUID(session_id_str)
    except ValueError as e:
        raise InternalToolError(f"Invalid session_id format: {session_id_str}") from e

    try:
        async with AgentSessionService.with_session(role=role) as service:
            session = await service.get_session(session_id)
            if not session or str(session.entity_id) != str(preset_id):
                raise InternalToolError(f"Session {session_id} not found.")

            messages = await service.list_messages(session.id)

            return AgentSessionReadWithMessages(
                id=session.id,
                workspace_id=session.workspace_id,
                title=session.title,
                created_by=session.created_by,
                entity_type=session.entity_type,
                entity_id=session.entity_id,
                tools=session.tools,
                agent_preset_id=session.agent_preset_id,
                harness_type=session.harness_type,
                created_at=session.created_at,
                updated_at=session.updated_at,
                last_stream_id=session.last_stream_id,
                messages=messages,
            ).model_dump(mode="json")
    except InternalToolError:
        raise
    except Exception as e:
        logger.error("Failed to get session", error=str(e), session_id=session_id_str)
        raise InternalToolError(f"Unable to retrieve session {session_id_str}.") from e


# -----------------------------------------------------------------------------
# Internal Tool Registry
# -----------------------------------------------------------------------------

INTERNAL_TOOL_HANDLERS: dict[str, InternalToolHandler] = {
    "internal.builder.get_preset_summary": get_preset_summary,
    "internal.builder.list_available_tools": list_available_tools,
    "internal.builder.update_preset": update_preset,
    "internal.builder.list_sessions": list_sessions,
    "internal.builder.get_session": get_session,
}


# -----------------------------------------------------------------------------
# Tool Definitions for MCP
# -----------------------------------------------------------------------------


class _ListAvailableToolsParams(BaseModel):
    """Parameters for list_available_tools."""

    query: str = Field(
        ...,
        description="The query to search for actions.",
        min_length=1,
        max_length=100,
    )


class _ListSessionsParams(BaseModel):
    """Parameters for list_sessions."""

    limit: int = Field(
        default=config.TRACECAT__LIMIT_AGENT_SESSIONS_DEFAULT,
        description="Maximum number of sessions to return.",
        ge=config.TRACECAT__LIMIT_MIN,
        le=config.TRACECAT__LIMIT_CURSOR_MAX,
    )


class _GetSessionParams(BaseModel):
    """Parameters for get_session."""

    session_id: str = Field(
        ...,
        description="The UUID of the session to retrieve.",
    )


def get_builder_internal_tool_definitions() -> dict[str, MCPToolDefinition]:
    """Return MCPToolDefinition for each builder internal tool.

    These definitions are used by the proxy MCP server to expose the tools
    to the agent runtime with proper JSON schemas.
    """
    return {
        "internal.builder.get_preset_summary": MCPToolDefinition(
            name="internal.builder.get_preset_summary",
            description="Return the latest configuration for this agent preset, including tools and approval rules.",
            parameters_json_schema={
                "type": "object",
                "properties": {},
                "required": [],
            },
        ),
        "internal.builder.list_available_tools": MCPToolDefinition(
            name="internal.builder.list_available_tools",
            description="Return available actions with configuration readiness metadata. Use this to suggest useful tools and only add configured ones.",
            parameters_json_schema=_ListAvailableToolsParams.model_json_schema(),
        ),
        "internal.builder.update_preset": MCPToolDefinition(
            name="internal.builder.update_preset",
            description=(
                "Patch selected fields on the agent preset and return the updated record. "
                "Only include fields you want to change. "
                "Supported fields: instructions (system prompt), actions (list of tool identifiers), "
                "namespaces (scope for tool discovery), tool_approvals"
                " (map of tool_name to bool. If true, require human-in-the-loop approval. "
                "If false, auto-run tool without approval). "
                "New actions can only be added when they are configured (required secrets/keys available)."
            ),
            parameters_json_schema=AgentPresetUpdate.model_json_schema(),
        ),
        "internal.builder.list_sessions": MCPToolDefinition(
            name="internal.builder.list_sessions",
            description="List agent sessions where this agent preset is being used by end users.",
            parameters_json_schema=_ListSessionsParams.model_json_schema(),
        ),
        "internal.builder.get_session": MCPToolDefinition(
            name="internal.builder.get_session",
            description="Get the full message history and metadata for a specific agent session.",
            parameters_json_schema=_GetSessionParams.model_json_schema(),
        ),
    }
