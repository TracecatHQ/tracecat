from __future__ import annotations

import uuid
from typing import Any, cast
from unittest.mock import AsyncMock, create_autospec, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.agent.session.service import AgentSessionService
from tracecat.agent.session.types import AgentSessionEntity, AgentSessionStatus
from tracecat.auth.types import Role
from tracecat.chat.schemas import BasicChatRequest
from tracecat.db.models import AgentSession
from tracecat.exceptions import TracecatConflictError


def _build_role(workspace_id: uuid.UUID) -> Role:
    return Role(
        type="user",
        service_id="tracecat-api",
        workspace_id=workspace_id,
        organization_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        scopes=frozenset({"agent:execute"}),
    )


def _build_service(role: Role) -> AgentSessionService:
    db_session = cast(
        AsyncSession,
        create_autospec(AsyncSession, instance=True, spec_set=True),
    )
    return AgentSessionService(cast(Any, db_session), role)


def _build_agent_session(
    *,
    workspace_id: uuid.UUID,
    session_id: uuid.UUID,
    status: AgentSessionStatus,
) -> AgentSession:
    agent_session = AgentSession(
        workspace_id=workspace_id,
        title="Test session",
        entity_type=AgentSessionEntity.COPILOT.value,
        entity_id=uuid.uuid4(),
        status=status.value,
    )
    agent_session.id = session_id
    return agent_session


@pytest.mark.anyio
@pytest.mark.parametrize(
    "status",
    [AgentSessionStatus.RUNNING, AgentSessionStatus.WAITING_FOR_APPROVAL],
)
async def test_validate_turn_request_rejects_active_turn_before_pending_rows(
    status: AgentSessionStatus,
) -> None:
    workspace_id = uuid.uuid4()
    session_id = uuid.uuid4()
    role = _build_role(workspace_id)
    service = _build_service(role)
    agent_session = _build_agent_session(
        workspace_id=workspace_id,
        session_id=session_id,
        status=status,
    )
    has_pending_approvals = AsyncMock(return_value=False)

    with (
        patch.object(service, "get_session", AsyncMock(return_value=agent_session)),
        patch.object(service, "has_pending_approvals", has_pending_approvals),
    ):
        with pytest.raises(TracecatConflictError, match="active turn"):
            await service.validate_turn_request(
                session_id=session_id,
                request=BasicChatRequest(message="hello"),
            )

    has_pending_approvals.assert_not_awaited()
