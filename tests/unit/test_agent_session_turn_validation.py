from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, create_autospec, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from temporalio.client import WorkflowExecutionStatus

from tracecat.agent.session.schemas import AgentSessionCancelRequest
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
    curr_run_id: uuid.UUID | None = None,
) -> AgentSession:
    agent_session = AgentSession(
        workspace_id=workspace_id,
        title="Test session",
        entity_type=AgentSessionEntity.COPILOT.value,
        entity_id=uuid.uuid4(),
        status=status.value,
        curr_run_id=curr_run_id,
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


@pytest.mark.anyio
async def test_validate_turn_request_rejects_idle_projection_with_active_workflow() -> (
    None
):
    workspace_id = uuid.uuid4()
    session_id = uuid.uuid4()
    run_id = uuid.uuid4()
    role = _build_role(workspace_id)
    service = _build_service(role)
    agent_session = _build_agent_session(
        workspace_id=workspace_id,
        session_id=session_id,
        status=AgentSessionStatus.IDLE,
        curr_run_id=run_id,
    )
    workflow_handle = SimpleNamespace(
        describe=AsyncMock(
            return_value=SimpleNamespace(status=WorkflowExecutionStatus.RUNNING)
        )
    )
    temporal_client = SimpleNamespace(
        get_workflow_handle=MagicMock(return_value=workflow_handle),
    )
    has_pending_approvals = AsyncMock(return_value=False)

    with (
        patch.object(service, "get_session", AsyncMock(return_value=agent_session)),
        patch.object(service, "has_pending_approvals", has_pending_approvals),
        patch(
            "tracecat.agent.session.service.get_temporal_client",
            AsyncMock(return_value=temporal_client),
        ),
    ):
        with pytest.raises(TracecatConflictError, match="active turn"):
            await service.validate_turn_request(
                session_id=session_id,
                request=BasicChatRequest(message="hello"),
            )

    has_pending_approvals.assert_not_awaited()
    workflow_handle.describe.assert_awaited_once()


@pytest.mark.anyio
@pytest.mark.parametrize(
    "status",
    [AgentSessionStatus.RUNNING, AgentSessionStatus.WAITING_FOR_APPROVAL],
)
async def test_request_cancel_accepts_active_turn_statuses(
    status: AgentSessionStatus,
) -> None:
    workspace_id = uuid.uuid4()
    session_id = uuid.uuid4()
    run_id = uuid.uuid4()
    role = _build_role(workspace_id)
    service = _build_service(role)
    agent_session = _build_agent_session(
        workspace_id=workspace_id,
        session_id=session_id,
        status=status,
        curr_run_id=run_id,
    )
    workflow_handle = SimpleNamespace(
        describe=AsyncMock(
            return_value=SimpleNamespace(status=WorkflowExecutionStatus.RUNNING)
        ),
        execute_update=AsyncMock(),
    )
    temporal_client = SimpleNamespace(
        get_workflow_handle=MagicMock(return_value=workflow_handle),
        get_workflow_handle_for=MagicMock(return_value=workflow_handle),
    )

    with (
        patch.object(service, "get_session", AsyncMock(return_value=agent_session)),
        patch(
            "tracecat.agent.session.service.get_temporal_client",
            AsyncMock(return_value=temporal_client),
        ),
    ):
        response = await service.request_cancel(
            session_id,
            AgentSessionCancelRequest(reason="user_cancel"),
        )

    assert response.session_id == session_id
    assert response.run_id == run_id
    assert response.turn_status is status
    workflow_handle.describe.assert_awaited_once()
    workflow_handle.execute_update.assert_awaited_once()


@pytest.mark.anyio
async def test_request_cancel_accepts_idle_projection_with_active_workflow() -> None:
    workspace_id = uuid.uuid4()
    session_id = uuid.uuid4()
    run_id = uuid.uuid4()
    role = _build_role(workspace_id)
    service = _build_service(role)
    agent_session = _build_agent_session(
        workspace_id=workspace_id,
        session_id=session_id,
        status=AgentSessionStatus.IDLE,
        curr_run_id=run_id,
    )
    workflow_handle = SimpleNamespace(
        describe=AsyncMock(
            return_value=SimpleNamespace(status=WorkflowExecutionStatus.RUNNING)
        ),
        execute_update=AsyncMock(),
    )
    temporal_client = SimpleNamespace(
        get_workflow_handle=MagicMock(return_value=workflow_handle),
        get_workflow_handle_for=MagicMock(return_value=workflow_handle),
    )

    with (
        patch.object(service, "get_session", AsyncMock(return_value=agent_session)),
        patch(
            "tracecat.agent.session.service.get_temporal_client",
            AsyncMock(return_value=temporal_client),
        ),
    ):
        response = await service.request_cancel(
            session_id,
            AgentSessionCancelRequest(reason="user_cancel"),
        )

    assert response.session_id == session_id
    assert response.run_id == run_id
    assert response.turn_status is AgentSessionStatus.IDLE
    workflow_handle.describe.assert_awaited_once()
    workflow_handle.execute_update.assert_awaited_once()


@pytest.mark.anyio
async def test_request_cancel_handles_finished_current_run() -> None:
    workspace_id = uuid.uuid4()
    session_id = uuid.uuid4()
    run_id = uuid.uuid4()
    role = _build_role(workspace_id)
    service = _build_service(role)
    agent_session = _build_agent_session(
        workspace_id=workspace_id,
        session_id=session_id,
        status=AgentSessionStatus.RUNNING,
        curr_run_id=run_id,
    )
    workflow_handle = SimpleNamespace(
        describe=AsyncMock(
            return_value=SimpleNamespace(status=WorkflowExecutionStatus.TERMINATED)
        ),
        execute_update=AsyncMock(),
    )
    temporal_client = SimpleNamespace(
        get_workflow_handle=MagicMock(return_value=workflow_handle),
        get_workflow_handle_for=MagicMock(return_value=workflow_handle),
    )

    with (
        patch.object(service, "get_session", AsyncMock(return_value=agent_session)),
        patch(
            "tracecat.agent.session.service.get_temporal_client",
            AsyncMock(return_value=temporal_client),
        ),
    ):
        with pytest.raises(TracecatConflictError, match="active turn"):
            await service.request_cancel(
                session_id,
                AgentSessionCancelRequest(reason="user_cancel"),
            )

    assert agent_session.curr_run_id is None
    assert agent_session.status == AgentSessionStatus.STOPPED.value
    workflow_handle.execute_update.assert_not_awaited()
