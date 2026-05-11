from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any

from pydantic import (
    UUID4,
    BaseModel,
)
from temporalio import activity
from temporalio.exceptions import ApplicationError

from tracecat.agent.common.types import (
    MCPHttpServerConfig,
    MCPServerConfig,
    MCPToolDefinition,
    is_http_mcp_server,
)
from tracecat.agent.mcp.internal_tools import (
    BUILDER_BUNDLED_ACTIONS,
    BUILDER_INTERNAL_TOOL_NAMES,
    get_builder_internal_tool_definitions,
)
from tracecat.agent.schemas import ToolFilters
from tracecat.agent.stream.connector import AgentStream
from tracecat.agent.tokens import InternalToolContext, UserMCPServerClaim
from tracecat.agent.tools import build_agent_tools
from tracecat.auth.types import Role
from tracecat.common import all_activities
from tracecat.contexts import ctx_role
from tracecat.exceptions import BuiltinRegistryHasNoSelectionError
from tracecat.logger import logger
from tracecat.registry.lock.service import RegistryLockService
from tracecat.registry.lock.types import RegistryLock
from tracecat.tiers.entitlements import Entitlement, EntitlementService
from tracecat.tiers.service import TierService


class BuildToolDefsArgs(BaseModel):
    role: Role
    tool_filters: ToolFilters
    tool_approvals: dict[str, bool] | None = None
    mcp_servers: list[MCPServerConfig] | None = None
    """User-defined MCP server configurations to discover tools from."""
    internal_tool_context: InternalToolContext | None = None
    """Context for internal tools (e.g., preset_id for builder assistant)."""


class BuildToolDefsResult(BaseModel):
    tool_definitions: dict[str, MCPToolDefinition]
    registry_lock: RegistryLock
    user_mcp_claims: list[UserMCPServerClaim] | None = None
    """Resolved user MCP server configs for JWT claims."""
    allowed_internal_tools: list[str] | None = None
    """List of allowed internal tool names for JWT claims."""


class ToolApprovalPayload(BaseModel):
    tool_call_id: str
    tool_name: str
    args: dict[str, Any] | str | None = None


class PersistApprovalsActivityInputs(BaseModel):
    role: Role
    session_id: uuid.UUID
    approvals: list[ToolApprovalPayload]


class ApprovalDecisionPayload(BaseModel):
    tool_call_id: str
    approved: bool
    reason: str | None = None
    decision: bool | dict[str, Any] | None = None
    decision_metadata: dict[str, Any] | None = None
    approved_by: UUID4 | None = None


class ApplyApprovalResultsActivityInputs(BaseModel):
    role: Role
    session_id: uuid.UUID
    decisions: list[ApprovalDecisionPayload]


class EmitSessionErrorInputs(BaseModel):
    session_id: uuid.UUID
    workspace_id: uuid.UUID
    message: str


class AgentActivities:
    """Activities for agent execution."""

    def get_activities(self) -> list[Callable[..., Any]]:
        return all_activities(self)

    @activity.defn
    async def build_tool_definitions(
        self,
        args: BuildToolDefsArgs,
    ) -> BuildToolDefsResult:
        # Set role context for services that require organization context
        ctx_role.set(args.role)

        # Runtime guard for approval-gated agent flows. This ensures direct
        # workflow execution paths still enforce entitlements.
        if args.tool_approvals:
            if args.role.organization_id is None:
                raise ValueError(
                    "Role must have organization_id to validate entitlements"
                )
            async with TierService.with_session() as tier_service:
                entitlement_service = EntitlementService(tier_service)
                await entitlement_service.check_entitlement(
                    args.role.organization_id, Entitlement.AGENT_ADDONS
                )

        # Check if this is a builder assistant session
        is_builder = (
            args.internal_tool_context is not None
            and args.internal_tool_context.entity_type == "agent_preset_builder"
        )

        # For builder sessions, add bundled actions to the tool filters
        actions_to_build = list(args.tool_filters.actions or [])
        if is_builder:
            # Add bundled registry actions for builder (core.table.*, tools.exa.*)
            for action in BUILDER_BUNDLED_ACTIONS:
                if action not in actions_to_build:
                    actions_to_build.append(action)

        try:
            result = await build_agent_tools(
                namespaces=args.tool_filters.namespaces,
                actions=actions_to_build if actions_to_build else None,
                tool_approvals=args.tool_approvals,
            )
        except ValueError as e:
            raise ApplicationError(
                str(e),
                type="AgentToolDefinitionError",
                non_retryable=True,
            ) from e
        # Convert to dict[str, MCPToolDefinition] keyed by canonical action name
        # Tools already have canonical names (with dots, e.g., "core.cases.list_cases")
        defs: dict[str, MCPToolDefinition] = {}
        for tool in result.tools:
            defs[tool.name] = MCPToolDefinition(
                name=tool.name,
                description=tool.description,
                parameters_json_schema=tool.parameters_json_schema,
            )

        # Add internal tools for builder assistant
        allowed_internal_tools: list[str] | None = None
        if is_builder:
            # Add builder internal tools to definitions
            internal_defs = get_builder_internal_tool_definitions()
            defs.update(internal_defs)
            allowed_internal_tools = list(BUILDER_INTERNAL_TOOL_NAMES)
            logger.info(
                "Added builder internal tools",
                tool_count=len(internal_defs),
                tools=list(internal_defs.keys()),
            )

        # Discover user MCP tools if configured
        user_mcp_claims: list[UserMCPServerClaim] | None = None
        if args.mcp_servers:
            from tracecat.agent.mcp.user_client import discover_user_mcp_tools
            from tracecat.agent.preset.service import AgentPresetService

            http_servers = [cfg for cfg in args.mcp_servers if is_http_mcp_server(cfg)]
            if not http_servers:
                logger.info("No HTTP MCP servers configured for discovery")
                http_servers = []

            # Hydrate headers from the DB for the duration of this activity.
            # Configs that arrive here carry ``id`` but no ``headers`` (the
            # boundary-safe shape produced by ``resolve_mcp_integration_refs``).
            # Fetch secrets per server, attach for ``tools/list``, and drop
            # them before returning so they never enter ``BuildToolDefsResult``
            # or leak across the Temporal boundary.
            hydrated_servers: list[MCPHttpServerConfig] = []
            async with AgentPresetService.with_session(role=args.role) as svc:
                for cfg in http_servers:
                    hydrated: MCPHttpServerConfig = {**cfg}
                    integration_id_str = cfg.get("id")
                    if integration_id_str:
                        try:
                            secrets = await svc.resolve_mcp_integration_secrets(
                                uuid.UUID(integration_id_str)
                            )
                        except ValueError:
                            secrets = None
                        if secrets:
                            hydrated["headers"] = secrets
                    hydrated_servers.append(hydrated)

            try:
                user_mcp_tools = await discover_user_mcp_tools(hydrated_servers)
                # Add user MCP tools to definitions
                for tool_name, tool_def in user_mcp_tools.items():
                    defs[tool_name] = tool_def

                # JWT claims carry only refs — name + source integration id.
                # Trusted MCP server re-resolves headers per call.
                user_mcp_claims = [
                    UserMCPServerClaim(
                        name=cfg["name"],
                        id=uuid.UUID(integration_id_str),
                    )
                    for cfg in http_servers
                    if (integration_id_str := cfg.get("id"))
                ]

                logger.info(
                    "Discovered user MCP tools",
                    tool_count=len(user_mcp_tools),
                    server_count=len(hydrated_servers),
                )
            except Exception as e:
                logger.error(
                    "Failed to discover user MCP tools",
                    error_type=type(e).__name__,
                    server_count=len(hydrated_servers),
                )
                # Continue without user MCP tools - don't fail the whole operation
            finally:
                # Defensive: ensure hydrated configs (with headers) drop out
                # of scope before this activity returns. Local variable; this
                # is documentation more than enforcement.
                hydrated_servers = []

        # Resolve registry lock for these actions
        # This provides origin→version mappings needed for action execution
        # Note: User MCP tools and internal tools don't need registry lock resolution
        registry_action_names = {
            name
            for name in defs.keys()
            if not name.startswith("mcp__") and not name.startswith("internal.")
        }
        try:
            async with RegistryLockService.with_session() as lock_service:
                registry_lock = await lock_service.resolve_lock_with_bindings(
                    registry_action_names
                )
        except BuiltinRegistryHasNoSelectionError as e:
            raise ApplicationError(
                str(e),
                e.detail,
                type=e.__class__.__name__,
            ) from e

        return BuildToolDefsResult(
            tool_definitions=defs,
            registry_lock=registry_lock,
            user_mcp_claims=user_mcp_claims,
            allowed_internal_tools=allowed_internal_tools,
        )

    @activity.defn
    async def emit_session_error(self, args: EmitSessionErrorInputs) -> None:
        """Push a terminal error onto the session's SSE stream.

        Used by workflow outer-scope handlers to surface pre-runtime failures
        to the chat UI, since those happen before the loopback is wired up.
        """
        stream = await AgentStream.new(
            session_id=args.session_id, workspace_id=args.workspace_id
        )
        await stream.error(args.message)
        await stream.done()
