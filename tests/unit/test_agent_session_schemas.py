from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

from tracecat.agent.session.schemas import (
    AgentSessionCreate,
    AgentSessionRead,
    AgentSessionUpdate,
)
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


def test_agent_session_create_ignores_agents_binding_payload() -> None:
    session = AgentSessionCreate.model_validate(
        {
            "title": "New session",
            "entity_type": AgentSessionEntity.AGENT_PRESET,
            "entity_id": str(uuid.uuid4()),
            "agents_binding": {"enabled": True, "subagents": []},
        }
    )

    assert "agents_binding" not in session.model_dump(mode="json")


def test_agent_session_update_ignores_agents_binding_payload() -> None:
    session_update = AgentSessionUpdate.model_validate(
        {
            "title": "Updated session",
            "agents_binding": {"enabled": True, "subagents": []},
        }
    )

    assert session_update.title == "Updated session"
    assert "agents_binding" not in session_update.model_dump(mode="json")


def test_agent_session_read_defaults_missing_agents_binding() -> None:
    now = datetime.now(UTC)
    session = AgentSessionRead.model_validate(
        SimpleNamespace(
            id=uuid.uuid4(),
            workspace_id=uuid.uuid4(),
            title="New session",
            created_by=uuid.uuid4(),
            entity_type="approval",
            entity_id=uuid.uuid4(),
            channel_context=None,
            tools=None,
            agent_preset_id=None,
            agent_preset_version_id=None,
            harness_type=None,
            last_stream_id=None,
            parent_session_id=None,
            created_at=now,
            updated_at=now,
        ),
        from_attributes=True,
    )

    assert session.agents_binding is None
