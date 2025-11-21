from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace

import orjson
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelRequestPart,
    ModelResponse,
    SystemPromptPart,
    ToolCallPart,
    ToolReturnPart,
)

from tracecat.agent.types import ModelMessageTA
from tracecat.config import (
    TRACECAT__AGENT_DEFAULT_CONTEXT_LIMIT,
    TRACECAT__AGENT_TOOL_OUTPUT_LIMIT,
    TRACECAT__MODEL_CONTEXT_LIMITS,
)
from tracecat.logger import logger


@dataclass(slots=True, frozen=True)
class _MessageEntry:
    """Helper for tracking retained messages during pruning."""

    index: int
    message: ModelMessage
    size: int


def prune_history(
    messages: Sequence[ModelMessage],
    model_name: str,
    *,
    reserved_tokens: int = 0,
) -> list[ModelMessage]:
    """Return the pruned message history to include when invoking the model.
    Args:
        messages: Historical messages to prune.
        model_name: Name of the model to determine context limit.
        reserved_tokens: Number of characters reserved for the current prompt (not in history).
    Returns:
        List of messages that fit within the context limit, prioritizing recent messages
        and always preserving the system prompt if present.
    """
    context_limit = TRACECAT__MODEL_CONTEXT_LIMITS.get(
        model_name, TRACECAT__AGENT_DEFAULT_CONTEXT_LIMIT
    )
    context_limit = max(0, context_limit - reserved_tokens)
    tool_limit = TRACECAT__AGENT_TOOL_OUTPUT_LIMIT

    retained: list[_MessageEntry] = []
    total_size = 0

    # Iterate from newest to oldest, keeping messages that fit within the limit
    for index in range(len(messages) - 1, -1, -1):
        message = messages[index]

        # Truncate tool outputs before calculating size
        truncated = _truncate_tool_outputs(message, tool_limit)
        message_size = _estimate_message_size(truncated)

        if retained and total_size + message_size > context_limit:
            break

        retained.append(_MessageEntry(index, truncated, message_size))
        total_size += message_size

    # Warn if the most recent message alone exceeds budget
    if retained and total_size > context_limit:
        logger.warning(
            "Most recent message exceeds context budget; model invocation may fail",
            message_size=retained[-1].size,
            total_size=total_size,
            context_limit=context_limit,
            reserved_tokens=reserved_tokens,
        )

    retained.reverse()

    # Ensure system prompt is retained (always at index 0 if it exists)
    if (
        messages
        and _has_system_prompt(messages[0])
        and all(entry.index != 0 for entry in retained)
    ):
        system_message = messages[0]
        system_size = _estimate_message_size(system_message)
        if retained:
            while retained and total_size + system_size > context_limit:
                popped = retained.pop(0)
                total_size -= popped.size
        retained.insert(0, _MessageEntry(0, system_message, system_size))
        total_size += system_size

    # Drop orphaned tool returns from the start of history
    while retained and _is_tool_result_only(retained[0].message):
        popped = retained.pop(0)
        total_size -= popped.size

    # Sanitize: remove any tool returns that don't have corresponding tool calls
    retained, total_size = _sanitize_orphaned_tool_returns(retained)

    logger.debug(
        "Pruned message history",
        original_count=len(messages),
        retained_count=len(retained),
        total_size=total_size,
        context_limit=context_limit,
    )

    return [entry.message for entry in retained]


def _truncate_tool_outputs(message: ModelMessage, limit: int) -> ModelMessage:
    if not isinstance(message, ModelRequest):
        return message

    updated_parts: list[ModelRequestPart] = []
    changed = False
    for part in message.parts:
        if isinstance(part, ToolReturnPart):
            truncated_content, did_change = truncate_tool_content(part.content, limit)
            if did_change:
                part = replace(part, content=truncated_content)
                changed = True
        updated_parts.append(part)

    if not changed:
        return message

    return replace(message, parts=tuple(updated_parts))


def truncate_tool_content(content: object, limit: int) -> tuple[object, bool]:
    """Truncate tool output content if it exceeds the specified limit.
    Args:
        content: The tool output content to potentially truncate.
        limit: Maximum size in characters.
    Returns:
        Tuple of (potentially truncated content, was_truncated boolean).
    """
    if content is None:
        return content, False

    # Serialize to check length
    try:
        if isinstance(content, str):
            serialized = content
        elif isinstance(content, (dict, list)):
            serialized = orjson.dumps(content).decode()
        else:
            serialized = str(content)
    except (TypeError, ValueError):
        serialized = str(content)

    if len(serialized) <= limit:
        return content, False

    return f"{serialized[:limit]}... [truncated]", True


def _estimate_message_size(message: ModelMessage) -> int:
    """Estimate the serialized size of a message in characters."""
    return len(ModelMessageTA.dump_json(message))


def _has_system_prompt(message: ModelMessage) -> bool:
    """Check if a message contains a system prompt part."""
    if not isinstance(message, ModelRequest):
        return False
    return any(isinstance(part, SystemPromptPart) for part in message.parts)


def _is_tool_result_only(message: ModelMessage) -> bool:
    if not isinstance(message, ModelRequest):
        return False
    return all(isinstance(part, ToolReturnPart) for part in message.parts)


def _sanitize_orphaned_tool_returns(
    retained: list[_MessageEntry],
) -> tuple[list[_MessageEntry], int]:
    """Remove tool returns that don't have corresponding tool calls in the history.
    When messages are pruned, we might drop a ModelResponse containing ToolCallPart
    but keep a later ModelRequest containing the corresponding ToolReturnPart. This
    causes API errors since the tool result references a non-existent tool call.
    Args:
        retained: List of message entries to clean.
    Returns:
        Tuple of (cleaned list, new total_size).
    """
    # Collect all valid tool_call_ids from tool calls in the history
    valid_tool_ids: set[str] = set()
    for entry in retained:
        if isinstance(entry.message, ModelResponse):
            for part in entry.message.parts:
                if isinstance(part, ToolCallPart):
                    valid_tool_ids.add(part.tool_call_id)

    # Filter out orphaned tool returns
    cleaned: list[_MessageEntry] = []
    for entry in retained:
        if not isinstance(entry.message, ModelRequest):
            cleaned.append(entry)
            continue

        # Filter parts, keeping only non-orphaned tool returns
        filtered_parts: list[ModelRequestPart] = []
        for part in entry.message.parts:
            if isinstance(part, ToolReturnPart):
                if part.tool_call_id in valid_tool_ids:
                    filtered_parts.append(part)
                else:
                    logger.debug(
                        "Dropping orphaned tool return",
                        tool_call_id=part.tool_call_id,
                        tool_name=part.tool_name,
                    )
            else:
                filtered_parts.append(part)

        # Only keep the message if it has parts left
        if filtered_parts:
            if len(filtered_parts) != len(entry.message.parts):
                # Message was modified, recalculate size
                updated_message = replace(entry.message, parts=tuple(filtered_parts))
                updated_size = _estimate_message_size(updated_message)
                entry = replace(entry, message=updated_message, size=updated_size)
            cleaned.append(entry)
        else:
            logger.debug("Dropping empty message after removing orphaned tool returns")

    total_size = sum(entry.size for entry in cleaned)
    return cleaned, total_size
