from __future__ import annotations

from pydantic_ai.messages import (
    ModelMessage,
    ModelResponse,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai.tools import RunContext

from tracecat.logger import logger

# Maximum number of messages to keep in history
MESSAGE_WINDOW = 15


def _has_tool_call(msg: ModelMessage) -> bool:
    """Check if message has a tool call."""
    return isinstance(msg, ModelResponse) and any(
        isinstance(part, ToolCallPart) for part in msg.parts
    )


def _has_tool_return(msg: ModelMessage) -> bool:
    """Check if message has a tool return."""
    return any(isinstance(part, ToolReturnPart) for part in msg.parts)


def _get_tool_call_ids(msg: ModelMessage) -> set[str]:
    """Get all tool call IDs from a message."""
    return {p.tool_call_id for p in msg.parts if isinstance(p, ToolCallPart)}


def _get_tool_return_ids(msg: ModelMessage) -> set[str]:
    """Get all tool return IDs from a message."""
    return {p.tool_call_id for p in msg.parts if isinstance(p, ToolReturnPart)}


def _clean_orphaned_tool_messages(
    messages: list[ModelMessage],
) -> list[ModelMessage]:
    """Remove any tool calls without matching returns and vice versa.

    Scans the entire history and removes:
    - Tool calls (ModelResponse) where next message doesn't have matching returns
    - Tool returns (ModelRequest) where previous message doesn't have matching calls
    """
    if not messages:
        return messages

    result: list[ModelMessage] = []
    i = 0

    while i < len(messages):
        msg = messages[i]

        # Check for orphaned tool calls
        if _has_tool_call(msg):
            call_ids = _get_tool_call_ids(msg)

            # Check if next message has matching returns
            has_matching_returns = False
            if i + 1 < len(messages):
                next_msg = messages[i + 1]
                return_ids = _get_tool_return_ids(next_msg)
                has_matching_returns = bool(call_ids & return_ids)

            if not has_matching_returns:
                # Skip this orphaned tool call
                i += 1
                continue

        # Check for orphaned tool returns
        if _has_tool_return(msg):
            return_ids = _get_tool_return_ids(msg)

            # Check if previous kept message has matching calls
            has_matching_calls = False
            if result:
                prev_msg = result[-1]
                call_ids = _get_tool_call_ids(prev_msg)
                has_matching_calls = bool(return_ids & call_ids)

            if not has_matching_calls:
                # Skip this orphaned tool return
                i += 1
                continue

        result.append(msg)
        i += 1

    return result


def trim_history_processor(
    ctx: RunContext[None],
    messages: list[ModelMessage],
) -> list[ModelMessage]:
    """Trim message history to a fixed window size.

    Cleans orphaned tool calls/returns, then finds a safe cut point.
    """
    # First, clean any orphaned tool messages from the input
    messages = _clean_orphaned_tool_messages(messages)

    if len(messages) <= MESSAGE_WINDOW:
        return messages

    # Start at target cut point and search backward for a safe cut
    target_cut = len(messages) - MESSAGE_WINDOW

    for cut_index in range(target_cut, -1, -1):
        first_message = messages[cut_index]

        # Skip if first message has tool returns (orphaned without calls)
        if _has_tool_return(first_message):
            continue

        # Skip if first message has tool calls (violates AI model ordering rules)
        if _has_tool_call(first_message):
            continue

        # Found a safe cut point
        result = messages[cut_index:]

        if len(result) < len(messages):
            logger.info(
                "History trimmed",
                original=len(messages),
                kept=len(result),
                cut_index=cut_index,
                model=ctx.model.model_name,
            )

        return result

    # No safe cut point found, keep all messages
    return messages
