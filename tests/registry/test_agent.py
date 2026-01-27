import textwrap
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from tracecat_registry import ActionIsInterfaceError
from tracecat_registry.core.agent import action, agent

from tracecat.auth.types import Role

requires_openai_mocks = pytest.mark.usefixtures("mock_openai_secrets")


PRIMITIVE_OUTPUT_TYPES = [
    pytest.param(None, id="default-str"),
    pytest.param("list[str]", id="primitive-list"),
]

JSON_SCHEMA_OUTPUT_TYPES = [
    pytest.param(
        {
            "name": "IncidentSummary",
            "type": "object",
            "description": "Summarized incident report.",
            "properties": {
                "summary": {"type": "string"},
                "confidence": {"type": "number"},
            },
            "required": ["summary"],
            "additionalProperties": False,
        },
        id="json-schema",
    )
]


def _prompt_for_output_type(output_type: Any, base_prompt: str) -> str:
    """Craft a targeted prompt to help the model satisfy the output_type."""
    if isinstance(output_type, str) and output_type == "list[str]":
        return f"{base_prompt} Respond only with a Python list of short strings (3-5 items)."
    if isinstance(output_type, dict):
        return f"{base_prompt} Respond as a JSON object with keys 'summary' and 'confidence'."
    return base_prompt


@pytest.mark.anyio
@requires_openai_mocks
@pytest.mark.parametrize("output_type", PRIMITIVE_OUTPUT_TYPES)
async def test_agent_primitives(output_type: Any, test_role: Role) -> None:
    # Import here to avoid any accidental test-time patching/import shenanigans

    user_prompt = _prompt_for_output_type(
        output_type,
        "Summarize the latest incident in 1-2 sentences:\n\n"
        + textwrap.dedent("""
        ```json
        {
            "title": "Latest Incident Report",
            "summary": "Systems experienced a brief outage; services recovered within 5 minutes.",
            "recommendations": ["Review auto-scaling thresholds", "Notify affected customers"],
        }
        ```
        """),
    )

    with pytest.raises(ActionIsInterfaceError):
        await agent(
            user_prompt=user_prompt,
            model_name="gpt-4o-mini",
            model_provider="openai",
            actions=[],
            instructions="Be concise and factual.",
            output_type=output_type,
            max_tool_calls=0,
            max_requests=3,
        )


@pytest.mark.anyio
@requires_openai_mocks
@pytest.mark.parametrize("output_type", JSON_SCHEMA_OUTPUT_TYPES)
async def test_agent_json_schema(output_type: Any, test_role: Role) -> None:
    # Import here to avoid any accidental test-time patching/import shenanigans

    user_prompt = _prompt_for_output_type(
        output_type,
        "Summarize the latest incident in 1-2 sentences:\n\n"
        + textwrap.dedent(
            """
        ```json
        {
            "title": "Latest Incident Report",
            "summary": "Systems experienced a brief outage; services recovered within 5 minutes.",
            "recommendations": ["Review auto-scaling thresholds", "Notify affected customers"],
        }
        ```
        """
        ),
    )

    with pytest.raises(ActionIsInterfaceError):
        await agent(
            user_prompt=user_prompt,
            model_name="gpt-4o-mini",
            model_provider="openai",
            actions=[],
            instructions="Be concise and factual.",
            output_type=output_type,
            max_tool_calls=0,
            max_requests=3,
        )


@pytest.mark.anyio
@pytest.mark.parametrize("output_type", PRIMITIVE_OUTPUT_TYPES)
async def test_action_primitives(output_type: Any) -> None:
    user_prompt = _prompt_for_output_type(
        output_type,
        "Draft a brief, empathetic customer update about a resolved incident.",
    )

    mock_result = {
        "output": "Test output",
        "message_history": None,
        "duration": 1.0,
        "usage": None,
        "session_id": "test-session-id",
    }
    mock_ctx = MagicMock()
    mock_ctx.agents.run = AsyncMock(return_value=mock_result)

    with patch("tracecat_registry.core.agent.get_context", return_value=mock_ctx):
        result = await action(
            user_prompt=user_prompt,
            model_name="gpt-4o-mini",
            model_provider="openai",
            instructions="Be empathetic and concise.",
            output_type=output_type,
            max_requests=3,
        )

    assert result == mock_result
    mock_ctx.agents.run.assert_called_once()
    call_kwargs = mock_ctx.agents.run.call_args.kwargs
    assert call_kwargs["user_prompt"] == user_prompt
    assert call_kwargs["config"].model_name == "gpt-4o-mini"
    assert call_kwargs["config"].model_provider == "openai"
    assert call_kwargs["config"].output_type == output_type
    assert call_kwargs["max_requests"] == 3


@pytest.mark.anyio
@pytest.mark.parametrize("output_type", JSON_SCHEMA_OUTPUT_TYPES)
async def test_action_json_schema(output_type: Any) -> None:
    user_prompt = _prompt_for_output_type(
        output_type,
        "Draft a brief, empathetic customer update about a resolved incident.",
    )

    mock_result = {
        "output": {"summary": "Test summary", "confidence": 0.95},
        "message_history": None,
        "duration": 1.0,
        "usage": None,
        "session_id": "test-session-id",
    }
    mock_ctx = MagicMock()
    mock_ctx.agents.run = AsyncMock(return_value=mock_result)

    with patch("tracecat_registry.core.agent.get_context", return_value=mock_ctx):
        result = await action(
            user_prompt=user_prompt,
            model_name="gpt-4o-mini",
            model_provider="openai",
            instructions="Be empathetic and concise.",
            output_type=output_type,
            max_requests=3,
        )

    assert result == mock_result
    mock_ctx.agents.run.assert_called_once()
    call_kwargs = mock_ctx.agents.run.call_args.kwargs
    assert call_kwargs["user_prompt"] == user_prompt
    assert call_kwargs["config"].model_name == "gpt-4o-mini"
    assert call_kwargs["config"].model_provider == "openai"
    assert call_kwargs["config"].output_type == output_type
    assert call_kwargs["max_requests"] == 3
