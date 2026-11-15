"""send_message holds no database connection while it waits on Temporal."""

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from temporalio.client import WorkflowExecutionStatus
from temporalio.service import RPCError, RPCStatusCode

from tracecat.agent.session.results import RunBusy
from tracecat.agent.session.service import AgentSessionService
from tracecat.agent.session.types import AgentSessionEntity
from tracecat.auth.types import Role
from tracecat.db.models import AgentSession

pytestmark = [pytest.mark.anyio, pytest.mark.usefixtures("db")]


async def test_send_message_releases_the_session_before_each_describe(
    session: AsyncSession, svc_role: Role
) -> None:
    assert svc_role.workspace_id is not None
    running_run = uuid.uuid4()
    agent_session = AgentSession(
        workspace_id=svc_role.workspace_id,
        title="Test chat",
        entity_type=AgentSessionEntity.WORKSPACE_CHAT.value,
        entity_id=svc_role.workspace_id,
        backend_id="oss",
        harness_type="claude_code",
        curr_run_id=running_run,
    )
    session.add(agent_session)
    await session.commit()

    described: list[str] = []

    def handle_for(_run: object, workflow_id: str) -> Mock:
        async def describe(**_kwargs: object) -> SimpleNamespace:
            assert not session.in_transaction(), f"connection held for {workflow_id}"
            described.append(workflow_id)
            if workflow_id == f"agent/{running_run}":
                return SimpleNamespace(
                    status=WorkflowExecutionStatus.RUNNING,
                    workflow_type="DurableAgentWorkflow",
                )
            raise RPCError("not found", RPCStatusCode.NOT_FOUND, b"")

        return Mock(describe=describe)

    client = Mock(get_workflow_handle_for=Mock(side_effect=handle_for))
    with (
        patch("tracecat.agent.backends.base.get_temporal_client", return_value=client),
        patch(
            "tracecat.agent.session.service.require_session_backend_entitlement",
            AsyncMock(),
        ),
    ):
        result = await AgentSessionService(session, svc_role).send_message(
            agent_session.id, text="Investigate this alert", request_id="slack:Ev1"
        )

    assert result == RunBusy(run_id=running_run)
    # Start: message run, then the holder; steer: message run, then the holder.
    assert described.count(f"agent/{running_run}") == 2
    assert len(described) == 4
