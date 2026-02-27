from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any

from pydantic import UUID4, BaseModel
from temporalio import activity

from tracecat.agent.common.types import MCPServerConfig, MCPToolDefinition
from tracecat.agent.mcp.internal_tools import (
    BUILDER_BUNDLED_ACTIONS,
    BUILDER_INTERNAL_TOOL_NAMES,
    get_builder_internal_tool_definitions,
)
from tracecat.agent.mcp.user_client import UserMCPClient, discover_user_mcp_tools
from tracecat.agent.mcp.utils import mcp_tool_name_to_canonical
from tracecat.agent.schemas import ToolFilters
from tracecat.agent.tokens import InternalToolContext, UserMCPServerClaim
from tracecat.agent.tools import build_agent_tools
from tracecat.auth.types import Role
from tracecat.common import all_activities
from tracecat.contexts import ctx_role
from tracecat.exceptions import TracecatValidationError
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
    approved_by: UUID4 | None = None


class ApplyApprovalResultsActivityInputs(BaseModel):
    role: Role
    session_id: uuid.UUID
    decisions: list[ApprovalDecisionPayload]


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
        actions_to_build = [
            s for action in (args.tool_filters.actions or []) if (s := action.strip())
        ]
        selected_mcp_action_names = {
            mcp_tool_name_to_canonical(action)
            for action in actions_to_build
            if UserMCPClient.parse_user_mcp_tool_name(action)
        }
        registry_actions = [
            action
            for action in actions_to_build
            if not UserMCPClient.parse_user_mcp_tool_name(action)
        ]
        if is_builder:
            # Add bundled registry actions for builder (core.table.*, tools.exa.*)
            for action in BUILDER_BUNDLED_ACTIONS:
                if action not in registry_actions:
                    registry_actions.append(action)

        result = await build_agent_tools(
            namespaces=args.tool_filters.namespaces,
            actions=registry_actions if registry_actions else None,
            tool_approvals=args.tool_approvals,
        )
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
        if selected_mcp_action_names and not args.mcp_servers:
            raise TracecatValidationError(
                "MCP tools were selected, but no MCP servers are configured: "
                f"{sorted(selected_mcp_action_names)}"
            )
        if args.mcp_servers:
            try:
                user_mcp_tools = await discover_user_mcp_tools(args.mcp_servers)

                # If specific MCP tools were selected, only include those.
                # Otherwise include all discovered MCP tools for backward compatibility.
                if selected_mcp_action_names:
                    canonical_mcp_names = {
                        mcp_tool_name_to_canonical(tool_name)
                        for tool_name in user_mcp_tools
                    }
                    missing_mcp_tools = selected_mcp_action_names - canonical_mcp_names
                    if missing_mcp_tools:
                        raise TracecatValidationError(
                            "Some MCP tools were not found in configured integrations: "
                            f"{sorted(missing_mcp_tools)}"
                        )
                    user_mcp_tools = {
                        tool_name: tool_def
                        for tool_name, tool_def in user_mcp_tools.items()
                        if mcp_tool_name_to_canonical(tool_name)
                        in selected_mcp_action_names
                    }

                # Add user MCP tools to definitions
                for tool_name, tool_def in user_mcp_tools.items():
                    defs[tool_name] = tool_def

                # Build claims for JWT (headers NOT resolved here - done by caller)
                user_mcp_claims = [
                    UserMCPServerClaim(
                        name=cfg["name"],
                        url=cfg["url"],
                        transport=cfg.get("transport", "http"),
                        headers=cfg.get("headers", {}),
                    )
                    for cfg in args.mcp_servers
                ]

                logger.info(
                    "Discovered user MCP tools",
                    tool_count=len(user_mcp_tools),
                    server_count=len(args.mcp_servers),
                )
            except TracecatValidationError:
                raise
            except Exception as e:
                logger.error(
                    "Failed to discover user MCP tools",
                    error=str(e),
                )
                # Continue without user MCP tools - don't fail the whole operation

        # Resolve registry lock for these actions
        # This provides origin→version mappings needed for action execution
        # Note: User MCP tools and internal tools don't need registry lock resolution
        registry_action_names = {
            name
            for name in defs.keys()
            if not name.startswith("mcp__") and not name.startswith("internal.")
        }
        async with RegistryLockService.with_session() as lock_service:
            registry_lock = await lock_service.resolve_lock_with_bindings(
                registry_action_names
            )

        return BuildToolDefsResult(
            tool_definitions=defs,
            registry_lock=registry_lock,
            user_mcp_claims=user_mcp_claims,
            allowed_internal_tools=allowed_internal_tools,
        )
