from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest

from tracecat.agent.session.schemas import (
    AgentSessionCreate,
    AgentSessionRead,
    AgentSessionUpdate,
)
from tracecat.agent.session.types import AgentSessionEntity

_CREATE_BASE = {
    "title": "New session",
    "entity_type": AgentSessionEntity.AGENT_PRESET,
    "entity_id": str(uuid.uuid4()),
}


@pytest.mark.parametrize(
    ("schema", "payload", "field"),
    [
        pytest.param(
            AgentSessionCreate,
            _CREATE_BASE
            | {
                "channel_context": {
                    "channel_type": "slack",
                    "channel_id": "C123",
                    "thread_ts": "1700000000.123",
                }
            },
            "channel_context",
            id="create-channel-context",
        ),
        pytest.param(
            AgentSessionCreate,
            _CREATE_BASE | {"agents_binding": {"subagents": []}},
            "agents_binding",
            id="create-agents-binding",
        ),
        pytest.param(
            AgentSessionUpdate,
            {"title": "Updated session", "agents_binding": {"subagents": []}},
            "agents_binding",
            id="update-agents-binding",
        ),
    ],
)
def test_agent_session_payloads_ignore_server_owned_fields(
    schema: type[AgentSessionCreate] | type[AgentSessionUpdate],
    payload: dict[str, Any],
    field: str,
) -> None:
    """Clients cannot mass-assign server-owned session fields."""
    parsed = schema.model_validate(payload)

    assert parsed.title == payload["title"]
    assert field not in parsed.model_dump(mode="json")


def test_agent_session_read_defaults_binding_without_availability_flags() -> None:
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
        last_stream_id=None,
        parent_session_id=None,
        created_at=now,
        updated_at=now,
    )
    session = AgentSessionRead.model_validate(source, from_attributes=True)
    assert session.agents_binding is None
    assert "backend_available" not in session.model_dump()
    assert "history_available" not in session.model_dump()
