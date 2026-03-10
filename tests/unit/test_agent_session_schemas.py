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


def test_agent_session_create_rejects_preset_and_model_catalog_ref() -> None:
    with pytest.raises(ValueError, match="model_catalog_ref cannot be set"):
        AgentSessionCreate.model_validate(
            {
                "title": "New session",
                "entity_type": AgentSessionEntity.CASE,
                "entity_id": str(uuid.uuid4()),
                "agent_preset_id": str(uuid.uuid4()),
                "model_catalog_ref": "default_sidecar:default:abc:gpt-5",
            }
        )
