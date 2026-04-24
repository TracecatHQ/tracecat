from __future__ import annotations

import uuid

from tracecat.agent.session.schemas import AgentSessionCreate
from tracecat.agent.session.types import AgentSessionEntity
from tracecat.agent.subagents import ResolvedAgentsConfig


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


def test_agent_session_create_types_agents_binding() -> None:
    session = AgentSessionCreate.model_validate(
        {
            "title": "New session",
            "entity_type": AgentSessionEntity.AGENT_PRESET,
            "entity_id": str(uuid.uuid4()),
            "agents_binding": {"enabled": True, "subagents": []},
        }
    )

    assert isinstance(session.agents_binding, ResolvedAgentsConfig)
    assert session.agents_binding.enabled is True
    assert session.model_dump(mode="json")["agents_binding"] == {
        "enabled": True,
        "subagents": [],
    }


def test_agent_session_create_normalizes_empty_agents_binding_to_disabled() -> None:
    session = AgentSessionCreate.model_validate(
        {
            "title": "New session",
            "entity_type": AgentSessionEntity.AGENT_PRESET,
            "entity_id": str(uuid.uuid4()),
            "agents_binding": {},
        }
    )

    assert isinstance(session.agents_binding, ResolvedAgentsConfig)
    assert session.agents_binding.enabled is False
    assert session.model_dump(mode="json")["agents_binding"] == {
        "enabled": False,
        "subagents": [],
    }
