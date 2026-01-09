from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any, Literal

from pydantic import UUID4, BaseModel, Field
from temporalio import activity

from tracecat.agent.mcp.types import MCPToolDefinition
from tracecat.agent.schemas import ToolFilters
from tracecat.agent.tools import (
    ToolExecutionError,
    ToolExecutor,
    build_agent_tools,
    denormalize_tool_name,
)
from tracecat.auth.types import Role
from tracecat.common import all_activities
from tracecat.contexts import ctx_role
from tracecat.logger import logger
from tracecat_ee.agent.context import AgentContext


class InvokeToolArgs(BaseModel):
    tool_name: str = Field(..., description="Name of the tool to execute")
    tool_args: dict[str, Any] = Field(..., description="Arguments for the tool")
    tool_call_id: str = Field(..., description="ID of the tool call")


class InvokeToolResult(BaseModel):
    type: Literal["result", "error", "retry"] = Field(..., description="Type of result")
    result: Any = Field(default=None, description="Tool return part")
    error: str | None = Field(
        default=None, description="Error message if execution failed"
    )
    retry_message: str | None = Field(
        default=None, description="Retry message if ModelRetry was raised"
    )


class BuildToolDefsArgs(BaseModel):
    tool_filters: ToolFilters
    tool_approvals: dict[str, bool] | None = None


class BuildToolDefsResult(BaseModel):
    tool_definitions: dict[str, MCPToolDefinition]


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

    def __init__(
        self,
        *,
        tool_executor: ToolExecutor,
    ) -> None:
        self.tool_executor = tool_executor

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
        return BuildToolDefsResult(tool_definitions=defs)

    @activity.defn
    async def invoke_tool(
        self, args: InvokeToolArgs, ctx: AgentContext, role: Role
    ) -> InvokeToolResult:
        """Execute a single tool call and return the result."""
        ctx_role.set(role)
        AgentContext.set_from(ctx)
        tool_name = denormalize_tool_name(args.tool_name)
        logger.debug("Invoke tool activity", args=args, role=role)

        try:
            result = await self.tool_executor.run(tool_name, args.tool_args)
            return InvokeToolResult(type="result", result=result)
        except ToolExecutionError as e:
            # Execution failed - return as retry so agent can adjust
            logger.info("Tool execution failed", tool_name=tool_name, error=str(e))
            return InvokeToolResult(type="retry", result=None, retry_message=str(e))
        except Exception as e:
            logger.error("Unexpected tool call failure", error=e, type=type(e))
            return InvokeToolResult(type="error", result=None, error=str(e))
