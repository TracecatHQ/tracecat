from __future__ import annotations as _annotations

import asyncio
from datetime import timedelta

from pydantic_ai.messages import TextPart
from temporalio import activity, workflow

with workflow.unsafe.imports_passed_through():
    import orjson
    from pydantic import BaseModel, Field
    from pydantic_ai.direct import model_request as direct_model_request
    from pydantic_ai.messages import (
        ModelMessage,
        ModelMessagesTypeAdapter,
        ModelRequest,
        ToolCallPart,
    )
    from pydantic_ai.models import ModelRequestParameters
    from pydantic_core import from_json, to_json
    from tracecat_registry.integrations.agents.builder import (
        build_agent_tool_definitions,
    )

    from tracecat import config
    from tracecat.logger import logger


class ToolFilters(BaseModel):
    actions: list[str] | None = None
    namespaces: list[str] | None = None


class ModelRequestArgs(BaseModel):
    message_history: bytes = Field(..., description="Serialized message history")
    tool_filters: ToolFilters = Field(
        default_factory=ToolFilters, description="Tool filters"
    )


class ModelRequestResult(BaseModel):
    model_response: bytes = Field(..., description="Serialized model response")


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
    for part in model_response.parts:
        # if tool call
        match part:
            case ToolCallPart(tool_name=tool_name, args=args):
                logger.info("Tool call", tool_name=tool_name, args=args)
            case TextPart(content=content):
                logger.info("Text part", content=content)

    return ModelRequestResult(model_response=to_json(model_response))


@workflow.defn
class AgenticLoopWorkflow:
    def __init__(self) -> None:
        self.message_history: list[ModelMessage] = []
        self.prompt_queue: asyncio.Queue[str] = asyncio.Queue()
        self.tool_filters: ToolFilters = ToolFilters(
            actions=[
                "core.cases.create_case",
                "core.cases.get_case",
                "core.cases.list_cases",
                "core.cases.update_case",
                "core.cases.list_cases",
            ],
        )

    @workflow.signal
    async def send_message(self, message: str) -> None:
        await self.prompt_queue.put(message)

    @workflow.run
    async def run(self) -> str:
        # Initial state
        should_end = False
        # Main conversation loop
        while True:
            # Receive user input
            logger.info(f"Waiting for prompt, queue size: {self.prompt_queue.qsize()}")
            await workflow.wait_condition(
                lambda: self.prompt_queue.qsize() > 0 or should_end
            )
            if should_end:
                return to_json(self.message_history).decode()

            # Handle one message
            message = await self.prompt_queue.get()
            # Run agentic loop until this message is fully handled
            self.message_history.append(ModelRequest.user_text_prompt(message))
            # Call LLM
            logger.info(f"Calling model with {len(self.message_history)} messages")
            response = await workflow.execute_activity(
                model_request,
                ModelRequestArgs(
                    message_history=to_json(self.message_history),
                    tool_filters=self.tool_filters,
                ),
                task_queue=config.TEMPORAL__CLUSTER_QUEUE,
                start_to_close_timeout=timedelta(seconds=30),
            )
            raw_response = from_json(response.model_response)
            new_messages = ModelMessagesTypeAdapter.validate_python([raw_response])
            logger.info(f"New messages: {new_messages}")
            self.message_history.extend(new_messages)
