"""Compatibility with the pinned LiteLLM Anthropic adapter."""

from unittest.mock import Mock

import pytest
from litellm.llms.anthropic.experimental_pass_through.adapters.transformation import (
    LiteLLMAnthropicMessagesAdapter,
)
from litellm.types.llms.anthropic import AnthropicMessagesRequest

from tracecat.agent.litellm_compat import apply_patch, truncate_tool_call_id


@pytest.mark.parametrize("provider", [None, "openai", "bedrock"])
def test_adapter_forwards_provider_and_preserves_tool_call_pairs(
    monkeypatch: pytest.MonkeyPatch, provider: str | None
) -> None:
    original_translate = LiteLLMAnthropicMessagesAdapter.translate_anthropic_to_openai
    translate = Mock(wraps=original_translate)
    # Register the mutation with pytest so apply_patch cannot leak into other tests.
    monkeypatch.setattr(
        LiteLLMAnthropicMessagesAdapter, "translate_anthropic_to_openai", translate
    )
    apply_patch()

    tool_id = "synthetic_tool_call_" + "a" * 48
    request: AnthropicMessagesRequest = {
        "model": "synthetic-model",
        "max_tokens": 128,
        "messages": [
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": tool_id,
                        "name": "synthetic_tool",
                        "input": {},
                    }
                ],
            },
            {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": tool_id, "content": "ok"}
                ],
            },
        ],
    }
    adapter = LiteLLMAnthropicMessagesAdapter()
    translated, tool_name_mapping = adapter.translate_anthropic_to_openai(
        request, custom_llm_provider=provider
    )

    translate.assert_called_once_with(adapter, request, custom_llm_provider=provider)
    assistant, result = translated["messages"]
    assert assistant["role"] == "assistant"
    assert result["role"] == "tool"
    tool_calls = assistant.get("tool_calls")
    assert tool_calls is not None
    assert tool_calls[0]["id"] == truncate_tool_call_id(tool_id)
    assert result["tool_call_id"] == truncate_tool_call_id(tool_id)
    assert len(result["tool_call_id"]) == 40
    assert tool_name_mapping == {}
