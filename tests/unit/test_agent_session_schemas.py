from __future__ import annotations

import uuid

import pytest

from tracecat.agent.session.schemas import AgentSessionCreate
from tracecat.agent.session.types import AgentSessionEntity


def test_agent_session_create_ignores_channel_context_payload() -> None:
    session = AgentSessionCreate.model_validate(
        {
            "title": "New session",
            "entity_type": AgentSessionEntity.AGENT_PRESET,
            "entity_id": str(uuid.uuid4()),
            "channel_context": {
                "channel_type": "slack",
                "channel_id": "C123",
                "thread_ts": "1700000000.123",
            },
        }
    )

    dumped = session.model_dump()
    assert "channel_context" not in dumped


def test_agent_session_create_rejects_preset_and_explicit_model_selection() -> None:
    with pytest.raises(
        ValueError,
        match="explicit model selection cannot be set when agent_preset_id is configured",
    ):
        AgentSessionCreate.model_validate(
            {
                "title": "New session",
                "entity_type": AgentSessionEntity.CASE,
                "entity_id": str(uuid.uuid4()),
                "agent_preset_id": str(uuid.uuid4()),
                "model_name": "gpt-5",
                "model_provider": "openai",
            }
        )


@pytest.mark.parametrize(
    ("payload", "pattern"),
    [
        (
            {"model_name": "gpt-5"},
            "model_name and model_provider must be set together when selecting a model",
        ),
        (
            {"source_id": str(uuid.uuid4())},
            "model_name and model_provider must be set together when selecting a model",
        ),
    ],
)
def test_agent_session_create_rejects_partial_explicit_model_selection(
    payload: dict[str, str],
    pattern: str,
) -> None:
    with pytest.raises(ValueError, match=pattern):
        AgentSessionCreate.model_validate(
            {
                "title": "New session",
                "entity_type": AgentSessionEntity.CASE,
                "entity_id": str(uuid.uuid4()),
                **payload,
            }
        )
