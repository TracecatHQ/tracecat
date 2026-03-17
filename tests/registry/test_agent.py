import textwrap
import uuid
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from tracecat_registry import ActionIsInterfaceError
from tracecat_registry.core.agent import action, agent
from tracecat_registry.sdk.agents import (
    encode_model_selection,
    rank_items,
    rank_items_pairwise,
    run_agent,
)

from tracecat.auth.types import Role

requires_openai_mocks = pytest.mark.usefixtures("mock_openai_secrets")
OPENAI_TEST_MODEL = encode_model_selection(
    source_id=None,
    model_provider="openai",
    model_name="gpt-4o-mini",
)


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
@pytest.mark.live_secret
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
            model=OPENAI_TEST_MODEL,
            actions=[],
            instructions="Be concise and factual.",
            output_type=output_type,
            max_tool_calls=0,
            max_requests=3,
        )


@pytest.mark.anyio
@pytest.mark.live_secret
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
            model=OPENAI_TEST_MODEL,
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

    with pytest.raises(ActionIsInterfaceError):
        await action(
            user_prompt=user_prompt,
            model=OPENAI_TEST_MODEL,
            instructions="Be empathetic and concise.",
            output_type=output_type,
            max_requests=3,
        )


@pytest.mark.anyio
@pytest.mark.parametrize("output_type", JSON_SCHEMA_OUTPUT_TYPES)
async def test_action_json_schema(output_type: Any) -> None:
    user_prompt = _prompt_for_output_type(
        output_type,
        "Draft a brief, empathetic customer update about a resolved incident.",
    )

    with pytest.raises(ActionIsInterfaceError):
        await action(
            user_prompt=user_prompt,
            model=OPENAI_TEST_MODEL,
            instructions="Be empathetic and concise.",
            output_type=output_type,
            max_requests=3,
        )


@pytest.mark.anyio
async def test_run_agent_keeps_legacy_positional_actions_slot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = AsyncMock(
        return_value={
            "output": "ok",
            "message_history": None,
            "duration": 0.1,
            "usage": {},
            "session_id": str(uuid.uuid4()),
        }
    )
    monkeypatch.setattr(
        "tracecat_registry.context.get_context",
        lambda: SimpleNamespace(agents=SimpleNamespace(run=run)),
    )

    await run_agent(
        "hello",
        "gpt-5",
        "openai",
        ["tools.slack.post_message"],
    )

    assert run.await_count == 1
    assert run.await_args is not None
    assert run.await_args.kwargs["config"].actions == ["tools.slack.post_message"]
    assert run.await_args.kwargs["config"].source_id is None


@pytest.mark.anyio
async def test_rank_items_keeps_legacy_positional_optional_slots(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rank = AsyncMock(return_value=["item-1"])
    monkeypatch.setattr(
        "tracecat_registry.context.get_context",
        lambda: SimpleNamespace(agents=SimpleNamespace(rank_items=rank)),
    )

    await rank_items(
        [{"id": "item-1", "text": "First"}],
        "Rank items",
        "gpt-5",
        "openai",
        {"temperature": 0.1},
        5,
        3,
        None,
        1,
        10,
        "11111111-1111-1111-1111-111111111111",
    )

    assert rank.await_count == 1
    assert rank.await_args is not None
    assert rank.await_args.kwargs["model_settings"] == {"temperature": 0.1}
    assert rank.await_args.kwargs["source_id"] == (
        "11111111-1111-1111-1111-111111111111"
    )
    assert rank.await_args.kwargs["min_items"] == 1
    assert rank.await_args.kwargs["max_items"] == 10


@pytest.mark.anyio
async def test_rank_items_pairwise_keeps_legacy_positional_optional_slots(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rank = AsyncMock(return_value=["item-1"])
    monkeypatch.setattr(
        "tracecat_registry.context.get_context",
        lambda: SimpleNamespace(agents=SimpleNamespace(rank_items_pairwise=rank)),
    )

    await rank_items_pairwise(
        [{"id": "item-1", "text": "First"}],
        "Rank items",
        "gpt-5",
        "openai",
        "custom_id",
        5,
        2,
        0.4,
        {"temperature": 0.1},
        5,
        3,
        None,
        1,
        10,
        "11111111-1111-1111-1111-111111111111",
    )

    assert rank.await_count == 1
    assert rank.await_args is not None
    assert rank.await_args.kwargs["id_field"] == "custom_id"
    assert rank.await_args.kwargs["batch_size"] == 5
    assert rank.await_args.kwargs["num_passes"] == 2
    assert rank.await_args.kwargs["refinement_ratio"] == 0.4
    assert rank.await_args.kwargs["model_settings"] == {"temperature": 0.1}
    assert rank.await_args.kwargs["source_id"] == (
        "11111111-1111-1111-1111-111111111111"
    )
    assert rank.await_args.kwargs["min_items"] == 1
    assert rank.await_args.kwargs["max_items"] == 10
