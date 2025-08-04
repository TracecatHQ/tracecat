"""Tool-related utilities for agent workflows."""

from __future__ import annotations as _annotations

import asyncio

from pydantic_ai.messages import (
    ModelMessage,
    RetryPromptPart,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_core import from_json

from tracecat.ee.agent.models import AgentDeps, ExecuteToolCallArgs
from tracecat.logger import logger


def extract_tool_calls(model_response: ModelMessage) -> list[ToolCallPart]:
    """Extract tool calls from a model response.

    Args:
        model_response: The model response to extract tool calls from

    Returns:
        List of ToolCallPart objects found in the response
    """
    tool_calls = []
    if hasattr(model_response, "parts"):
        for part in model_response.parts:
            if isinstance(part, ToolCallPart):
                tool_calls.append(part)
    return tool_calls


async def execute_tool_calls_parallel(
    deps: AgentDeps,
    tool_calls: list[ToolCallPart],
) -> list[ToolReturnPart]:
    """Execute multiple tool calls in parallel and return results.

    Args:
        tool_calls: List of tool calls to execute
        task_queue: Temporal task queue name

    Returns:
        List of ToolReturnPart objects with execution results
    """

    # Execute all tool calls in parallel (following Gemini pattern)
    tool_results = await asyncio.gather(
        *[
            deps.call_tool(
                ExecuteToolCallArgs(
                    tool_name=tool_call.tool_name,
                    tool_args=tool_call.args
                    if isinstance(tool_call.args, dict)
                    else {},
                    tool_call_id=tool_call.tool_call_id,
                ),
            )
            for tool_call in tool_calls
        ]
    )

    # Convert results to ToolReturnParts
    tool_return_parts = []
    for result in tool_results:
        raw_tool_return = from_json(result.tool_return)
        match raw_tool_return.get("part_kind"):
            case "tool-return":
                tool_return_part = ToolReturnPart(
                    tool_name=raw_tool_return["tool_name"],
                    tool_call_id=raw_tool_return["tool_call_id"],
                    content=raw_tool_return["content"],
                )
            case "retry-prompt":
                tool_return_part = RetryPromptPart(
                    tool_name=raw_tool_return["tool_name"],
                    tool_call_id=raw_tool_return["tool_call_id"],
                    content=raw_tool_return["content"],
                )
            case _:
                raise ValueError(
                    f"Unexpected tool return type: {raw_tool_return.get('part-kind')}"
                )
        logger.info("Tool return part", tool_return_part=tool_return_part)

        tool_return_parts.append(tool_return_part)

        if result.error:
            logger.warning(f"Tool execution error: {result.error}")

    return tool_return_parts
