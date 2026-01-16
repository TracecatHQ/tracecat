from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any

from pydantic import UUID4, BaseModel
from temporalio import activity

from tracecat.agent.common.types import MCPServerConfig
from tracecat.agent.mcp.types import MCPToolDefinition
from tracecat.agent.schemas import ToolFilters
from tracecat.agent.tokens import UserMCPServerClaim
from tracecat.agent.tools import build_agent_tools
from tracecat.auth.types import Role
from tracecat.common import all_activities
from tracecat.logger import logger
from tracecat.registry.lock.service import RegistryLockService
from tracecat.registry.lock.types import RegistryLock


class BuildToolDefsArgs(BaseModel):
    tool_filters: ToolFilters
    tool_approvals: dict[str, bool] | None = None
    mcp_servers: list[MCPServerConfig] | None = None
    """User-defined MCP server configurations to discover tools from."""


class BuildToolDefsResult(BaseModel):
    tool_definitions: dict[str, MCPToolDefinition]
    registry_lock: RegistryLock
    user_mcp_claims: list[UserMCPServerClaim] | None = None
    """Resolved user MCP server configs for JWT claims."""


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
        result = await build_agent_tools(
            namespaces=args.tool_filters.namespaces,
            actions=args.tool_filters.actions,
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

        # Discover user MCP tools if configured
        user_mcp_claims: list[UserMCPServerClaim] | None = None
        if args.mcp_servers:
            from tracecat.agent.mcp.user_client import discover_user_mcp_tools

            try:
                user_mcp_tools = await discover_user_mcp_tools(args.mcp_servers)
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
            except Exception as e:
                logger.error(
                    "Failed to discover user MCP tools",
                    error=str(e),
                )
                # Continue without user MCP tools - don't fail the whole operation

        # Resolve registry lock for these actions
        # This provides origin→version mappings needed for action execution
        # Note: User MCP tools don't need registry lock resolution
        registry_action_names = {
            name for name in defs.keys() if not name.startswith("mcp__")
        }
        async with RegistryLockService.with_session() as lock_service:
            registry_lock = await lock_service.resolve_lock_with_bindings(
                registry_action_names
            )

        return BuildToolDefsResult(
            tool_definitions=defs,
            registry_lock=registry_lock,
            user_mcp_claims=user_mcp_claims,
        )
