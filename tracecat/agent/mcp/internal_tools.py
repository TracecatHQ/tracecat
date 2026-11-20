"""Internal tools for agent execution.

Internal tools are system-level tools that:
- Have direct database access (run on the trusted side)
- Are NOT part of the registry (not usable in workflows)
- Are authorized via JWT claims (allowed_internal_tools)
- Receive context from JWT claims (preset_id, workspace_id, etc.)

This module provides the builder assistant tools that help users
configure agent presets through natural language, and the workflow tools
that let any agent start, check and cancel workflow runs without waiting.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable, Sequence
from typing import TYPE_CHECKING, Any, Literal, Self, TypedDict, cast

import orjson
from pydantic import BaseModel, Field, ValidationError, model_validator
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from temporalio.client import WorkflowExecutionStatus, WorkflowFailureError
from tracecat_registry import RegistryOAuthSecret, RegistrySecret

from tracecat import config
from tracecat.agent.common.types import MCPToolDefinition
from tracecat.agent.preset.schemas import AgentPresetUpdate
from tracecat.agent.session.schemas import (
    AgentSessionRead,
    AgentSessionReadWithMessages,
)
from tracecat.agent.session.types import AgentSessionEntity
from tracecat.agent.session.views import build_session_read
from tracecat.agent.tokens import InternalToolContext, MCPTokenClaims
from tracecat.auth.types import Role
from tracecat.authz.controls import check_scopes
from tracecat.authz.scopes import SERVICE_PRINCIPAL_SCOPES
from tracecat.contexts import ctx_role
from tracecat.db.engine import get_async_session_auth_context_manager
from tracecat.db.models import ServiceAccount
from tracecat.exceptions import (
    ScopeDeniedError,
    TracecatNotFoundError,
    TracecatValidationError,
)
from tracecat.identifiers.workflow import (
    WorkflowExecutionID,
    WorkflowID,
    WorkflowUUID,
    exec_id_to_parts,
)
from tracecat.integrations.enums import OAuthGrantType
from tracecat.integrations.schemas import ProviderKey
from tracecat.integrations.service import IntegrationService
from tracecat.logger import logger
from tracecat.registry.actions.service import RegistryActionsService
from tracecat.secrets.constants import DEFAULT_SECRETS_ENVIRONMENT
from tracecat.storage.object import (
    CollectionObject,
    ExternalObject,
    StoredObject,
    retrieve_stored_object,
)

if TYPE_CHECKING:
    from tracecat.workflow.management.management import WorkflowsManagementService

# Tool name constants - these are the canonical names for internal tools
BUILDER_INTERNAL_TOOL_NAMES = [
    "internal.builder.get_preset_summary",
    "internal.builder.list_available_tools",
    "internal.builder.update_preset",
    "internal.builder.list_sessions",
    "internal.builder.get_session",
]

# Workflow tools any agent can opt into by listing them in its actions.
# They run in the tool-serving process, so a run never holds an executor slot
# or a turn while it waits on a workflow.
WORKFLOW_INTERNAL_TOOL_SCOPES: dict[str, str] = {
    "internal.workflows.start": "workflow:execute",
    "internal.workflows.get": "workflow:read",
    "internal.workflows.cancel": "workflow:terminate",
}
"""The scope a user needs to be offered each workflow tool, as for the MCP tools."""
WORKFLOW_INTERNAL_TOOL_NAMES = list(WORKFLOW_INTERNAL_TOOL_SCOPES)

# Registry actions that are bundled with the builder assistant
# These are standard registry tools, not internal tools
BUILDER_BUNDLED_ACTIONS = [
    "core.table.download",
    "core.table.get_table_metadata",
    "core.table.list_tables",
    "core.table.search_rows",
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
    missing_requirements: list[str]
    already_in_preset: bool


class InternalToolError(Exception):
    """Raised when an internal tool execution fails."""


class SecretRequirement(TypedDict):
    """Configuration requirement for a regular secret."""

    type: Literal["secret"]
    name: str
    required_keys: list[str]
    optional: bool


class OAuthRequirement(TypedDict):
    """Configuration requirement for a workspace OAuth integration."""

    type: Literal["oauth"]
    provider_id: str
    grant_type: str
    optional: bool


Requirement = SecretRequirement | OAuthRequirement


def _secrets_to_requirements(secrets: list[Any]) -> list[Requirement]:
    """Convert registry secret objects to simple requirement metadata."""
    requirements: list[Requirement] = []
    for secret in secrets:
        if isinstance(secret, RegistrySecret):
            requirements.append(
                {
                    "type": "secret",
                    "name": secret.name,
                    "required_keys": list(secret.keys or []),
                    "optional": secret.optional,
                }
            )
        elif isinstance(secret, RegistryOAuthSecret):
            requirements.append(
                {
                    "type": "oauth",
                    "provider_id": secret.provider_id,
                    "grant_type": secret.grant_type,
                    "optional": secret.optional,
                }
            )
    return requirements


def _evaluate_configuration(
    requirements: Sequence[Requirement | dict[str, Any]],
    workspace_inventory: dict[str, set[str]],
    org_inventory: dict[str, set[str]],
    oauth_inventory: set[ProviderKey],
) -> tuple[bool, list[str]]:
    """Evaluate whether required secret names/keys are configured."""
    missing: list[str] = []
    for requirement in requirements:
        if requirement.get("optional", False):
            continue
        requirement_type = requirement.get("type", "secret")
        if requirement_type == "oauth":
            oauth_requirement = cast(OAuthRequirement | dict[str, Any], requirement)
            provider_key = ProviderKey(
                id=cast(str, oauth_requirement["provider_id"]),
                grant_type=OAuthGrantType(cast(str, oauth_requirement["grant_type"])),
            )
            if provider_key not in oauth_inventory:
                missing.append(
                    "missing oauth integration: "
                    f"{provider_key.id} ({provider_key.grant_type.value})"
                )
            continue

        secret_requirement = cast(SecretRequirement | dict[str, Any], requirement)
        secret_name = cast(str, secret_requirement["name"])
        required_keys = set(cast(list[str], secret_requirement["required_keys"]))
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
    from tracecat.secrets.service import SecretsService, secret_key_names

    async with SecretsService.with_session(role=role) as svc:
        workspace_inventory: dict[str, set[str]] = {}
        org_inventory: dict[str, set[str]] = {}

        workspace_secrets = await svc.list_secrets()
        for secret in workspace_secrets:
            if secret.environment != DEFAULT_SECRETS_ENVIRONMENT:
                continue
            workspace_inventory[secret.name] = set(secret_key_names(svc, secret))

        org_secrets = await svc.list_org_secrets()
        for secret in org_secrets:
            if secret.environment != DEFAULT_SECRETS_ENVIRONMENT:
                continue
            org_inventory[secret.name] = set(secret_key_names(svc, secret))

    return workspace_inventory, org_inventory


async def _load_oauth_inventory(role: Role) -> set[ProviderKey]:
    """Load configured workspace OAuth integrations."""
    async with IntegrationService.with_session(role=role) as svc:
        integrations = await svc.list_integrations()
    return {
        ProviderKey(id=integration.provider_id, grant_type=integration.grant_type)
        for integration in integrations
    }


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


async def _caller_role(claims: MCPTokenClaims) -> Role:
    """Resolve the role a workflow tool acts as.

    Act as the caller behind the token with their real scopes, as the MCP
    server does, so a started run and every scope check follow the caller's
    RBAC: a user with their workspace scopes, or a service account with its
    own scopes. Only tokens with neither (webhook and case-triggered runs,
    which already run as the service principal) use the MCP service principal.
    """
    if claims.user_id is not None:
        # Local import: tracecat.auth.credentials imports the agent stack,
        # which imports this module.
        from tracecat.auth.credentials import compute_effective_scopes

        role = Role(
            type="user",
            service_id="tracecat-mcp",
            workspace_id=claims.workspace_id,
            organization_id=claims.organization_id,
            user_id=claims.user_id,
        )
        return role.model_copy(update={"scopes": await compute_effective_scopes(role)})
    if claims.service_account_id is not None:
        return await _service_account_role(claims, claims.service_account_id)
    return _build_role(claims)


async def _service_account_role(
    claims: MCPTokenClaims, service_account_id: uuid.UUID
) -> Role:
    """Load an active service account's scopes, as API key auth does."""
    async with get_async_session_auth_context_manager() as session:
        result = await session.execute(
            select(ServiceAccount)
            .where(
                ServiceAccount.id == service_account_id,
                ServiceAccount.organization_id == claims.organization_id,
            )
            .options(selectinload(ServiceAccount.scopes))
        )
        service_account = result.scalar_one_or_none()
    if service_account is None or service_account.disabled_at is not None:
        raise InternalToolError("The service account behind this agent is not active")
    return Role(
        type="service_account",
        service_id="tracecat-mcp",
        workspace_id=claims.workspace_id,
        organization_id=claims.organization_id,
        bound_workspace_id=service_account.workspace_id,
        service_account_id=service_account.id,
        scopes=frozenset(scope.name for scope in service_account.scopes),
    )


async def _get_action_configuration(
    svc: RegistryActionsService,
    action_name: str,
    workspace_inventory: dict[str, set[str]],
    org_inventory: dict[str, set[str]],
    oauth_inventory: set[ProviderKey],
) -> tuple[bool, list[str]]:
    """Return (configured, missing_requirements) for an action."""
    indexed = await svc.get_action_from_index(action_name)
    if indexed is None:
        return False, [f"action not found: {action_name}"]

    secrets = svc.aggregate_secrets_from_manifest(indexed.manifest, action_name)
    requirements = _secrets_to_requirements(secrets)
    return _evaluate_configuration(
        requirements,
        workspace_inventory,
        org_inventory,
        oauth_inventory,
    )


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
        preset_read = await service.build_preset_read(preset)

    return preset_read.model_dump(mode="json")


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
    oauth_inventory = await _load_oauth_inventory(role)
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
                oauth_inventory,
            )
            tools.append(
                AgentToolSummary(
                    action_id=action_name,
                    description=entry.description,
                    configured=configured,
                    missing_requirements=missing_requirements,
                    already_in_preset=action_name in preset_action_set,
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
                oauth_inventory = await _load_oauth_inventory(role)
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
                            oauth_inventory,
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
        updated_read = await service.build_preset_read(updated)

    return updated_read.model_dump(mode="json")


async def list_sessions(args: dict[str, Any], claims: MCPTokenClaims) -> dict[str, Any]:
    """List workspace sessions where this agent preset is used."""
    from tracecat.agent.session.service import AgentSessionService

    preset_id = _get_preset_id(claims.internal_tool_context)
    role = _build_role(claims)
    ctx_role.set(role)

    limit = args.get("limit", 50)
    if not isinstance(limit, int) or limit < 1 or limit > 100:
        limit = 50

    created_by: uuid.UUID | None = None
    if created_by_value := args.get("created_by"):
        if isinstance(created_by_value, uuid.UUID):
            created_by = created_by_value
        elif isinstance(created_by_value, str):
            try:
                created_by = uuid.UUID(created_by_value)
            except ValueError as e:
                raise InternalToolError(
                    f"Invalid created_by format: {created_by_value}"
                ) from e
        else:
            raise InternalToolError(f"Invalid created_by format: {created_by_value}")

    try:
        async with AgentSessionService.with_session(role=role) as service:
            sessions = await service.list_sessions(
                created_by=created_by,
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
            if (
                not session
                or AgentSessionEntity(session.entity_type)
                is not AgentSessionEntity.AGENT_PRESET
                or session.entity_id != preset_id
            ):
                raise InternalToolError(f"Session {session_id} not found.")

            messages = await service.list_messages(session.id)
            return AgentSessionReadWithMessages(
                **build_session_read(session, role).model_dump(),
                messages=messages,
            ).model_dump(mode="json")
    except InternalToolError:
        raise
    except Exception as e:
        logger.error("Failed to get session", error=str(e), session_id=session_id_str)
        raise InternalToolError(f"Unable to retrieve session {session_id_str}.") from e


# -----------------------------------------------------------------------------
# Workflow Tool Handlers
#
# These share the service layer and scope checks with the MCP server tools
# `run_workflow`, `get_workflow_execution` and `cancel_workflow_execution`.
# -----------------------------------------------------------------------------


def _parse_args[T: BaseModel](model: type[T], args: dict[str, Any]) -> T:
    try:
        return model.model_validate(args)
    except ValidationError as e:
        raise InternalToolError(f"Invalid arguments: {e}") from e


def _scope_error(error: ScopeDeniedError) -> InternalToolError:
    return InternalToolError(
        f"Missing required scope: {', '.join(error.required_scopes)}"
    )


def _require_scope(role: Role, scope: str) -> None:
    try:
        check_scopes(role, scope)
    except ScopeDeniedError as e:
        raise _scope_error(e) from e


async def _require_workspace_execution(
    role: Role, execution_id: WorkflowExecutionID
) -> None:
    """Raise unless the execution's workflow belongs to the role's workspace."""
    from tracecat.workflow.management.management import WorkflowsManagementService

    wf_id, _ = exec_id_to_parts(execution_id)
    async with WorkflowsManagementService.with_session(role=role) as svc:
        workflow = await svc.get_workflow(wf_id)
    if workflow is None:
        raise InternalToolError(f"Workflow execution {execution_id} not found")


async def _resolve_workflow_id(
    svc: WorkflowsManagementService, params: _StartWorkflowParams
) -> WorkflowID:
    if params.workflow_id is not None:
        return WorkflowUUID.new(params.workflow_id)
    alias = cast(str, params.workflow_alias)
    workflow_id = await svc.resolve_workflow_alias(
        alias, use_committed=not params.use_draft
    )
    if workflow_id is None:
        raise InternalToolError(f"No workflow found with alias '{alias}'")
    return workflow_id


# Same ceiling as the MCP server's largest action result window.
MAX_WORKFLOW_RESULT_BYTES = 1024 * 1024


async def _bounded_result(stored: StoredObject) -> dict[str, Any]:
    """Return a run's result, or a size note when it is too large to inline.

    A large result is never downloaded into the tool-serving process.
    """
    match stored:
        case ExternalObject(ref=ref) if ref.size_bytes > MAX_WORKFLOW_RESULT_BYTES:
            return {
                "result_omitted": (
                    f"The result is {ref.size_bytes} bytes, over the "
                    f"{MAX_WORKFLOW_RESULT_BYTES}-byte limit for this tool."
                )
            }
        case CollectionObject(count=count):
            return {
                "result_omitted": (
                    f"The result is a collection of {count} items, too large "
                    "to return through this tool."
                )
            }
        case _:
            return {"result": await retrieve_stored_object(stored)}


async def start_workflow(
    args: dict[str, Any], claims: MCPTokenClaims
) -> dict[str, Any]:
    """Start a workflow run without waiting for it and return its execution ID."""
    # Local import: the workflow services import the agent stack, which imports
    # this module (same reason as the builder tools' service imports).
    from tracecat.workflow.management.management import WorkflowsManagementService

    params = _parse_args(_StartWorkflowParams, args)
    role = await _caller_role(claims)
    ctx_role.set(role)

    try:
        async with WorkflowsManagementService.with_session(role=role) as svc:
            workflow_id = await _resolve_workflow_id(svc, params)
            response = await svc.run_workflow(
                workflow_id,
                inputs=params.inputs,
                use_draft=params.use_draft,
                version=params.version,
            )
    except ScopeDeniedError as e:
        raise _scope_error(e) from e
    except TracecatValidationError as e:
        detail = orjson.dumps(e.detail, default=str).decode() if e.detail else ""
        raise InternalToolError(f"{e} {detail}".strip()) from e
    except (TracecatNotFoundError, ValueError) as e:
        raise InternalToolError(str(e)) from e

    return {
        "workflow_id": WorkflowUUID.new(response["wf_id"]).short(),
        "workflow_execution_id": response["wf_exec_id"],
        "message": response["message"],
    }


async def get_workflow_run(
    args: dict[str, Any], claims: MCPTokenClaims
) -> dict[str, Any]:
    """Return a workflow run's status, plus its result or error once it ends."""
    from tracecat.workflow.executions.service import WorkflowExecutionsService

    params = _parse_args(_WorkflowRunParams, args)
    execution_id = params.workflow_execution_id
    role = await _caller_role(claims)
    ctx_role.set(role)

    _require_scope(role, "workflow:read")
    await _require_workspace_execution(role, execution_id)

    exec_service = await WorkflowExecutionsService.connect(role=role)
    execution = await exec_service.get_execution(execution_id)
    if execution is None:
        raise InternalToolError(f"Workflow execution {execution_id} not found")

    run: dict[str, Any] = {
        "workflow_execution_id": execution_id,
        "status": execution.status.name if execution.status else "UNKNOWN",
        "start_time": execution.start_time,
        "close_time": execution.close_time,
    }
    if execution.status not in (
        WorkflowExecutionStatus.COMPLETED,
        WorkflowExecutionStatus.FAILED,
    ):
        return run
    try:
        stored = await exec_service.handle(execution_id).result()
    except WorkflowFailureError as e:
        run["error"] = WorkflowExecutionsService.format_failure_cause(e.cause)
        return run
    run.update(await _bounded_result(stored))
    return run


async def cancel_workflow_run(
    args: dict[str, Any], claims: MCPTokenClaims
) -> dict[str, Any]:
    """Request graceful cancellation of a workflow run."""
    from tracecat.workflow.executions.service import WorkflowExecutionsService

    params = _parse_args(_WorkflowRunParams, args)
    execution_id = params.workflow_execution_id
    role = await _caller_role(claims)
    ctx_role.set(role)

    _require_scope(role, "workflow:terminate")
    await _require_workspace_execution(role, execution_id)

    exec_service = await WorkflowExecutionsService.connect(role=role)
    try:
        await exec_service.cancel_workflow_execution(execution_id)
    except ValueError as e:
        raise InternalToolError(str(e)) from e
    return {
        "workflow_execution_id": execution_id,
        "message": (
            f"Cancellation requested for {execution_id}. "
            "Call internal.workflows.get to confirm it reached CANCELED."
        ),
    }


# -----------------------------------------------------------------------------
# Internal Tool Registry
# -----------------------------------------------------------------------------

INTERNAL_TOOL_HANDLERS: dict[str, InternalToolHandler] = {
    "internal.builder.get_preset_summary": get_preset_summary,
    "internal.builder.list_available_tools": list_available_tools,
    "internal.builder.update_preset": update_preset,
    "internal.builder.list_sessions": list_sessions,
    "internal.builder.get_session": get_session,
    "internal.workflows.start": start_workflow,
    "internal.workflows.get": get_workflow_run,
    "internal.workflows.cancel": cancel_workflow_run,
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
    created_by: uuid.UUID | None = Field(
        default=None,
        description="Filter by session creator. Omit to list the entire workspace.",
    )


class _GetSessionParams(BaseModel):
    """Parameters for get_session."""

    session_id: str = Field(
        ...,
        description="The UUID of the session to retrieve.",
    )


class _StartWorkflowParams(BaseModel):
    """Parameters for internal.workflows.start."""

    workflow_id: str | None = Field(
        default=None,
        description="Workflow ID (short `wf_...` or UUID). Give this or `workflow_alias`.",
    )
    workflow_alias: str | None = Field(
        default=None,
        description="Workflow alias. Give this or `workflow_id`.",
    )
    inputs: dict[str, Any] | None = Field(
        default=None,
        description="Trigger inputs for the run, matching the workflow's input schema.",
    )
    use_draft: bool = Field(
        default=False,
        description=(
            "Run the current draft instead of a published version. "
            "Defaults to false (published)."
        ),
    )
    version: int | None = Field(
        default=None,
        description=(
            "Published version to run. Null runs the current published version. "
            "Ignored when `use_draft` is true."
        ),
    )

    @model_validator(mode="after")
    def _one_target(self) -> Self:
        if (self.workflow_id is None) == (self.workflow_alias is None):
            raise ValueError("Give exactly one of workflow_id or workflow_alias")
        return self


class _WorkflowRunParams(BaseModel):
    """Parameters for internal.workflows.get and internal.workflows.cancel."""

    workflow_execution_id: WorkflowExecutionID = Field(
        ...,
        description=(
            "The run's `workflow_execution_id` (`wf_.../exec_...`), "
            "as returned by internal.workflows.start."
        ),
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
            description=(
                "List workspace agent sessions where this preset is being used. "
                "Returns all teammates by default; optionally filter by creator."
            ),
            parameters_json_schema=_ListSessionsParams.model_json_schema(),
        ),
        "internal.builder.get_session": MCPToolDefinition(
            name="internal.builder.get_session",
            description="Get the full message history and metadata for a specific agent session.",
            parameters_json_schema=_GetSessionParams.model_json_schema(),
        ),
    }


def get_workflow_internal_tool_definitions() -> dict[str, MCPToolDefinition]:
    """Return MCPToolDefinition for each workflow internal tool."""
    return {
        "internal.workflows.start": MCPToolDefinition(
            name="internal.workflows.start",
            description=(
                "Start a workflow run and return its `workflow_execution_id` at once, "
                "without waiting for the run to finish. Check on it later with "
                "internal.workflows.get."
            ),
            parameters_json_schema=_StartWorkflowParams.model_json_schema(),
        ),
        "internal.workflows.get": MCPToolDefinition(
            name="internal.workflows.get",
            description=(
                "Get a workflow run's status (RUNNING, COMPLETED, FAILED, CANCELED, "
                "TERMINATED, TIMED_OUT). Includes `result` when COMPLETED and "
                "`error` when FAILED. Returns at once; call again later while RUNNING."
            ),
            parameters_json_schema=_WorkflowRunParams.model_json_schema(),
        ),
        "internal.workflows.cancel": MCPToolDefinition(
            name="internal.workflows.cancel",
            description=(
                "Request graceful cancellation of a workflow run. The run may take a "
                "moment to stop; confirm with internal.workflows.get."
            ),
            parameters_json_schema=_WorkflowRunParams.model_json_schema(),
        ),
    }


def get_internal_tool_definitions() -> dict[str, MCPToolDefinition]:
    """Return MCPToolDefinition for every internal tool."""
    return {
        **get_builder_internal_tool_definitions(),
        **get_workflow_internal_tool_definitions(),
    }
