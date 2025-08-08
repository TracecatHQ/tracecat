from __future__ import annotations as _annotations

from collections.abc import Awaitable, Callable
from datetime import timedelta
from typing import Any

from pydantic_ai import RunContext, Tool
from pydantic_ai.exceptions import ModelRetry
from pydantic_ai.messages import (
    ModelMessage,
    ModelResponse,
    RetryPromptPart,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai.models import Model, ModelRequestParameters
from pydantic_ai.settings import ModelSettings
from pydantic_ai.tools import (
    AgentDepsT,
    ToolDefinition,
)
from pydantic_core import to_json
from temporalio import workflow

from tracecat.contexts import ctx_role
from tracecat.ee.agent.activities import AgentActivities
from tracecat.ee.agent.context import AgentContext
from tracecat.ee.agent.models import (
    DurableModelRequestArgs,
    ExecuteToolCallArgs,
    ExecuteToolCallResult,
    ModelInfo,
    ModelResponseTA,
)
from tracecat.logger import logger


class DurableGatedTool[AgentDepsT]:
    """
    Proxy around a real Tool that pauses for human approval
    before letting the underlying tool run.
    """

    def __init__(
        self,
        tool: ToolDefinition,
        gate: Callable[
            [ToolCallPart, RunContext[AgentDepsT]],  # (tool_name, args, context)
            Awaitable[bool],  # approve = True / False
        ],
    ) -> None:
        self._tool = tool
        self._gate = gate

        # Re-export the wrapped tool’s public definition so that the
        # LLM still “sees” the same schema.
        self.tool = Tool[AgentDepsT](
            self._call_gated_tool,
            name=tool.name,
            description=tool.description,
            takes_ctx=True,  # we need ctx for audit trail
            strict=tool.strict,
        )

    async def _call_gated_tool(
        self,
        ctx: RunContext[AgentDepsT],
        **tool_args: Any,
    ) -> ToolReturnPart | RetryPromptPart:
        logger.info("HITLTool", tool_name=self._tool.name, tool_args=tool_args, ctx=ctx)
        part = ToolCallPart(
            tool_name=self._tool.name,
            tool_call_id=ctx.tool_call_id or "",
            args=tool_args,
        )
        approved = await self._gate(part, ctx)

        if not approved:
            # Tell the model to try again or do something else
            return RetryPromptPart(
                tool_name=self._tool.name,
                tool_call_id=ctx.tool_call_id
                or "",  # echo back whatever id pydantic-ai gave
                content="Call rejected by human reviewer. Try another approach.",
            )

        # Human said yes – delegate to the real implementation
        return await self._call_tool(part, tool_args)

    async def _call_tool(
        self, tool_call: ToolCallPart, tool_args: dict[str, Any]
    ) -> Any:
        # This function represents the actual tool call
        # We proxy it to temporal
        args = ExecuteToolCallArgs(
            tool_name=self._tool.name,
            tool_args=tool_args,
            tool_call_id=tool_call.tool_call_id,
        )

        agent_ctx = AgentContext.get()
        role = ctx_role.get()
        result = await workflow.execute_activity_method(
            AgentActivities.execute_tool_call,
            args=(args, agent_ctx, role),
            start_to_close_timeout=timedelta(seconds=60),
        )
        logger.info("Tool call result", result=result)

        # Check if the activity returned a retry message
        match result:
            case ExecuteToolCallResult(type="retry", retry_message=retry_message):
                logger.info("Tool requested retry", message=retry_message)
                raise ModelRetry(retry_message or "Tool requested retry")
            case ExecuteToolCallResult(type="error", error=error):
                logger.error("Tool call failed", error=error)
                raise Exception(error)
            case ExecuteToolCallResult(type="result", result=result):
                return result
            case _:
                raise RuntimeError("We should never get here")


class DurableModel(Model):
    """
    A durable AI model implementation that proxies model requests to Temporal activities.

    This class wraps AI model requests in Temporal activities to provide durability,
    retry capabilities, and workflow integration. All model requests are executed
    as Temporal activities, allowing them to be retried, monitored, and managed
    within the Temporal workflow system.
    """

    def __init__(self, info: ModelInfo):
        self._info = info

    @property
    def model_name(self) -> str:
        return self._info.name

    @property
    def system(self) -> str:
        return self._info.provider

    async def request(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
    ) -> ModelResponse:
        agent_ctx = AgentContext.get()
        args = DurableModelRequestArgs(
            messages=messages,
            model_settings=model_settings,
            model_request_parameters=model_request_parameters,
            model_info=self._info,
        )
        role = ctx_role.get()
        logger.debug(f"DurableModel request: {to_json(args, indent=2).decode()}")
        result = await workflow.execute_activity_method(
            AgentActivities.durable_model_request,
            args=(args, agent_ctx, role),
            start_to_close_timeout=timedelta(seconds=120),
        )
        resp = ModelResponseTA.validate_python(result.model_response)
        logger.debug(f"DurableModel response: {to_json(resp, indent=2).decode()}")
        return resp
