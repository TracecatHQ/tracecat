import textwrap
from typing import Any

import pytest
from tracecat_registry.core.agent import action, agent

from tracecat.types.auth import Role

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

    result = await agent(
        user_prompt=user_prompt,
        model_name="gpt-4o-mini",
        model_provider="openai",
        actions=[],
        instructions="Be concise and factual.",
        output_type=output_type,
        max_tool_calls=0,
        max_requests=3,
    )

    assert isinstance(result, dict)
    assert "output" in result
    assert "message_history" in result
    assert isinstance(result["message_history"], list)

    output = result["output"]
    if output_type is None:
        assert isinstance(output, str)
        assert len(output) > 0
    elif output_type == "list[str]":
        assert isinstance(output, list)
        assert len(output) >= 1
        assert all(isinstance(x, str) and x for x in output)


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

    result = await agent(
        user_prompt=user_prompt,
        model_name="gpt-4o-mini",
        model_provider="openai",
        actions=[],
        instructions="Be concise and factual.",
        output_type=output_type,
        max_tool_calls=0,
        max_requests=3,
    )

    assert isinstance(result, dict)
    assert "output" in result
    assert "message_history" in result
    assert isinstance(result["message_history"], list)

    output = result["output"]
    assert isinstance(output, dict)
    # At minimum, the schema requires 'summary'
    assert "summary" in output and isinstance(output["summary"], str)


@pytest.mark.anyio
@requires_openai_mocks
@pytest.mark.parametrize("output_type", PRIMITIVE_OUTPUT_TYPES)
async def test_action_primitives(output_type: Any, test_role: Role) -> None:
    user_prompt = _prompt_for_output_type(
        output_type,
        "Draft a brief, empathetic customer update about a resolved incident.",
    )

    result = await action(
        user_prompt=user_prompt,
        model_name="gpt-4o-mini",
        model_provider="openai",
        instructions="Be empathetic and concise.",
        output_type=output_type,
        max_requests=3,
    )

    assert isinstance(result, dict)
    assert "output" in result
    assert "message_history" in result
    assert isinstance(result["message_history"], list)

    output = result["output"]
    if output_type is None:
        assert isinstance(output, str)
        assert len(output) > 0
    elif output_type == "list[str]":
        assert isinstance(output, list)
        assert len(output) >= 1
        assert all(isinstance(x, str) and x for x in output)


@pytest.mark.anyio
@requires_openai_mocks
@pytest.mark.parametrize("output_type", JSON_SCHEMA_OUTPUT_TYPES)
async def test_action_json_schema(output_type: Any, test_role: Role) -> None:
    user_prompt = _prompt_for_output_type(
        output_type,
        "Draft a brief, empathetic customer update about a resolved incident.",
    )

    result = await action(
        user_prompt=user_prompt,
        model_name="gpt-4o-mini",
        model_provider="openai",
        instructions="Be empathetic and concise.",
        output_type=output_type,
        max_requests=3,
    )

    assert isinstance(result, dict)
    assert "output" in result
    assert "message_history" in result
    assert isinstance(result["message_history"], list)

    output = result["output"]
    assert isinstance(output, dict)
    assert "summary" in output and isinstance(output["summary"], str)
