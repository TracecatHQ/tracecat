from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import replace
from typing import Any, Literal

from pydantic import UUID4, BaseModel, ConfigDict, Field
from pydantic_ai import ModelSettings, RunContext, ToolDefinition
from pydantic_ai.durable_exec.temporal import TemporalRunContext
from pydantic_ai.exceptions import ModelRetry
from pydantic_ai.messages import (
    AgentStreamEvent,
    ModelMessage,
    ModelResponse,
)
from pydantic_ai.models import ModelRequestParameters
from temporalio import activity

from tracecat.agent.context import AgentContext
from tracecat.agent.models import (
    ModelInfo,
    ModelRequestArgs,
    ModelRequestResult,
    ToolFilters,
)
from tracecat.agent.providers import get_model
from tracecat.agent.stream.common import (
    PersistableStreamingAgentDeps,
    PersistableStreamingAgentDepsSpec,
)
from tracecat.agent.stream.writers import event_stream_handler
from tracecat.agent.tools import (
    ToolExecutor,
    build_agent_tools,
    denormalize_tool_name,
)
from tracecat.common import all_activities
from tracecat.contexts import ctx_role
from tracecat.dsl.enums import PlatformAction
from tracecat.logger import logger
from tracecat.secrets import secrets_manager
from tracecat.types.auth import Role


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


class BuildToolDefsResult(BaseModel):
    tool_definitions: list[ToolDefinition]


class RequestStreamArgs(BaseModel):
    model_config: ConfigDict = ConfigDict(arbitrary_types_allowed=True)
    role: Role
    messages: list[ModelMessage]
    model_settings: ModelSettings | None
    model_request_parameters: ModelRequestParameters
    serialized_run_context: Any
    model_info: ModelInfo


class EventStreamHandlerArgs(BaseModel):
    model_config: ConfigDict = ConfigDict(arbitrary_types_allowed=True)
    serialized_run_context: Any
    event: AgentStreamEvent


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
    decision: dict[str, Any] | None = None
    approved_by: UUID4 | None = None


class ApplyApprovalResultsActivityInputs(BaseModel):
    role: Role
    session_id: uuid.UUID
    decisions: list[ApprovalDecisionPayload]


class AgentActivities:
    """Activities for agent execution with optional Redis streaming."""

    def __init__(
        self,
        *,
        tool_executor: ToolExecutor,
        run_context_type: type[
            TemporalRunContext[PersistableStreamingAgentDeps]
        ] = TemporalRunContext[PersistableStreamingAgentDeps],
    ) -> None:
        self.tool_executor = tool_executor
        self.run_context_type = run_context_type

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
        )
        defs = [
            replace(t.tool_def, metadata={"approval_required": t.requires_approval})
            for t in result.tools
        ]
        return BuildToolDefsResult(tool_definitions=defs)

    @activity.defn
    async def invoke_tool(
        self, args: InvokeToolArgs, ctx: AgentContext, role: Role
    ) -> InvokeToolResult:
        """Execute a single tool call and return the result as a ToolReturnPart."""
        ctx_role.set(role)
        AgentContext.set_from(ctx)
        tool_name = denormalize_tool_name(args.tool_name)
        logger.info("Executing tool call", args=args)

        try:
            # Use the tool executor to run the tool
            # result = await self.executor.run(tool_name, args.tool_args)
            result = f"<executed tool {tool_name}:{args.tool_args}>"

            return InvokeToolResult(type="result", result=result)
        except ModelRetry as e:
            # Don't let ModelRetry fail the activity - return it as a special result
            # that the workflow can handle
            logger.info("Tool raised ModelRetry", tool_name=tool_name, error=str(e))
            return InvokeToolResult(type="retry", result=None, retry_message=str(e))
        except Exception as e:
            logger.error("Unexpected tool call failure", error=e, type=type(e))
            return InvokeToolResult(type="error", result=None, error=str(e))

    @activity.defn
    async def model_request(
        self, args: ModelRequestArgs, ctx: AgentContext
    ) -> ModelRequestResult:
        """Execute a durable model request with optional Redis streaming."""
        logger.info("Model request", args=args, ctx=ctx, role=args.role)
        ctx_role.set(args.role)
        AgentContext.set_from(ctx)

        async with secrets_manager.load_secrets(PlatformAction.AI_HITL_AGENT):
            model = get_model(
                args.model_info.name, args.model_info.provider, args.model_info.base_url
            )
        request_params = model.customize_request_parameters(
            args.model_request_parameters
        )

        logger.info(
            "Request params, model, settings, filters prepared",
            request_params=request_params,
        )
        model_response = await model.request(
            args.messages, args.model_settings, request_params
        )

        return ModelRequestResult(model_response=model_response)

    @activity.defn
    async def request_stream(
        self,
        args: RequestStreamArgs,
        deps: PersistableStreamingAgentDepsSpec,
    ) -> ModelResponse:
        logger.warning(
            "REQUEST STREAM ACTIVITY", params=args, deps=deps, role=args.role
        )
        ctx_role.set(args.role)
        run_context = await self._reconstruct_run_context(
            args.serialized_run_context, spec=deps
        )
        async with secrets_manager.load_secrets(PlatformAction.AI_HITL_AGENT):
            model = get_model(
                args.model_info.name,
                args.model_info.provider,
                args.model_info.base_url,
            )
        async with model.request_stream(
            args.messages,
            args.model_settings,
            args.model_request_parameters,
            run_context,
        ) as streamed_response:
            await event_stream_handler(run_context, streamed_response)

            async for _ in streamed_response:
                pass
        return streamed_response.get()

    @activity.defn
    async def event_stream_handler(
        self,
        args: EventStreamHandlerArgs,
        deps: PersistableStreamingAgentDepsSpec,
    ) -> None:
        run_context = await self._reconstruct_run_context(
            args.serialized_run_context, spec=deps
        )
        logger.warning(
            "ACTIVITY: EVENT STREAM HANDLER",
            ctx=run_context,
            event=args.event,
        )

        async def streamed_response():
            yield args.event

        await event_stream_handler(run_context, streamed_response())

    async def _reconstruct_run_context(
        self,
        serialized_run_context: Any,
        spec: PersistableStreamingAgentDepsSpec,
    ) -> RunContext[PersistableStreamingAgentDeps]:
        deps = await spec.build()
        return self.run_context_type.deserialize_run_context(
            serialized_run_context, deps=deps
        )
