from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, Mock

import pytest

from tracecat.agent.session.service import AgentSessionService
from tracecat.auth.types import Role
from tracecat.chat.enums import MessageKind
from tracecat.db.models import AgentSession


def _mock_scalar_result(items: list[Any]) -> Mock:
    scalars = Mock()
    scalars.all.return_value = items
    result = Mock()
    result.scalars.return_value = scalars
    return result


def _build_service() -> tuple[AgentSessionService, AgentSession]:
    workspace_id = uuid.uuid4()
    role = Role(
        type="service",
        service_id="tracecat-api",
        workspace_id=workspace_id,
        organization_id=uuid.uuid4(),
        scopes=frozenset({"agent:execute"}),
    )
    session = SimpleNamespace()
    service = AgentSessionService(cast(Any, session), role)
    agent_session = AgentSession(
        workspace_id=workspace_id,
        title="Chat",
        created_by=None,
        entity_type="case",
        entity_id=uuid.uuid4(),
    )
    return service, agent_session


@pytest.mark.anyio
async def test_list_messages_preserves_compaction_trigger() -> None:
    service, agent_session = _build_service()
    compaction_entry = SimpleNamespace(
        id=uuid.uuid4(),
        kind=MessageKind.COMPACTION.value,
        content={
            "type": "system",
            "subtype": "compact_boundary",
            "compactMetadata": {
                "preTokens": 128000,
                "trigger": "auto",
            },
        },
    )

    service.get_session = AsyncMock(return_value=agent_session)
    service.session.execute = AsyncMock(
        side_effect=[
            _mock_scalar_result([compaction_entry]),
            _mock_scalar_result([]),
        ]
    )

    messages = await service.list_messages(agent_session.id)

    assert len(messages) == 1
    assert messages[0].kind == MessageKind.COMPACTION
    assert messages[0].compaction == {
        "phase": "completed",
        "pre_tokens": 128000,
        "trigger": "auto",
    }
