from __future__ import annotations as _annotations

import asyncio
from datetime import timedelta

from temporalio import activity, workflow

with workflow.unsafe.imports_passed_through():
    import orjson
    from pydantic_ai.direct import model_request as direct_model_request
    from pydantic_ai.messages import (
        ModelMessage,
        ModelMessagesTypeAdapter,
        ModelRequest,
        TextPart,
        ToolCallPart,
        ToolReturnPart,
    )
    from pydantic_ai.models import ModelRequestParameters
    from pydantic_core import from_json, to_json
    from tracecat_registry.integrations.agents.builder import (
        build_agent_tool_definitions,
        call_tracecat_action,
    )

    from tracecat import config
    from tracecat.ee.agent.models import (
        ExecuteToolCallArgs,
        ExecuteToolCallResult,
        ModelRequestArgs,
        ModelRequestResult,
        ToolFilters,
    )
    from tracecat.logger import logger


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


@activity.defn
async def execute_tool_call(args: ExecuteToolCallArgs) -> ExecuteToolCallResult:
    """Execute a single tool call and return the result as a ToolReturnPart."""
    logger.info(f"Executing tool call: {args.tool_name}")

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


@workflow.defn
class AgenticLoopWorkflow:
    @workflow.init
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
        # Safety parameters (following Gemini CLI pattern)
        self.max_turns_per_message: int = 25
        self.current_turn_count: int = 0

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

            # Handle one message with agentic loop
            message = await self.prompt_queue.get()
            await self._handle_user_message(message)

    async def _handle_user_message(self, message: str) -> None:
        """Handle a single user message with full agentic loop until completion."""
        # Reset turn counter for this message
        self.current_turn_count = 0

        # Add user message to history
        self.message_history.append(ModelRequest.user_text_prompt(message))
        logger.info(f"Starting agentic loop for message: {message[:100]}...")

        # Agentic loop - continue until no more tool calls needed
        while True:
            self.current_turn_count += 1

            # Safety check: prevent infinite loops
            if self.current_turn_count > self.max_turns_per_message:
                logger.error(
                    f"Reached max turns ({self.max_turns_per_message}) for this message. "
                    "Stopping agentic loop to prevent infinite execution."
                )
                # Add an error message to let the LLM know what happened
                error_message = ModelRequest(
                    parts=[
                        ToolReturnPart(
                            tool_name="system",
                            tool_call_id="error",
                            content=f"Maximum turns ({self.max_turns_per_message}) exceeded. Please provide a final response.",
                        )
                    ]
                )
                self.message_history.append(error_message)
                break

            logger.info(
                f"Agentic turn {self.current_turn_count}/{self.max_turns_per_message}"
            )

            # Call LLM
            response = await workflow.execute_activity(
                model_request,
                ModelRequestArgs(
                    message_history=to_json(self.message_history),
                    tool_filters=self.tool_filters,
                ),
                task_queue=config.TEMPORAL__CLUSTER_QUEUE,
                start_to_close_timeout=timedelta(seconds=30),
            )

            # Parse the model response
            raw_response = from_json(response.model_response)
            model_response = ModelMessagesTypeAdapter.validate_python([raw_response])[0]
            self.message_history.append(model_response)

            # Check for tool calls in the response
            tool_calls = self._extract_tool_calls(model_response)

            if not tool_calls:
                # No tool calls - the agent is done with this message
                logger.info(
                    f"Agentic loop completed in {self.current_turn_count} turns"
                )
                break

            # Execute tool calls in parallel and add results to message history
            logger.info(f"Executing {len(tool_calls)} tool calls")
            await self._execute_tool_calls(tool_calls)

    def _extract_tool_calls(self, model_response: ModelMessage) -> list[ToolCallPart]:
        """Extract tool calls from a model response."""
        tool_calls = []
        if hasattr(model_response, "parts"):
            for part in model_response.parts:
                if isinstance(part, ToolCallPart):
                    tool_calls.append(part)
        return tool_calls

    async def _execute_tool_calls(self, tool_calls: list[ToolCallPart]) -> None:
        """Execute multiple tool calls in parallel and add results to message history."""
        # Execute all tool calls in parallel (following Gemini pattern)
        tool_results = await asyncio.gather(
            *[
                workflow.execute_activity(
                    execute_tool_call,
                    ExecuteToolCallArgs(
                        tool_name=tool_call.tool_name,
                        tool_args=tool_call.args
                        if isinstance(tool_call.args, dict)
                        else {},
                        tool_call_id=tool_call.tool_call_id,
                    ),
                    task_queue=config.TEMPORAL__CLUSTER_QUEUE,
                    start_to_close_timeout=timedelta(
                        seconds=60
                    ),  # Longer timeout for tool execution
                )
                for tool_call in tool_calls
            ]
        )

        # Convert results to ToolReturnParts and add to message history
        tool_return_parts = []
        for result in tool_results:
            raw_tool_return = from_json(result.tool_return)
            # Recreate ToolReturnPart from the serialized data
            tool_return_part = ToolReturnPart(
                tool_name=raw_tool_return["tool_name"],
                tool_call_id=raw_tool_return["tool_call_id"],
                content=raw_tool_return["content"],
            )
            tool_return_parts.append(tool_return_part)

            if result.error:
                logger.warning(f"Tool execution error: {result.error}")

        # Add all tool returns as a single ModelRequest
        if tool_return_parts:
            tool_response_message = ModelRequest(parts=tool_return_parts)
            self.message_history.append(tool_response_message)
            logger.info(
                f"Added {len(tool_return_parts)} tool results to message history"
            )
