from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

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
            "agents_binding": {"subagents": []},
        }
    )

    assert "agents_binding" not in session.model_dump(mode="json")


def test_agent_session_update_ignores_agents_binding_payload() -> None:
    session_update = AgentSessionUpdate.model_validate(
        {
            "title": "Updated session",
            "agents_binding": {"subagents": []},
        }
    )

    assert session_update.title == "Updated session"
    assert "agents_binding" not in session_update.model_dump(mode="json")


@pytest.mark.parametrize(
    "missing_availability", [None, "backend_available", "history_available"]
)
def test_agent_session_read_requires_availability_and_defaults_binding(
    missing_availability: str | None,
) -> None:
    now = datetime.now(UTC)
    source = SimpleNamespace(
        id=uuid.uuid4(),
        workspace_id=uuid.uuid4(),
        title="New session",
        created_by=uuid.uuid4(),
        entity_type="approval",
        entity_id=uuid.uuid4(),
        channel_context=None,
        tools=None,
        mcp_integrations=None,
        agent_preset_id=None,
        agent_preset_version_id=None,
        harness_type=None,
        backend_available=True,
        history_available=True,
        last_stream_id=None,
        parent_session_id=None,
        created_at=now,
        updated_at=now,
    )
    if missing_availability is not None:
        delattr(source, missing_availability)
        with pytest.raises(ValidationError, match=missing_availability):
            AgentSessionRead.model_validate(source, from_attributes=True)
        return
    session = AgentSessionRead.model_validate(source, from_attributes=True)
    assert session.agents_binding is None
