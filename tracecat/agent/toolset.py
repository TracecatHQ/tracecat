from datetime import timedelta
from typing import Any

from pydantic_ai import ApprovalRequired, RunContext, ToolDefinition, UserError
from pydantic_ai.tools import AgentDepsT
from pydantic_ai.toolsets import AbstractToolset, ToolsetTool
from pydantic_ai.toolsets.external import TOOL_SCHEMA_VALIDATOR
from temporalio import workflow

from tracecat.agent.activities import AgentActivities, InvokeToolArgs
from tracecat.agent.context import AgentContext
from tracecat.contexts import ctx_role
from tracecat.logger import logger


class RemoteToolset(AbstractToolset[AgentDepsT]):
    """A toolset that offloads tool execution to a remote service.

    See [toolset docs](../toolsets.md#external-toolset) for more information.
    """

    tool_defs: list[ToolDefinition]
    _id: str | None

    def __init__(self, tool_defs: list[ToolDefinition], *, id: str | None = None):
        self.tool_defs = tool_defs
        self._id = id

    def __repr__(self) -> str:
        return f"RemoteToolset(tool_defs=[{', '.join([f'{t.name}: {t.kind}' for t in self.tool_defs])}])"

    @property
    def id(self) -> str | None:
        return self._id

    async def get_tools(
        self, ctx: RunContext[AgentDepsT]
    ) -> dict[str, ToolsetTool[AgentDepsT]]:
        return {
            tool_def.name: ToolsetTool(
                toolset=self,
                tool_def=tool_def,
                max_retries=0,
                args_validator=TOOL_SCHEMA_VALIDATOR,
            )
            for tool_def in self.tool_defs
        }

    async def call_tool(
        self,
        name: str,
        tool_args: dict[str, Any],
        ctx: RunContext[AgentDepsT],
        tool: ToolsetTool[AgentDepsT],
    ) -> Any:
        if not workflow.in_workflow():
            raise UserError("Remote tool execution must be called inside a workflow")
        approval_required = bool(
            (meta := tool.tool_def.metadata) and meta.get("approval_required")
        )
        logger.info(
            "Calling remote tool",
            name=name,
            tool_args=tool_args,
            tool_call_id=tool.tool_def.name,
            approval_required=approval_required,
        )
        if approval_required and not ctx.tool_call_approved:
            raise ApprovalRequired

        result = await workflow.execute_activity_method(
            AgentActivities.invoke_tool,
            args=(
                InvokeToolArgs(
                    tool_name=name,
                    tool_args=tool_args,
                    tool_call_id=tool.tool_def.name,
                ),
                AgentContext.get(),
                ctx_role.get(),
            ),
            start_to_close_timeout=timedelta(seconds=60),
        )
        return result
