from __future__ import annotations

from dataclasses import replace

from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai.tools import RunContext
from pydantic_core import to_json

from tracecat.config import (
    TRACECAT__AGENT_DEFAULT_CONTEXT_LIMIT,
    TRACECAT__AGENT_TOOL_OUTPUT_LIMIT,
    TRACECAT__MODEL_CONTEXT_LIMITS,
)
from tracecat.logger import logger


def _count_tokens(msg: ModelMessage) -> int:
    """Count tokens in a message (~4 bytes per token)."""
    return len(to_json(msg)) // 4


def _truncate_content(content: str, max_chars: int) -> str:
    """Truncate content with indicator."""
    if len(content) <= max_chars:
        return content
    return (
        content[:max_chars]
        + f"\n\n[... truncated {len(content) - max_chars} chars ...]"
    )


def truncate_tool_returns_processor(
    ctx: RunContext[None],
    messages: list[ModelMessage],
) -> list[ModelMessage]:
    """Truncate large tool return outputs to fit within limits.

    Uses TRACECAT__AGENT_TOOL_OUTPUT_LIMIT from config (default 20k chars).
    """
    max_chars = TRACECAT__AGENT_TOOL_OUTPUT_LIMIT
    result: list[ModelMessage] = []

    for msg in messages:
        if not isinstance(msg, ModelRequest):
            result.append(msg)
            continue

        # Check if any tool returns need truncation
        needs_truncation = False
        for part in msg.parts:
            if isinstance(part, ToolReturnPart):
                content = part.content
                if isinstance(content, str) and len(content) > max_chars:
                    needs_truncation = True
                    break

        if not needs_truncation:
            result.append(msg)
            continue

        # Truncate tool return parts
        new_parts = []
        for part in msg.parts:
            if isinstance(part, ToolReturnPart):
                content = part.content
                if isinstance(content, str) and len(content) > max_chars:
                    truncated = _truncate_content(content, max_chars)
                    new_parts.append(replace(part, content=truncated))
                else:
                    new_parts.append(part)
            else:
                new_parts.append(part)

        result.append(replace(msg, parts=new_parts))

    return result


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
    """Trim message history to fit within model's context limit.

    Cleans orphaned tool calls/returns, then trims from the front
    based on actual token counts until we fit within budget.
    """
    # First, clean any orphaned tool messages from the input
    messages = _clean_orphaned_tool_messages(messages)

    if not messages:
        return messages

    # Get context limit for this model
    token_budget = TRACECAT__MODEL_CONTEXT_LIMITS.get(
        ctx.model.model_name, TRACECAT__AGENT_DEFAULT_CONTEXT_LIMIT
    )

    # Count tokens for each message
    message_tokens = [_count_tokens(msg) for msg in messages]
    total_tokens = sum(message_tokens)

    # If we're under budget, keep everything
    if total_tokens <= token_budget:
        return messages

    # Work backwards from the end, accumulating tokens until we hit budget
    accumulated_tokens = 0
    cut_index = len(messages)

    for i in range(len(messages) - 1, -1, -1):
        msg_tokens = message_tokens[i]

        if accumulated_tokens + msg_tokens > token_budget:
            # This message would exceed budget, cut here
            cut_index = i + 1
            break

        accumulated_tokens += msg_tokens

    # Search backward from cut_index to find a safe cut point
    for safe_cut in range(cut_index, len(messages)):
        first_message = messages[safe_cut]

        # Skip if first message has tool returns (orphaned without calls)
        if _has_tool_return(first_message):
            continue

        # Skip if first message has tool calls (violates AI model ordering rules)
        if _has_tool_call(first_message):
            continue

        # Found a safe cut point
        result = messages[safe_cut:]

        if len(result) < len(messages):
            kept_tokens = sum(message_tokens[safe_cut:])
            logger.info(
                "History trimmed",
                original=len(messages),
                kept=len(result),
                cut_index=safe_cut,
                total_tokens=total_tokens,
                kept_tokens=kept_tokens,
                token_budget=token_budget,
                model=ctx.model.model_name,
            )

        return result

    # No safe cut point found, keep all messages
    return messages
