import os
from typing import Any

import pytest
from dotenv import load_dotenv
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart
from tracecat_registry.integrations.pydantic_ai import _parse_message_history, call

load_dotenv()

# Skip tests if OpenAI API key is not available
skip_if_no_openai_api_key = pytest.mark.skipif(
    os.environ.get("OPENAI_API_KEY") is None,
    reason="OPENAI_API_KEY not available in environment variables",
)

# Mark all tests in this module to be skipped if no OpenAI API key is available
pytestmark = [
    skip_if_no_openai_api_key,
]


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
        ModelRequest(
            parts=[UserPromptPart(content="What is the capital of the moon?")],
        ),
        ModelResponse(
            parts=[TextPart(content="The Moon's capital is Tranquility Base.")],
        ),
    ]
    assert parsed_messages[0].parts[0].content == expected_messages[0].parts[0].content  # type: ignore
    assert parsed_messages[1].parts[0].content == expected_messages[1].parts[0].content  # type: ignore


def test_pydantic_ai_call():
    result = call(
        instructions="You are a helpful assistant.",
        user_prompt="What is the capital of France?",
        model_name="gpt-4o-mini",
        model_provider="openai",
    )
    assert isinstance(result, str)
    assert "Paris" in result


def test_pydantic_ai_call_with_bool_output_type():
    result = call(
        instructions="You are a helpful assistant.",
        user_prompt="Are you a helpful assistant?",
        model_name="gpt-4o-mini",
        model_provider="openai",
        output_type="bool",
    )
    assert isinstance(result, bool)
    assert result


def test_pydantic_ai_call_with_analyze_alert_schema():
    """Tests the call function with the analyze_alert.yml schema."""
    analyze_alert_schema = {
        "name": "alert_analysis",
        "type": "object",
        "properties": {
            "thoughts": {"type": "array", "items": {"type": "string"}},
            "who": {"type": "string"},
            "what": {"type": "string"},
            "when": {"type": "string"},
            "where": {"type": "string"},
            "why": {"type": "string"},
            "how": {"type": "string"},
            "false_positives": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "scenario": {"type": "string"},
                        "reasoning": {"type": "string"},
                    },
                    "required": ["scenario", "reasoning"],
                },
            },
            "true_positives": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "scenario": {"type": "string"},
                        "reasoning": {"type": "string"},
                    },
                    "required": ["scenario", "reasoning"],
                },
            },
        },
        "required": [
            "thoughts",
            "who",
            "what",
            "when",
            "where",
            "why",
            "how",
            "false_positives",
            "true_positives",
        ],
    }
    result = call(
        instructions="Analyze the provided security alert.",
        user_prompt="Alert: Unusual login detected from IP 1.2.3.4 for user 'test@example.com'.",
        model_name="gpt-4o-mini",
        model_provider="openai",
        output_type=analyze_alert_schema,
    )
    assert isinstance(result, dict)
    # Check for a few key fields
    assert "thoughts" in result
    assert "who" in result
    assert "what" in result
    assert "false_positives" in result
    assert "true_positives" in result


def test_pydantic_ai_call_with_build_timeline_schema():
    """Tests the call function with the build_timeline.yml schema."""
    build_timeline_schema = {
        "name": "timeline_analysis",
        "type": "object",
        "properties": {
            "thoughts": {"type": "array", "items": {"type": "string"}},
            "timeline": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "timestamp": {"type": "string"},
                        "actor_id": {"type": "string"},
                        "actor_type": {"type": "string"},
                        "user_agent": {"type": "string"},
                        "event_action": {"type": "string"},
                        "event_outcome": {"type": "string"},
                        "event_description": {"type": "string"},
                        "event_details": {"type": "object"},
                    },
                    "required": [
                        "timestamp",
                        "actor_id",
                        "actor_type",
                        "user_agent",
                        "event_action",
                        "event_outcome",
                        "event_description",
                        "event_details",
                    ],
                },
            },
            "relationships": {"type": "string"},
        },
        "required": ["thoughts", "timeline", "relationships"],
    }
    result = call(
        instructions="Build a timeline of events.",
        user_prompt="Event 1: User logged in at 2023-01-01T10:00:00Z. Event 2: User accessed file X at 2023-01-01T10:05:00Z.",
        model_name="gpt-4o-mini",
        model_provider="openai",
        output_type=build_timeline_schema,
    )
    assert isinstance(result, dict)
    # Check for a few key fields
    assert "thoughts" in result
    assert "timeline" in result
    assert "relationships" in result


def test_pydantic_ai_call_with_enum_output():
    """Tests the call function with a OpenAI call and an enum in the output schema."""
    order_status_schema = {
        "title": "OrderStatus",
        "type": "object",
        "properties": {
            "order_id": {"type": "string"},
            "status": {
                "type": "string",
                "enum": ["pending", "shipped", "delivered", "cancelled"],
            },
        },
        "required": ["order_id", "status"],
    }

    instructions = (
        "You are an order fulfillment system. Provide the order status in the specified JSON format. "
        "The status must be one of: pending, shipped, delivered, or cancelled."
    )
    user_prompt = "The order ABC123 has just been shipped. Please provide its status."

    # This test makes a live API call to OpenAI
    result = call(
        instructions=instructions,
        user_prompt=user_prompt,
        model_name="gpt-3.5-turbo",  # Using a cheaper model for live test
        model_provider="openai",
        output_type=order_status_schema,
    )

    assert isinstance(result, dict)
    assert "order_id" in result
    assert "status" in result
    assert result["status"] in ["pending", "shipped", "delivered", "cancelled"]


def test_pydantic_ai_call_with_tool_choice():
    """Tests that model_settings can include tool_choice for OpenAI."""

    # Define a simple mock calculator tool
    calculator_schema = {
        "type": "function",
        "function": {
            "name": "calculator",
            "description": "Perform a calculation",
            "parameters": {
                "type": "object",
                "properties": {
                    "expression": {
                        "type": "string",
                        "description": "The mathematical expression to evaluate",
                    }
                },
                "required": ["expression"],
            },
        },
    }

    # Call with tool_choice to force tool use
    result = call(
        instructions="You are a helpful calculator assistant.",
        user_prompt="What is 123 + 456?",
        model_name="gpt-4o-mini",
        model_provider="openai",
        model_settings={
            "temperature": 0.0,
            "tools": [calculator_schema],
            "tool_choice": {"type": "function", "function": {"name": "calculator"}},
        },
    )

    # Result should contain the calculation result in some form
    assert isinstance(result, str)
    assert "579" in result or "123 + 456" in result
