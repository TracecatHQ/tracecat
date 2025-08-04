from collections.abc import Awaitable, Callable
from typing import Any

import orjson
from pydantic import BaseModel
from pydantic_ai import ModelRetry
from pydantic_ai.direct import model_request as direct_model_request
from pydantic_ai.messages import (
    ModelMessage,
    ModelMessagesTypeAdapter,
    RetryPromptPart,
    ToolReturnPart,
)
from pydantic_ai.models import ModelRequestParameters
from pydantic_core import to_json, to_jsonable_python
from temporalio import activity
from tracecat_registry.integrations.agents.builder import (
    ModelMessageTA,
    build_agent_tool_definitions,
    call_tracecat_action,
)
from tracecat_registry.integrations.pydantic_ai import get_model

from tracecat.ee.agent.models import (
    DurableModelRequestArgs,
    ExecuteToolCallArgs,
    ExecuteToolCallResult,
    ModelRequestArgs,
    ModelRequestResult,
)
from tracecat.ee.agent.service import AgentManagementService
from tracecat.logger import logger
from tracecat.redis.client import RedisClient
from tracecat.types.auth import Role


@activity.defn
async def model_request(request_args: ModelRequestArgs) -> ModelRequestResult:
    logger.info(f"Requesting model with {len(request_args.message_history)} messages")
    raw_messages = orjson.loads(request_args.message_history)
    messages = ModelMessagesTypeAdapter.validate_python(raw_messages)
    result = await build_agent_tool_definitions(
        namespace_filters=request_args.tool_filters.namespaces,
        action_filters=request_args.tool_filters.actions,
    )
    model_response = await direct_model_request(
        "openai:gpt-4o-mini",
        messages,
        model_request_parameters=ModelRequestParameters(
            function_tools=result.tool_definitions,
        ),
    )
    return ModelRequestResult(model_response=to_json(model_response))


@activity.defn
async def execute_tool_call(args: ExecuteToolCallArgs) -> ExecuteToolCallResult:
    """Execute a single tool call and return the result as a ToolReturnPart."""
    logger.info("Executing tool call", args=args)

    try:
        # Execute the tool using the existing call_tracecat_action function
        result = await call_tracecat_action(args.tool_name, args.tool_args)

        # Create a ToolReturnPart with the result
        tool_return_part = ToolReturnPart(
            tool_name=args.tool_name,
            tool_call_id=args.tool_call_id,
            content=str(result),  # Convert result to string for LLM consumption
        )

        logger.info(f"Tool call {args.tool_name} completed successfully")
        return ExecuteToolCallResult(tool_return=to_json(tool_return_part))
    except ModelRetry as e:
        logger.error("Model retry", error=e)
        error_msg = f"Tool execution failed, retrying: {str(e)}"
        return ExecuteToolCallResult(
            error=error_msg,
            tool_return=to_json(
                RetryPromptPart(
                    tool_name=args.tool_name,
                    tool_call_id=args.tool_call_id,
                    content=error_msg,
                )
            ),
        )
    except Exception as e:
        error_msg = f"Tool execution failed: {str(e)}"
        logger.error(f"Tool call {args.tool_name} failed: {error_msg}")

        # Create an error ToolReturnPart
        tool_return_part = ToolReturnPart(
            tool_name=args.tool_name,
            tool_call_id=args.tool_call_id,
            content=f"Error: {error_msg}",
        )

        return ExecuteToolCallResult(
            tool_return=to_json(tool_return_part), error=error_msg
        )


@activity.defn
async def durable_model_request(
    args: DurableModelRequestArgs, role: Role
) -> ModelRequestResult:
    logger.info(f"DurableModel request with {len(args.messages)} messages")

    async with (
        AgentManagementService.with_session(role=role) as svc,
        svc.with_model_config(),
    ):
        model = get_model(
            args.model_info.name, args.model_info.provider, args.model_info.base_url
        )
        request_params = model.customize_request_parameters(
            args.model_request_parameters
        )
    model_response = await model.request(
        args.messages, args.model_settings, request_params
    )
    return ModelRequestResult(model_response=to_json(model_response))


def agent_activities() -> list[Callable[..., Awaitable[Any]]]:
    return [model_request, execute_tool_call, durable_model_request]


class WriteChatMessageArgs(BaseModel):
    stream_key: str
    message: ModelMessage


class ReadChatMessagesArgs(BaseModel):
    stream_key: str


class ReadChatMessagesResult(BaseModel):
    conversation_history: list[ModelMessage]


class RedisClientActivities:
    def __init__(self, client: RedisClient):
        self.client = client

    def all_activities(self) -> list[Callable[..., Awaitable[Any]]]:
        return [
            self.write_chat_message,
            self.read_chat_messages,
        ]

    @activity.defn
    async def write_chat_message(self, args: WriteChatMessageArgs) -> None:
        """Stream a chat message to Redis using the configured client."""
        try:
            await self.client.xadd(
                args.stream_key,
                {"d": orjson.dumps(args.message, default=to_jsonable_python).decode()},
                maxlen=10000,
                approximate=True,
            )
        except Exception as e:
            logger.warning("Failed to stream message to Redis", error=str(e))

    @activity.defn
    async def read_chat_messages(
        self, args: ReadChatMessagesArgs
    ) -> ReadChatMessagesResult:
        """Read chat messages from Redis using the configured client."""
        conversation_history: list[ModelMessage] = []

        try:
            messages = await self.client.xrange(args.stream_key, min_id="-", max_id="+")

            for _, fields in messages:
                try:
                    data = orjson.loads(fields["d"])
                    if data.get("__end__") == 1:
                        # This is an end-of-stream marker, skip
                        continue

                    validated_msg = ModelMessageTA.validate_python(data)
                    conversation_history.append(validated_msg)
                except Exception as e:
                    logger.warning("Failed to load message", error=str(e))

        except Exception as e:
            logger.warning(
                "Failed to read messages from Redis",
                error=str(e),
            )

        return ReadChatMessagesResult(conversation_history=conversation_history)
