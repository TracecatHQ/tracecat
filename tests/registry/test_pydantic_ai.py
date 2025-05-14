from typing import Any

import pytest
from pydantic_ai.messages import TextPart, UserPromptPart
from tracecat_registry.integrations.pydantic_ai import _parse_message_history


@pytest.mark.parametrize(
    "model_provider,messages",
    [
        # https://platform.openai.com/docs/api-reference/chat/create
        (
            "openai",
            [
                # System: {"role": "system", "content": "You are a helpful assistant."} - handled by _parse_message_history
                {"role": "user", "content": "What is the capital of the moon?"},
                {
                    "role": "assistant",
                    "content": "The Moon's capital is Tranquility Base.",
                },
            ],
        ),
        # https://platform.openai.com/docs/api-reference/chat/create
        (
            "openai_responses",
            [
                # System: {"role": "system", "content": "You are a helpful assistant."} - handled by _parse_message_history
                {"role": "user", "content": "What is the capital of the moon?"},
                {
                    "role": "assistant",
                    "content": "The Moon's capital is Tranquility Base.",
                },
            ],
        ),
        # https://docs.anthropic.com/en/api/messages
        (
            "anthropic",
            [
                # System prompt: "You are a helpful assistant." - handled as a separate 'system' parameter by _parse_message_history
                {"role": "user", "content": "What is the capital of the moon?"},
                {
                    "role": "assistant",
                    "content": "The Moon's capital is Tranquility Base.",
                },
            ],
        ),
        # https://docs.aws.amazon.com/bedrock/latest/userguide/model-parameters-anthropic-claude-messages.html
        (
            "bedrock",
            [
                # System prompt: "You are a helpful assistant." - handled as a separate 'system' parameter by _parse_message_history for Claude
                {"role": "user", "content": "What is the capital of the moon?"},
                {
                    "role": "assistant",
                    "content": "The Moon's capital is Tranquility Base.",
                },
            ],
        ),
        # https://ai.google.dev/api/generate-content
        (
            "gemini",
            [
                # System prompt: "You are a helpful assistant." - handled as a separate 'system_instruction' by _parse_message_history
                {
                    "role": "user",
                    "parts": [{"text": "What is the capital of the moon?"}],
                },
                {
                    "role": "model",
                    "parts": [{"text": "The Moon's capital is Tranquility Base."}],
                },
            ],
        ),
    ],
)
def test_parse_message_history(model_provider: str, messages: list[dict[str, Any]]):
    parsed_messages = _parse_message_history(messages)
    expected_messages = [
        UserPromptPart(content="What is the capital of the moon?"),
        TextPart(content="The Moon's capital is Tranquility Base."),
    ]
    assert parsed_messages == expected_messages, (
        f"Failed to parse message history for {model_provider!r}"
    )
