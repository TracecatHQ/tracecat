from collections.abc import Callable
from typing import Any

import orjson
from pydantic import BaseModel
from pydantic_ai.exceptions import ModelRetry
from pydantic_ai.messages import ModelMessage, ModelRequest, ModelResponse, TextPart
from pydantic_ai.tools import ToolDefinition
from pydantic_core import to_json, to_jsonable_python
from temporalio import activity
from tracecat_registry.integrations.agents.builder import (
    ModelMessageTA,
    build_agent_tool_definitions,
    create_tool_from_registry,
)
from tracecat_registry.integrations.agents.tools import create_tool_return
from tracecat_registry.integrations.pydantic_ai import get_model

from tracecat.contexts import ctx_role
from tracecat.ee.agent.context import AgentContext
from tracecat.ee.agent.models import (
    DurableModelRequestArgs,
    ExecuteToolCallArgs,
    ExecuteToolCallResult,
    ModelRequestResult,
    ToolFilters,
)
from tracecat.ee.agent.service import AgentManagementService
from tracecat.ee.agent.stream import DATA_KEY, END_TOKEN, END_TOKEN_VALUE
from tracecat.logger import logger
from tracecat.redis.client import RedisClient, get_redis_client
from tracecat.registry.actions.service import RegistryActionsService
from tracecat.types.auth import Role


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


class WriteChatMessageArgs(BaseModel):
    stream_key: str
    message: ModelMessage


class ReadChatMessagesArgs(BaseModel):
    stream_key: str


class ReadChatMessagesResult(BaseModel):
    conversation_history: list[ModelMessage]


class WriteUserPromptArgs(BaseModel):
    user_prompt: ModelRequest


class AgentActivities:
    """Activities for agent execution with optional Redis streaming."""

    def __init__(self, client: RedisClient | None = None):
        self.client = client

    async def _get_client(self) -> RedisClient:
        """Get Redis client, initializing if needed."""
        if self.client is None:
            self.client = await get_redis_client()
        return self.client

    async def _write_message(self, stream_key: str, message: Any) -> None:
        """Internal helper to write a message to Redis stream."""
        try:
            client = await self._get_client()
            await client.xadd(
                stream_key,
                {DATA_KEY: orjson.dumps(message, default=to_jsonable_python).decode()},
                maxlen=10000,
                approximate=True,
            )
        except Exception as e:
            logger.warning("Failed to stream message to Redis", error=str(e))

    async def _write_end_token(self, stream_key: str) -> None:
        """Internal helper to write an end-of-stream token to Redis."""
        try:
            client = await self._get_client()
            await client.xadd(
                stream_key,
                {DATA_KEY: orjson.dumps({END_TOKEN: END_TOKEN_VALUE}).decode()},
                maxlen=10000,
                approximate=True,
            )
        except Exception as e:
            logger.warning("Failed to write end-of-stream token to Redis", error=str(e))

    async def _read_messages(self, stream_key: str) -> list[ModelMessage]:
        """Internal helper to read messages from Redis stream."""
        conversation_history: list[ModelMessage] = []

        try:
            client = await self._get_client()
            messages = await client.xrange(stream_key, min_id="-", max_id="+")

            for _, fields in messages:
                try:
                    data = orjson.loads(fields[DATA_KEY])
                    if data.get(END_TOKEN) == END_TOKEN_VALUE:
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

        return conversation_history

    async def _stream_message(self, message: ModelMessage) -> None:
        """Internal helper to stream a message to Redis."""

        ctx = AgentContext.get()
        if ctx and ctx.session_id:
            try:
                await self._write_message(ctx.session_id, message)
            except Exception as e:
                logger.warning("Failed to stream message to Redis", error=str(e))

    def _stream_enabled(self) -> bool:
        """Check if streaming is enabled."""

        ctx = AgentContext.get()
        return ctx is not None and ctx.session_id is not None

    @activity.defn
    async def execute_tool_call(
        self, args: ExecuteToolCallArgs, ctx: AgentContext, role: Role
    ) -> ExecuteToolCallResult:
        """Execute a single tool call and return the result as a ToolReturnPart."""
        ctx_role.set(role)
        AgentContext.set_from(ctx)
        action_name = args.tool_name.replace("__", ".")
        logger.info("Executing tool call", args=args)
        try:
            async with RegistryActionsService.with_session() as svc:
                tool = await create_tool_from_registry(
                    action_name, args.tool_args, service=svc
                )
            try:
                result = await tool.function(**args.tool_args)  # type: ignore
            except ModelRetry as e:
                # Don't let ModelRetry fail the activity - return it as a special result
                # that the workflow can handle
                logger.info(
                    "Tool raised ModelRetry", tool_name=action_name, error=str(e)
                )
                return ExecuteToolCallResult(
                    type="retry", result=None, retry_message=str(e)
                )

            # Optional Redis streaming
            if self._stream_enabled():
                message = create_tool_return(
                    tool_name=action_name,
                    content=result,
                    tool_call_id=args.tool_call_id,
                )
                await self._stream_message(message)
            return ExecuteToolCallResult(type="result", result=result)
        except Exception as e:
            logger.error("Unexpected tool call failure", error=e, type=type(e))
            return ExecuteToolCallResult(type="error", result=None, error=str(e))

    @activity.defn
    async def durable_model_request(
        self, args: DurableModelRequestArgs, ctx: AgentContext, role: Role
    ) -> ModelRequestResult:
        """Execute a durable model request with optional Redis streaming."""
        logger.info(f"DurableModel request with {len(args.messages)} messages")
        ctx_role.set(role)
        AgentContext.set_from(ctx)

        # Merge Redis history with new messages
        messages: list[ModelMessage] = []
        if ctx.session_id:
            # If there's external history, add it to the messages as a single message
            # TODO: Add a HWM (high water mark) to the stream to avoid reading too many messages
            history = await self._read_messages(ctx.session_id)
            history_message = ModelResponse(
                parts=[
                    TextPart(
                        content=f"<chat_history>{to_json(history, indent=2).decode()}</chat_history>"
                    )
                ]
            )

            messages.append(history_message)
            logger.info(f"Added {len(history)} history messages to the request")
        messages.extend(args.messages)

        async with (
            AgentManagementService.with_session() as svc,
            svc.with_model_config(),
        ):
            model = get_model(
                args.model_info.name, args.model_info.provider, args.model_info.base_url
            )
        request_params = model.customize_request_parameters(
            args.model_request_parameters
        )

        tool_filters = args.tool_filters or ToolFilters.default()
        result = await build_agent_tool_definitions(
            namespace_filters=tool_filters.namespaces,
            action_filters=tool_filters.actions,
        )
        request_params.function_tools += result.tool_definitions
        logger.info("Request params, model, settings, filters prepared")
        model_response = await model.request(
            messages, args.model_settings, request_params
        )

        # Optional Redis streaming
        if self._stream_enabled():
            await self._stream_message(model_response)

        return ModelRequestResult(model_response=model_response)

    @activity.defn
    async def write_user_prompt(
        self, args: WriteUserPromptArgs, ctx: AgentContext
    ) -> None:
        """Log the user prompt to Redis."""
        logger.info("Logging user prompt", prompt=args.user_prompt)
        AgentContext.set_from(ctx)
        await self._stream_message(args.user_prompt)

    @activity.defn
    async def write_end_token(self, ctx: AgentContext) -> None:
        """Log the model response to Redis."""
        logger.info("Logging model response")
        AgentContext.set_from(ctx)
        if ctx.session_id:
            await self._write_end_token(ctx.session_id)

    def all_activities(self) -> list[Callable[..., Any]]:
        return [
            fn
            for method_name in dir(self)
            if hasattr(
                fn := getattr(self, method_name),
                "__temporal_activity_definition",
            )
            and callable(fn)
        ]
