from collections.abc import Awaitable, Callable
from typing import Any

import orjson
from pydantic import BaseModel
from pydantic_ai.direct import model_request as direct_model_request
from pydantic_ai.messages import (
    ModelMessage,
    ModelMessagesTypeAdapter,
)
from pydantic_ai.models import ModelRequestParameters
from pydantic_ai.tools import ToolDefinition
from pydantic_core import to_json, to_jsonable_python
from temporalio import activity
from tracecat_registry.integrations.agents.builder import (
    ModelMessageTA,
    build_agent_tool_definitions,
    create_tool_from_registry,
)
from tracecat_registry.integrations.pydantic_ai import get_model

from tracecat.contexts import ctx_role
from tracecat.ee.agent.models import (
    DurableModelRequestArgs,
    ExecuteToolCallArgs,
    ExecuteToolCallResult,
    ModelRequestArgs,
    ModelRequestResult,
    ToolFilters,
)
from tracecat.ee.agent.service import AgentManagementService
from tracecat.logger import logger
from tracecat.redis.client import RedisClient
from tracecat.registry.actions.service import RegistryActionsService
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
async def execute_tool_call(
    args: ExecuteToolCallArgs, role: Role
) -> ExecuteToolCallResult:
    """Execute a single tool call and return the result as a ToolReturnPart."""
    ctx_role.set(role)
    action_name = args.tool_name.replace("__", ".")
    logger.info("Executing tool call", args=args)
    async with RegistryActionsService.with_session() as svc:
        tool = await create_tool_from_registry(action_name, args.tool_args, service=svc)
    result = await tool.function(**args.tool_args)  # type: ignore
    return ExecuteToolCallResult(tool_return=to_json(result))


class BuildToolDefinitionsArgs(BaseModel):
    tool_filters: ToolFilters


class BuildToolDefinitionsResult(BaseModel):
    tool_definitions: list[ToolDefinition]


@activity.defn
async def build_tool_definitions(
    args: BuildToolDefinitionsArgs,
) -> BuildToolDefinitionsResult:
    result = await build_agent_tool_definitions(
        namespace_filters=args.tool_filters.namespaces,
        action_filters=args.tool_filters.actions,
    )
    return BuildToolDefinitionsResult(tool_definitions=result.tool_definitions)


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
    request_params = model.customize_request_parameters(args.model_request_parameters)

    tool_filters = args.tool_filters or ToolFilters.default()
    result = await build_agent_tool_definitions(
        namespace_filters=tool_filters.namespaces,
        action_filters=tool_filters.actions,
    )
    request_params.function_tools += result.tool_definitions
    logger.info(
        f"Request params: {to_json(request_params, indent=2).decode()}",
    )
    logger.info(f"Model: {model.model_name}")
    logger.info(f"Model settings: {to_json(args.model_settings, indent=2).decode()}")
    logger.info(f"Tool filters: {to_json(tool_filters, indent=2).decode()}")
    model_response = await model.request(
        args.messages, args.model_settings, request_params
    )
    return ModelRequestResult(model_response=to_json(model_response))


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


def agent_activities() -> list[Callable[..., Awaitable[Any]]]:
    return [
        model_request,
        execute_tool_call,
        durable_model_request,
        build_tool_definitions,
    ]
