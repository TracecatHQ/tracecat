from __future__ import annotations as _annotations

from collections.abc import Awaitable, Callable
from datetime import timedelta
from typing import Any, Generic

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
from temporalio.exceptions import ActivityError, ApplicationError

from tracecat.contexts import ctx_role
from tracecat.ee.agent.activities import (
    durable_model_request,
    execute_tool_call,
)
from tracecat.ee.agent.models import (
    DurableModelRequestArgs,
    ExecuteToolCallArgs,
    ModelInfo,
    ModelResponseTA,
)
from tracecat.logger import logger


class DurableGatedTool(Generic[AgentDepsT]):
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
        approved = await self._gate(
            ToolCallPart(
                tool_name=self._tool.name,
                tool_call_id=ctx.tool_call_id or "",
                args=tool_args,
            ),
            ctx,
        )

        if not approved:
            # Tell the model to try again or do something else
            return RetryPromptPart(
                tool_name=self._tool.name,
                tool_call_id=ctx.tool_call_id
                or "",  # echo back whatever id pydantic-ai gave
                content="Call rejected by human reviewer. Try another approach.",
            )

        # Human said yes – delegate to the real implementation
        return await self._call_tool(ctx, tool_args)

    async def _call_tool(
        self, ctx: RunContext[AgentDepsT], tool_args: dict[str, Any]
    ) -> Any:
        # This function represents the actual tool call
        # We proxy it to temporal
        args = ExecuteToolCallArgs(
            tool_name=self._tool.name,
            tool_args=tool_args,
            tool_call_id=ctx.tool_call_id or "",
        )
        role = ctx_role.get()
        try:
            result = await workflow.execute_activity(
                execute_tool_call,
                args=(args, role),
                start_to_close_timeout=timedelta(seconds=60),
            )
            logger.info("Tool call result", result=result)
            return result.tool_return
        except ActivityError as e:
            # Temporal wraps failures from the activity in ActivityError.
            # If the real failure was a ModelRetry we resurrect it so that
            # pydantic-ai turns it into a RetryPromptPart (recoverable).
            match cause := e.cause:
                case ModelRetry():
                    # Native deserialisation case
                    raise cause from e
                case ApplicationError() if cause.type == "ModelRetry":
                    # Fallback when the SDK can't deserialize the original class
                    raise ModelRetry(cause.message) from e
                case _:
                    # Any other failure is unrecoverable – let it propagate
                    raise


class DurableModel(Model):
    """A durable AI model implementation that proxies model requests to temporal activities.

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
        role = ctx_role.get()
        args = DurableModelRequestArgs(
            messages=messages,
            model_settings=model_settings,
            model_request_parameters=model_request_parameters,
            model_info=self._info,
        )
        logger.info(f"DurableModel request: {to_json(args, indent=2).decode()}")
        result = await workflow.execute_activity(
            durable_model_request,
            args=(args, role),
            start_to_close_timeout=timedelta(seconds=120),
        )
        resp = ModelResponseTA.validate_json(result.model_response)
        logger.info(f"DurableModel response: {to_json(resp, indent=2).decode()}")
        return resp
