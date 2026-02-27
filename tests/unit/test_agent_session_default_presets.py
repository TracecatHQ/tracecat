"""Tests for default system preset selection in AgentSessionService."""

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from tracecat.agent.preset.service import SYSTEM_PRESET_SLUG_WORKSPACE_COPILOT
from tracecat.agent.session.schemas import AgentSessionCreate
from tracecat.agent.session.service import AgentSessionService
from tracecat.agent.session.types import AgentSessionEntity
from tracecat.auth.types import Role
from tracecat.chat.tools import get_default_tools


@pytest.fixture
def role() -> Role:
    return Role(
        type="user",
        service_id="tracecat-api",
        workspace_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
    )


@pytest.mark.anyio
async def test_create_session_uses_default_system_preset_for_copilot(
    role: Role,
) -> None:
    session = AsyncMock()
    preset_id = uuid.uuid4()
    session.execute.return_value = SimpleNamespace(scalar_one_or_none=lambda: preset_id)

    service = AgentSessionService(session, role)
    created = await service.create_session(
        AgentSessionCreate(
            title="Copilot chat",
            entity_type=AgentSessionEntity.COPILOT,
            entity_id=uuid.uuid4(),
        )
    )

    assert created.agent_preset_id == preset_id
    assert created.tools is None
    stmt = session.execute.await_args.args[0]
    assert SYSTEM_PRESET_SLUG_WORKSPACE_COPILOT in str(stmt)


@pytest.mark.anyio
async def test_create_session_falls_back_to_legacy_tools_when_default_missing(
    role: Role,
) -> None:
    session = AsyncMock()
    session.execute.return_value = SimpleNamespace(scalar_one_or_none=lambda: None)

    service = AgentSessionService(session, role)
    created = await service.create_session(
        AgentSessionCreate(
            title="Case chat",
            entity_type=AgentSessionEntity.CASE,
            entity_id=uuid.uuid4(),
        )
    )

    assert created.agent_preset_id is None
    assert created.tools == get_default_tools(AgentSessionEntity.CASE.value)
