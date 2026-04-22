"""Monkeypatches for LiteLLM's Anthropic pass-through adapter.

Fixes tool_call_id length incompatibility when routing Anthropic-format
requests to OpenAI models. Anthropic tool_use IDs can exceed OpenAI's
40-character limit for tool_call_id fields.
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Sequence
from typing import Any

logger = logging.getLogger(__name__)

OPENAI_MAX_TOOL_CALL_ID_LENGTH = 40
_TOOL_ID_HASH_LENGTH = 8
_TOOL_ID_PREFIX_LENGTH = OPENAI_MAX_TOOL_CALL_ID_LENGTH - _TOOL_ID_HASH_LENGTH - 1


def truncate_tool_call_id(tool_id: str) -> str:
    """Truncate a tool_call_id to fit OpenAI's 40-character limit."""
    if len(tool_id) <= OPENAI_MAX_TOOL_CALL_ID_LENGTH:
        return tool_id
    id_hash = hashlib.sha256(tool_id.encode()).hexdigest()[:_TOOL_ID_HASH_LENGTH]
    return f"{tool_id[:_TOOL_ID_PREFIX_LENGTH]}_{id_hash}"


def _truncate_tool_call_ids_in_messages(messages: Sequence[Any]) -> None:
    """Truncate tool_call IDs in-place across OpenAI-format messages."""
    id_mapping: dict[str, str] = {}
    for msg in messages:
        if msg.get("role") == "assistant" and (tool_calls := msg.get("tool_calls")):
            for tool_call in tool_calls:
                original_id = tool_call.get("id", "")
                if len(original_id) > OPENAI_MAX_TOOL_CALL_ID_LENGTH:
                    id_mapping[original_id] = truncate_tool_call_id(original_id)

    if not id_mapping:
        return

    for msg in messages:
        if msg.get("role") == "assistant" and (tool_calls := msg.get("tool_calls")):
            for tool_call in tool_calls:
                original_id = tool_call.get("id", "")
                if original_id in id_mapping:
                    tool_call["id"] = id_mapping[original_id]
        elif msg.get("role") == "tool":
            original_id = msg.get("tool_call_id", "")
            if original_id in id_mapping:
                msg["tool_call_id"] = id_mapping[original_id]

    logger.info(
        "Truncated %d tool_call_id(s) for OpenAI compatibility", len(id_mapping)
    )


def apply_patch() -> None:
    """Apply monkeypatches to LiteLLM's Anthropic adapter."""
    from litellm.llms.anthropic.experimental_pass_through.adapters.transformation import (
        LiteLLMAnthropicMessagesAdapter,
    )

    original_translate = LiteLLMAnthropicMessagesAdapter.translate_anthropic_to_openai

    def patched_translate_anthropic_to_openai(self, anthropic_message_request):
        openai_request, tool_name_mapping = original_translate(
            self, anthropic_message_request
        )
        if messages := openai_request.get("messages"):
            _truncate_tool_call_ids_in_messages(messages)
        return openai_request, tool_name_mapping

    LiteLLMAnthropicMessagesAdapter.translate_anthropic_to_openai = (
        patched_translate_anthropic_to_openai
    )
    logger.info("Applied LiteLLM tool_call_id truncation patch")
