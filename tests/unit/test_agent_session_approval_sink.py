from __future__ import annotations

import contextlib
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.agent.approvals.enums import ApprovalStatus
from tracecat.agent.session.service import AgentSessionService
from tracecat.agent.session.types import AgentSessionEntity
from tracecat.agent.types import AgentConfig
from tracecat.auth.types import Role
from tracecat.chat.schemas import ApprovalDecision, BasicChatRequest, ContinueRunRequest
from tracecat.db.models import AgentSession, Approval

pytestmark = pytest.mark.usefixtures("db")


@pytest.fixture
async def external_agent_session(session: AsyncSession, svc_role: Role) -> AgentSession:
    agent_session = AgentSession(
        id=uuid.uuid4(),
        title="Slack thread",
        workspace_id=svc_role.workspace_id,
        entity_type=AgentSessionEntity.EXTERNAL_CHANNEL.value,
        entity_id=uuid.uuid4(),
        channel_context={
            "channel_type": "slack",
            "team_id": "T123",
            "channel_id": "C123",
            "thread_ts": "1700000000.100",
            "user_id": "U123",
            "event_ts": "1700000000.100",
            "bot_user_id": "B123",
        },
    )
    session.add(agent_session)
    await session.commit()
    await session.refresh(agent_session)
    return agent_session


@pytest.mark.anyio
async def test_claim_external_channel_approval_sink_noops_without_locking(
    session: AsyncSession,
    svc_role: Role,
    external_agent_session: AgentSession,
) -> None:
    service = AgentSessionService(session=session, role=svc_role)

    claimed = await service.claim_external_channel_approval_sink(
        session_id=external_agent_session.id,
        source="slack",
    )

    assert claimed == "slack"

    refreshed = await service.get_session(external_agent_session.id)
    assert refreshed is not None
    assert isinstance(refreshed.channel_context, dict)
    assert "active_sink" not in refreshed.channel_context


@pytest.mark.anyio
async def test_claim_external_channel_approval_sink_allows_source_switch(
    session: AsyncSession,
    svc_role: Role,
    external_agent_session: AgentSession,
) -> None:
    service = AgentSessionService(session=session, role=svc_role)

    await service.claim_external_channel_approval_sink(
        session_id=external_agent_session.id,
        source="slack",
    )

    switched = await service.claim_external_channel_approval_sink(
        session_id=external_agent_session.id,
        source="inbox",
    )

    assert switched == "inbox"


@pytest.mark.anyio
async def test_claim_external_channel_approval_sink_is_idempotent(
    session: AsyncSession,
    svc_role: Role,
    external_agent_session: AgentSession,
) -> None:
    service = AgentSessionService(session=session, role=svc_role)

    first = await service.claim_external_channel_approval_sink(
        session_id=external_agent_session.id,
        source="inbox",
    )
    second = await service.claim_external_channel_approval_sink(
        session_id=external_agent_session.id,
        source="inbox",
    )

    assert first == "inbox"
    assert second == "inbox"


@pytest.mark.anyio
async def test_has_pending_approvals_reflects_status(
    session: AsyncSession,
    svc_role: Role,
    external_agent_session: AgentSession,
) -> None:
    service = AgentSessionService(session=session, role=svc_role)

    assert await service.has_pending_approvals(external_agent_session.id) is False

    approval = Approval(
        workspace_id=svc_role.workspace_id,
        session_id=external_agent_session.id,
        tool_call_id="call_123",
        tool_name="core.http_request",
        tool_call_args={"url": "https://example.com"},
        status=ApprovalStatus.PENDING,
    )
    session.add(approval)
    await session.commit()

    assert await service.has_pending_approvals(external_agent_session.id) is True

    approval.status = ApprovalStatus.APPROVED
    session.add(approval)
    await session.commit()

    assert await service.has_pending_approvals(external_agent_session.id) is False


@pytest.mark.anyio
async def test_run_turn_continue_with_inbox_source_for_non_external_session(
    session: AsyncSession,
    svc_role: Role,
) -> None:
    agent_session = AgentSession(
        id=uuid.uuid4(),
        title="Preset chat",
        workspace_id=svc_role.workspace_id,
        entity_type=AgentSessionEntity.AGENT_PRESET.value,
        entity_id=uuid.uuid4(),
        curr_run_id=uuid.uuid4(),
    )
    session.add(agent_session)
    session.add(
        Approval(
            workspace_id=svc_role.workspace_id,
            session_id=agent_session.id,
            tool_call_id="tool_call_123",
            tool_name="core.http_request",
            tool_call_args={"url": "https://example.com"},
            status=ApprovalStatus.PENDING,
        )
    )
    await session.commit()
    await session.refresh(agent_session)

    service = AgentSessionService(session=session, role=svc_role)
    continuation = ContinueRunRequest(
        decisions=[
            ApprovalDecision(
                tool_call_id="tool_call_123",
                action="approve",
            )
        ],
        source="inbox",
    )

    fake_handle = SimpleNamespace(execute_update=AsyncMock(return_value=None))
    get_workflow_handle_for = Mock(return_value=fake_handle)
    fake_client = SimpleNamespace(get_workflow_handle_for=get_workflow_handle_for)
    fake_redis = SimpleNamespace(
        xadd=AsyncMock(return_value="1-0"),
    )
    with (
        patch(
            "tracecat.agent.session.service.get_temporal_client",
            AsyncMock(return_value=fake_client),
        ),
        patch(
            "tracecat.agent.session.service.get_redis_client",
            AsyncMock(return_value=fake_redis),
        ),
    ):
        result = await service.run_turn(agent_session.id, continuation)

    assert result is None
    get_workflow_handle_for.assert_called_once()
    fake_handle.execute_update.assert_awaited_once()


@pytest.mark.anyio
async def test_run_turn_continue_without_pending_approvals_is_noop(
    session: AsyncSession,
    svc_role: Role,
) -> None:
    agent_session = AgentSession(
        id=uuid.uuid4(),
        title="Preset chat",
        workspace_id=svc_role.workspace_id,
        entity_type=AgentSessionEntity.AGENT_PRESET.value,
        entity_id=uuid.uuid4(),
        curr_run_id=uuid.uuid4(),
    )
    session.add(agent_session)
    await session.commit()
    await session.refresh(agent_session)

    service = AgentSessionService(session=session, role=svc_role)
    continuation = ContinueRunRequest(
        decisions=[
            ApprovalDecision(
                tool_call_id="tool_call_123",
                action="approve",
            )
        ],
        source="inbox",
    )

    fake_handle = SimpleNamespace(execute_update=AsyncMock(return_value=None))
    get_workflow_handle_for = Mock(return_value=fake_handle)
    fake_client = SimpleNamespace(get_workflow_handle_for=get_workflow_handle_for)
    with patch(
        "tracecat.agent.session.service.get_temporal_client",
        AsyncMock(return_value=fake_client),
    ):
        result = await service.run_turn(agent_session.id, continuation)

    assert result is None
    get_workflow_handle_for.assert_not_called()
    fake_handle.execute_update.assert_not_awaited()


@pytest.mark.anyio
async def test_run_turn_continue_duplicate_submission_is_noop(
    session: AsyncSession,
    svc_role: Role,
) -> None:
    agent_session = AgentSession(
        id=uuid.uuid4(),
        title="Preset chat",
        workspace_id=svc_role.workspace_id,
        entity_type=AgentSessionEntity.AGENT_PRESET.value,
        entity_id=uuid.uuid4(),
        curr_run_id=uuid.uuid4(),
    )
    session.add(agent_session)
    session.add(
        Approval(
            workspace_id=svc_role.workspace_id,
            session_id=agent_session.id,
            tool_call_id="tool_call_123",
            tool_name="core.http_request",
            tool_call_args={"url": "https://example.com"},
            status=ApprovalStatus.PENDING,
        )
    )
    await session.commit()
    await session.refresh(agent_session)

    service = AgentSessionService(session=session, role=svc_role)
    continuation = ContinueRunRequest(
        decisions=[
            ApprovalDecision(
                tool_call_id="tool_call_123",
                action="approve",
            )
        ],
        source="inbox",
    )

    fake_redis = SimpleNamespace(
        set_if_not_exists=AsyncMock(return_value=False),
        xadd=AsyncMock(return_value="1-0"),
        delete=AsyncMock(return_value=1),
    )
    fake_handle = SimpleNamespace(execute_update=AsyncMock(return_value=None))
    get_workflow_handle_for = Mock(return_value=fake_handle)
    fake_client = SimpleNamespace(get_workflow_handle_for=get_workflow_handle_for)
    with (
        patch(
            "tracecat.agent.session.service.get_temporal_client",
            AsyncMock(return_value=fake_client),
        ),
        patch(
            "tracecat.agent.session.service.get_redis_client",
            AsyncMock(return_value=fake_redis),
        ),
    ):
        result = await service.run_turn(agent_session.id, continuation)

    assert result is None
    fake_redis.set_if_not_exists.assert_awaited_once()
    fake_handle.execute_update.assert_not_awaited()


@pytest.mark.anyio
async def test_run_turn_merges_basic_chat_request_instructions(
    session: AsyncSession,
    svc_role: Role,
    external_agent_session: AgentSession,
) -> None:
    service = AgentSessionService(session=session, role=svc_role)
    fake_client = SimpleNamespace(start_workflow=AsyncMock(return_value=None))

    @contextlib.asynccontextmanager
    async def _fake_build_agent_config(_session: AgentSession):
        yield AgentConfig(
            instructions="Base preset instructions",
            model_name="gpt-4o-mini",
            model_provider="openai",
            actions=[],
        )

    with (
        patch.object(service, "_build_agent_config", _fake_build_agent_config),
        patch.object(
            service,
            "has_pending_approvals",
            AsyncMock(return_value=False),
        ),
        patch.object(
            service,
            "auto_title_session_on_first_prompt",
            AsyncMock(return_value=None),
        ),
        patch(
            "tracecat.agent.session.service.get_temporal_client",
            AsyncMock(return_value=fake_client),
        ),
    ):
        response = await service.run_turn(
            external_agent_session.id,
            BasicChatRequest(
                message="summarize this thread",
                instructions="Slack actor context for this turn:\n- Slack email: jordan@example.com",
            ),
        )

    assert response is not None
    await_args = fake_client.start_workflow.await_args
    assert await_args is not None
    workflow_args = await_args.args[1]
    assert (
        workflow_args.agent_args.config.instructions
        == "Base preset instructions\n\nSlack actor context for this turn:\n- Slack email: jordan@example.com"
    )
    assert workflow_args.agent_args.user_prompt == "summarize this thread"
