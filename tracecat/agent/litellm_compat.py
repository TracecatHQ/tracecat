"""Monkeypatches for LiteLLM's Anthropic pass-through adapter.

Fixes tool_call_id length incompatibility when routing Anthropic-format
requests to OpenAI models. Anthropic tool_use IDs can be ~116 chars,
but OpenAI enforces a 40-char max on tool_call_id fields.

The existing adapter truncates tool *names* (64-char limit) but passes
tool_call IDs through unchanged. This patch adds deterministic ID
truncation using the same hash-based approach.

Applied at module import time in gateway.py (loaded inside LiteLLM process).

Upstream tracking:
- https://github.com/BerriAI/litellm/issues/17904
- https://github.com/BerriAI/litellm/issues/22317
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Sequence
from typing import Any

logger = logging.getLogger(__name__)

# OpenAI enforces max 40 chars for tool_call_id
OPENAI_MAX_TOOL_CALL_ID_LENGTH = 40
_TOOL_ID_HASH_LENGTH = 8
_TOOL_ID_PREFIX_LENGTH = (
    OPENAI_MAX_TOOL_CALL_ID_LENGTH - _TOOL_ID_HASH_LENGTH - 1  # 31
)


def truncate_tool_call_id(tool_id: str) -> str:
    """Truncate a tool_call_id to fit OpenAI's 40-char limit.

    Uses format: {31-char-prefix}_{8-char-hash} to avoid collisions.
    """
    if len(tool_id) <= OPENAI_MAX_TOOL_CALL_ID_LENGTH:
        return tool_id
    id_hash = hashlib.sha256(tool_id.encode()).hexdigest()[:_TOOL_ID_HASH_LENGTH]
    return f"{tool_id[:_TOOL_ID_PREFIX_LENGTH]}_{id_hash}"


def _truncate_tool_call_ids_in_messages(messages: Sequence[Any]) -> None:
    """Truncate tool_call IDs in-place across OpenAI-format messages.

    Builds a mapping from original → truncated IDs, then rewrites both:
    - assistant message tool_calls[].id
    - tool message tool_call_id

    This ensures IDs stay paired after truncation.
    """
    # First pass: collect all tool_call IDs from assistant messages
    id_mapping: dict[str, str] = {}
    for msg in messages:
        if msg.get("role") == "assistant" and (tool_calls := msg.get("tool_calls")):
            for tc in tool_calls:
                original_id = tc.get("id", "")
                if len(original_id) > OPENAI_MAX_TOOL_CALL_ID_LENGTH:
                    truncated = truncate_tool_call_id(original_id)
                    id_mapping[original_id] = truncated

    if not id_mapping:
        return

    # Second pass: rewrite IDs in both assistant and tool messages
    for msg in messages:
        if msg.get("role") == "assistant" and (tool_calls := msg.get("tool_calls")):
            for tc in tool_calls:
                original_id = tc.get("id", "")
                if original_id in id_mapping:
                    tc["id"] = id_mapping[original_id]
        elif msg.get("role") == "tool":
            original_id = msg.get("tool_call_id", "")
            if original_id in id_mapping:
                msg["tool_call_id"] = id_mapping[original_id]

    logger.info(
        "Truncated %d tool_call_id(s) for OpenAI compatibility",
        len(id_mapping),
    )


def apply_patch() -> None:
    """Apply monkeypatches to LiteLLM's Anthropic adapter.

    Patches translate_anthropic_to_openai to truncate tool_call IDs
    in the translated messages before they're sent to OpenAI.
    """
    from litellm.llms.anthropic.experimental_pass_through.adapters.transformation import (
        LiteLLMAnthropicMessagesAdapter,
    )

    original_translate = LiteLLMAnthropicMessagesAdapter.translate_anthropic_to_openai

    def patched_translate_anthropic_to_openai(self, anthropic_message_request):
        openai_request, tool_name_mapping = original_translate(
            self, anthropic_message_request
        )
        # Post-process: truncate tool_call IDs in the translated messages
        if messages := openai_request.get("messages"):
            _truncate_tool_call_ids_in_messages(messages)
        return openai_request, tool_name_mapping

    LiteLLMAnthropicMessagesAdapter.translate_anthropic_to_openai = (
        patched_translate_anthropic_to_openai
    )

    logger.info("Applied LiteLLM tool_call_id truncation patch")
