"""Non-default agent backends require ``agent_runtime_preview``.

The gate covers new sessions and every new turn of an existing session.
"""

from __future__ import annotations

import contextlib
import uuid
from collections.abc import AsyncIterator, Iterator
from unittest.mock import AsyncMock, Mock, patch

import pytest

from tracecat.agent.adapter.vercel import TextUIPart, UIMessage
from tracecat.agent.backends.access import (
    SESSION_BACKEND_NOT_ENTITLED_MESSAGE,
    can_use_backend,
)
from tracecat.agent.backends.default import DefaultBackend
from tracecat.agent.backends.types import WithdrawResult
from tracecat.agent.control.schemas import (
    AgentCancelControl,
    AgentSteerControl,
    AgentWithdrawControl,
)
from tracecat.agent.session.schemas import AgentSessionCreate
from tracecat.agent.session.service import AgentSessionService
from tracecat.agent.session.types import AgentSessionEntity, TurnLifecycle
from tracecat.agent.types import AgentConfig
from tracecat.auth.types import Role
from tracecat.chat.schemas import (
    ApprovalDecision,
    BasicChatRequest,
    ChatResponse,
    ContinueRunRequest,
    VercelChatRequest,
)
from tracecat.db.models import AgentSession
from tracecat.exceptions import EntitlementRequired, TracecatValidationError
from tracecat.tiers.enums import Entitlement

IS_ORG_ENTITLED = "tracecat.agent.backends.access.is_org_entitled"
SERVICE = "tracecat.agent.session.service"


def _service() -> tuple[AgentSessionService, AsyncMock, Role]:
    role = Role(
        type="service",
        service_id="tracecat-api",
        workspace_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
    )
    db = AsyncMock(add=Mock())
    return AgentSessionService(db, role), db, role


@pytest.mark.anyio
@pytest.mark.parametrize("backend_id", [None, "oss"])
async def test_default_backend_never_checks_entitlement(
    backend_id: str | None,
) -> None:
    with patch(IS_ORG_ENTITLED, AsyncMock(return_value=False)) as entitled:
        assert await can_use_backend(AsyncMock(), uuid.uuid4(), backend_id)
    entitled.assert_not_awaited()


@pytest.mark.anyio
@pytest.mark.parametrize("is_entitled", [False, True])
async def test_non_default_backend_follows_entitlement(is_entitled: bool) -> None:
    db = AsyncMock()
    org_id = uuid.uuid4()
    with patch(IS_ORG_ENTITLED, AsyncMock(return_value=is_entitled)) as entitled:
        assert await can_use_backend(db, org_id, "external") is is_entitled
    entitled.assert_awaited_once_with(db, org_id, Entitlement.AGENT_RUNTIME_PREVIEW)


@pytest.mark.anyio
async def test_create_rejects_non_default_backend_without_entitlement() -> None:
    service, db, role = _service()
    assert role.workspace_id is not None
    with (
        patch(IS_ORG_ENTITLED, AsyncMock(return_value=False)),
        pytest.raises(EntitlementRequired),
    ):
        await service.create_session(
            AgentSessionCreate(
                entity_type=AgentSessionEntity.WORKSPACE_CHAT,
                entity_id=role.workspace_id,
                backend_id="external",
            )
        )
    db.add.assert_not_called()
    db.commit.assert_not_awaited()


@pytest.mark.anyio
async def test_create_allows_default_backend_without_entitlement() -> None:
    service, db, role = _service()
    assert role.workspace_id is not None
    with patch(IS_ORG_ENTITLED, AsyncMock(return_value=False)):
        created = await service.create_session(
            AgentSessionCreate(
                entity_type=AgentSessionEntity.WORKSPACE_CHAT,
                entity_id=role.workspace_id,
            )
        )
    assert created.backend_id == "oss"
    db.commit.assert_awaited_once()


@pytest.mark.anyio
async def test_create_passes_gate_with_entitlement() -> None:
    """Entitled orgs reach backend resolution, which rejects uninstalled ones."""
    service, db, role = _service()
    assert role.workspace_id is not None
    with (
        patch(IS_ORG_ENTITLED, AsyncMock(return_value=True)),
        pytest.raises(TracecatValidationError, match="unavailable"),
    ):
        await service.create_session(
            AgentSessionCreate(
                entity_type=AgentSessionEntity.WORKSPACE_CHAT,
                entity_id=role.workspace_id,
                backend_id="external",
            )
        )
    db.add.assert_not_called()


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("is_entitled", "expected"),
    [(False, EntitlementRequired), (True, TracecatValidationError)],
)
async def test_fork_gates_non_default_source_backend(
    is_entitled: bool, expected: type[Exception]
) -> None:
    service, db, role = _service()
    source = AgentSession(
        id=uuid.uuid4(),
        workspace_id=role.workspace_id,
        title="Source",
        entity_type=AgentSessionEntity.WORKSPACE_CHAT.value,
        entity_id=role.workspace_id,
        backend_id="external",
        harness_type="claude_code",
    )
    result = Mock()
    result.tuples.return_value.one_or_none.return_value = (source, None)
    db.execute.return_value = result
    with (
        patch(IS_ORG_ENTITLED, AsyncMock(return_value=is_entitled)),
        pytest.raises(expected),
    ):
        await service.fork_session(source.id)
    db.add.assert_not_called()
    db.commit.assert_not_awaited()


# Turns on existing sessions. Every surface (Workspace Chat, agent presets,
# cases, external channels, case-comment invocations, MCP) starts a turn
# through ``run_turn`` or ``prepare_new_turn``; both pass through
# ``validate_turn_request`` before writing any turn state.

TURN_SURFACES = [
    AgentSessionEntity.WORKSPACE_CHAT,
    AgentSessionEntity.AGENT_PRESET,
    AgentSessionEntity.CASE,
    AgentSessionEntity.EXTERNAL_CHANNEL,
]


def _existing_session(
    role: Role,
    *,
    entity_type: AgentSessionEntity = AgentSessionEntity.WORKSPACE_CHAT,
    backend_id: str = "external",
    curr_run_id: uuid.UUID | None = None,
) -> AgentSession:
    return AgentSession(
        id=uuid.uuid4(),
        workspace_id=role.workspace_id,
        title="Existing",
        entity_type=entity_type.value,
        entity_id=uuid.uuid4(),
        backend_id=backend_id,
        harness_type="claude_code",
        curr_run_id=curr_run_id,
    )


def _provider() -> Mock:
    provider = Mock(spec=DefaultBackend)
    provider.caller_owned_turns = True
    provider.steering = True
    provider.withdrawing = True
    provider.start_turn = AsyncMock()
    provider.get_turn_lifecycle = AsyncMock(return_value=TurnLifecycle.RUNNING)
    provider.steer = AsyncMock(return_value=True)
    provider.withdraw = AsyncMock(return_value=WithdrawResult())
    provider.cancel = AsyncMock()
    return provider


@contextlib.contextmanager
def _existing_turn(
    service: AgentSessionService,
    agent_session: AgentSession,
    provider: Mock,
    *,
    is_entitled: bool,
) -> Iterator[AsyncMock]:
    """Route the service to ``agent_session`` and ``provider``.

    Yields the entitlement lookup so tests can assert whether it ran.
    """
    with (
        patch(IS_ORG_ENTITLED, AsyncMock(return_value=is_entitled)) as entitled,
        patch.object(service, "get_session", AsyncMock(return_value=agent_session)),
        patch.object(service, "has_pending_approvals", AsyncMock(return_value=False)),
        patch.object(service, "require_entitlement", AsyncMock(return_value=None)),
        patch(f"{SERVICE}.get_agent_backend", return_value=provider),
        patch(f"{SERVICE}.find_agent_backend", return_value=provider),
    ):
        yield entitled


def _approval() -> ContinueRunRequest:
    return ContinueRunRequest(
        decisions=[ApprovalDecision(tool_call_id="call_1", action="approve")],
        source="inbox",
    )


@pytest.mark.anyio
@pytest.mark.parametrize("entity_type", TURN_SURFACES)
async def test_revoked_backend_refuses_a_new_turn_before_dispatch(
    entity_type: AgentSessionEntity,
) -> None:
    service, db, role = _service()
    agent_session = _existing_session(role, entity_type=entity_type)
    provider = _provider()
    with (
        _existing_turn(service, agent_session, provider, is_entitled=False),
        patch.object(service, "_build_agent_config") as build_config,
        pytest.raises(EntitlementRequired) as exc_info,
    ):
        await service.run_turn(agent_session.id, BasicChatRequest(message="Hi"))

    assert str(exc_info.value) == SESSION_BACKEND_NOT_ENTITLED_MESSAGE
    assert exc_info.value.detail == {
        "entitlement": Entitlement.AGENT_RUNTIME_PREVIEW.value
    }
    # Refused before any turn state: no config, no dispatch, no writes.
    build_config.assert_not_called()
    provider.start_turn.assert_not_awaited()
    db.add.assert_not_called()
    db.commit.assert_not_awaited()
    assert agent_session.curr_run_id is None


@pytest.mark.anyio
async def test_revoked_backend_refuses_a_vercel_chat_message() -> None:
    service, _, role = _service()
    agent_session = _existing_session(role)
    request = VercelChatRequest(
        message=UIMessage(
            id="msg-1", role="user", parts=[TextUIPart(type="text", text="Hi")]
        )
    )
    with (
        _existing_turn(service, agent_session, _provider(), is_entitled=False),
        pytest.raises(EntitlementRequired),
    ):
        await service.validate_turn_request(agent_session.id, request)


@pytest.mark.anyio
async def test_entitled_backend_admits_a_new_turn() -> None:
    service, _, role = _service()
    agent_session = _existing_session(role)
    with _existing_turn(
        service, agent_session, _provider(), is_entitled=True
    ) as entitled:
        admitted = await service.validate_turn_request(
            agent_session.id, BasicChatRequest(message="Hi")
        )
    assert admitted is agent_session
    entitled.assert_awaited_once()


@pytest.mark.anyio
async def test_default_backend_turns_never_check_the_entitlement() -> None:
    service, _, role = _service()
    agent_session = _existing_session(role, backend_id="oss")
    with _existing_turn(
        service, agent_session, _provider(), is_entitled=False
    ) as entitled:
        await service.validate_turn_request(
            agent_session.id, BasicChatRequest(message="Hi")
        )
    entitled.assert_not_awaited()


@pytest.mark.anyio
async def test_revoked_backend_refuses_a_caller_owned_turn() -> None:
    """Case-comment invocations prepare turns for their own workflow."""
    service, db, role = _service()
    agent_session = _existing_session(role, entity_type=AgentSessionEntity.CASE)
    with (
        _existing_turn(service, agent_session, _provider(), is_entitled=False),
        patch.object(service, "_build_agent_config") as build_config,
        pytest.raises(EntitlementRequired),
    ):
        await service.prepare_new_turn(agent_session.id, "Investigate")
    build_config.assert_not_called()
    db.execute.assert_not_awaited()
    db.commit.assert_not_awaited()
    assert agent_session.curr_run_id is None


@pytest.mark.anyio
async def test_revoked_backend_lets_a_retry_adopt_its_prepared_turn() -> None:
    """A caller-owned retry after its preparation committed finishes that turn.

    Refusing it would leave the committed run pointers with no workflow to
    release them, and the session would refuse every later turn as busy.
    """
    service, db, role = _service()
    run_id = uuid.uuid4()
    agent_session = _existing_session(
        role, entity_type=AgentSessionEntity.CASE, curr_run_id=run_id
    )
    config = AgentConfig(model_name="test", model_provider="anthropic")

    @contextlib.asynccontextmanager
    async def build_config(_: AgentSession) -> AsyncIterator[AgentConfig]:
        yield config

    with (
        _existing_turn(
            service, agent_session, _provider(), is_entitled=False
        ) as entitled,
        patch.object(service, "_build_agent_config", build_config),
    ):
        prepared = await service.prepare_new_turn(agent_session.id, "Investigate")

    assert prepared.run_id == run_id
    entitled.assert_not_awaited()
    db.commit.assert_awaited_once()


@pytest.mark.anyio
async def test_revoked_backend_refuses_to_adopt_a_direct_dispatch() -> None:
    """A direct dispatch keeps its reservation marker; adopting it is a new turn."""
    service, db, role = _service()
    run_id = uuid.uuid4()
    agent_session = _existing_session(
        role, entity_type=AgentSessionEntity.CASE, curr_run_id=run_id
    )
    agent_session.reserved_run_id = run_id
    with (
        _existing_turn(service, agent_session, _provider(), is_entitled=False),
        patch.object(service, "_build_agent_config") as build_config,
        pytest.raises(EntitlementRequired),
    ):
        await service.prepare_new_turn(agent_session.id, "Investigate")
    build_config.assert_not_called()
    db.commit.assert_not_awaited()
    assert agent_session.reserved_run_id == run_id


@pytest.mark.anyio
async def test_revoked_backend_still_accepts_approvals_for_a_parked_turn() -> None:
    """Approval decisions finish a turn that started while entitled."""
    service, _, role = _service()
    agent_session = _existing_session(role, curr_run_id=uuid.uuid4())
    response = ChatResponse(
        stream_url="/stream", chat_id=agent_session.id, curr_run_id=uuid.uuid4()
    )
    request = _approval()
    with (
        _existing_turn(
            service, agent_session, _provider(), is_entitled=False
        ) as entitled,
        patch.object(
            service, "_continue_with_approvals", AsyncMock(return_value=response)
        ) as continue_with_approvals,
    ):
        assert await service.run_turn(agent_session.id, request) is response
    continue_with_approvals.assert_awaited_once_with(agent_session.id, request)
    entitled.assert_not_awaited()


@pytest.mark.anyio
async def test_revoking_mid_turn_leaves_the_running_turn_alone() -> None:
    """A running turn keeps streaming and stays cancellable after a revoke.

    New user input is refused: a steer could be queued for a later turn.
    """
    service, _, role = _service()
    run_id = uuid.uuid4()
    agent_session = _existing_session(role, curr_run_id=run_id)
    provider = _provider()
    with _existing_turn(service, agent_session, provider, is_entitled=False):
        lifecycle = await service.get_turn_lifecycle(agent_session)
        withdrawn = await service.control(
            agent_session.id,
            AgentWithdrawControl(type="withdraw", id=uuid.uuid4()),
        )
        cancelled = await service.control(
            agent_session.id, AgentCancelControl(type="cancel", id=uuid.uuid4())
        )
        with pytest.raises(EntitlementRequired):
            await service.control(
                agent_session.id,
                AgentSteerControl(type="steer", id=uuid.uuid4(), text="More"),
            )

    assert lifecycle.lifecycle is TurnLifecycle.RUNNING
    assert withdrawn.status == "withdrawn"
    assert cancelled.status == "accepted"
    provider.withdraw.assert_awaited_once()
    provider.cancel.assert_awaited_once_with(run_id)
    provider.steer.assert_not_awaited()


@pytest.mark.anyio
async def test_entitled_backend_accepts_a_steer() -> None:
    service, _, role = _service()
    agent_session = _existing_session(role, curr_run_id=uuid.uuid4())
    provider = _provider()
    with _existing_turn(service, agent_session, provider, is_entitled=True):
        receipt = await service.control(
            agent_session.id,
            AgentSteerControl(type="steer", id=uuid.uuid4(), text="More"),
        )
    assert receipt.status == "accepted"
    provider.steer.assert_awaited_once()
