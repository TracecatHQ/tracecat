"""Core agentic loop implementation."""

from __future__ import annotations as _annotations

from dataclasses import dataclass

import orjson
from pydantic_ai.messages import (
    ModelMessage,
    ModelMessagesTypeAdapter,
    ModelRequest,
    ToolReturnPart,
)
from pydantic_core import from_json, to_json

from tracecat.ee.agent.constants import MAX_TURNS_PER_MESSAGE
from tracecat.ee.agent.models import (
    AgentDeps,
    ModelRequestArgs,
    ToolFilters,
)
from tracecat.ee.agent.tools import execute_tool_calls_parallel, extract_tool_calls
from tracecat.logger import logger


def build_message_history(message_history_bytes: bytes) -> list[ModelMessage]:
    """Build message history from serialized bytes.

    Args:
        message_history_bytes: Serialized message history

    Returns:
        List of ModelMessage objects
    """
    raw_messages = orjson.loads(message_history_bytes)
    return ModelMessagesTypeAdapter.validate_python(raw_messages)


@dataclass
class AgentTurnResult:
    message_history: list[ModelMessage]
    turn_count: int


# Accept dependencies as a dataclass to allow for easier mocking in tests
async def run_agent_loop(
    user_prompt: str,
    messages: list[ModelMessage],
    tool_filters: ToolFilters,
    deps: AgentDeps,
    max_turns: int = MAX_TURNS_PER_MESSAGE,
) -> AgentTurnResult:
    """Run the core agentic loop until completion.

    This is the heart of the agent logic - it continues calling the LLM
    and executing tools until the LLM responds without any tool calls.

    Args:
        messages: Current message history
        tool_filters: Tool filters for the agent
        max_turns: Maximum number of turns to prevent infinite loops
        task_queue: Temporal task queue name

    Returns:
        Updated message history with all turns completed

    Raises:
        RuntimeError: If max turns exceeded
    """

    current_turn_count = 0
    working_messages = messages.copy() + [ModelRequest.user_text_prompt(user_prompt)]

    # Agentic loop - continue until no more tool calls needed
    while True:
        current_turn_count += 1

        # Safety check: prevent infinite loops
        if current_turn_count > max_turns:
            logger.error(
                f"Reached max turns ({max_turns}) for this message. "
                "Stopping agentic loop to prevent infinite execution."
            )
            # Add an error message to let the LLM know what happened
            error_message = ModelRequest(
                parts=[
                    ToolReturnPart(
                        tool_name="system",
                        tool_call_id="error",
                        content=f"Maximum turns ({max_turns}) exceeded. Please provide a final response.",
                    )
                ]
            )
            working_messages.append(error_message)
            break

        logger.info(f"Agentic turn {current_turn_count}/{max_turns}")

        # Call LLM
        response = await deps.call_model(
            ModelRequestArgs(
                message_history=to_json(working_messages),
                tool_filters=tool_filters,
            )
        )

        # Parse the model response
        raw_response = from_json(response.model_response)
        model_response = ModelMessagesTypeAdapter.validate_python([raw_response])[0]
        working_messages.append(model_response)

        # Check for tool calls in the response
        tool_calls = extract_tool_calls(model_response)

        if not tool_calls:
            # No tool calls - the agent is done with this message
            logger.info(f"Agentic loop completed in {current_turn_count} turns")
            break

        # Execute tool calls in parallel and add results to message history
        logger.info(f"Executing {len(tool_calls)} tool calls")
        tool_return_parts = await execute_tool_calls_parallel(deps, tool_calls)

        # Add all tool returns as a single ModelRequest
        if tool_return_parts:
            # Cast to list[ModelRequestPart] since ToolReturnPart is a ModelRequestPart
            tool_response_message = ModelRequest(parts=tool_return_parts)  # type: ignore[arg-type]
            working_messages.append(tool_response_message)
            logger.info(
                f"Added {len(tool_return_parts)} tool results to message history"
            )

    return AgentTurnResult(
        message_history=working_messages,
        turn_count=current_turn_count,
    )
