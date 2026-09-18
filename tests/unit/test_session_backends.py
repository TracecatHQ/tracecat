"""Backend registration and service delegation without a private implementation."""

import contextlib
from collections.abc import Iterator
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from uuid import uuid4

import pytest
from temporalio.client import WorkflowExecutionStatus
from temporalio.common import TypedSearchAttributes

from tracecat.agent.session.backends import registry
from tracecat.agent.session.backends.claude import ClaudeSessionBackend
from tracecat.agent.session.backends.types import (
    SessionDispatchUncertain,
    SessionTurnContext,
)
from tracecat.agent.session.schemas import AgentSessionCreate, AgentSessionUpdate
from tracecat.agent.session.service import AgentSessionService
from tracecat.agent.session.types import AgentSessionEntity, TurnLifecycle
from tracecat.agent.types import AgentConfig
from tracecat.auth.types import Role
from tracecat.chat.schemas import BasicChatRequest
from tracecat.db.models import AgentSession
from tracecat.exceptions import TracecatValidationError


@pytest.fixture(autouse=True)
def clean_backend_registry() -> Iterator[None]:
    registry.get_session_backends.cache_clear()
    yield
    registry.get_session_backends.cache_clear()


def entry(name: str, factory: object) -> SimpleNamespace:
    return SimpleNamespace(name=name, load=Mock(return_value=factory))


def test_discovery_loads_factories_once_and_rejects_unknown_backends():
    provider = ClaudeSessionBackend()
    factory = Mock(return_value=provider)
    with patch.object(
        registry, "entry_points", return_value=[entry("external", factory)]
    ):
        assert registry.get_session_backend("external") is provider
        assert registry.get_session_backend("external") is provider
        assert isinstance(registry.get_session_backend(None), ClaudeSessionBackend)
        factory.assert_called_once_with()
        with pytest.raises(TracecatValidationError, match="unavailable"):
            registry.get_session_backend("missing")


@pytest.mark.parametrize(
    "entries, error",
    [
        ([entry("claude_code", ClaudeSessionBackend)], ValueError),
        (
            [
                entry("external", ClaudeSessionBackend),
                entry("external", ClaudeSessionBackend),
            ],
            ValueError,
        ),
        ([entry("invalid.name", ClaudeSessionBackend)], ValueError),
        ([entry("x" * 51, ClaudeSessionBackend)], ValueError),
        ([entry("external", object())], TypeError),
        ([entry("external", lambda: object())], TypeError),
    ],
)
def test_invalid_plugins_fail_closed(entries, error):
    with (
        patch.object(registry, "entry_points", return_value=entries),
        pytest.raises(error),
    ):
        registry.get_session_backends()


def test_disabled_plugins_cannot_be_selected():
    provider = ClaudeSessionBackend()
    with (
        patch.object(provider, "is_enabled", return_value=False),
        patch.object(
            registry, "entry_points", return_value=[entry("external", lambda: provider)]
        ),
        pytest.raises(TracecatValidationError, match="unavailable"),
    ):
        registry.get_session_backend("external")


def context() -> SessionTurnContext:
    role = Role(
        type="service",
        service_id="tracecat-api",
        organization_id=uuid4(),
        workspace_id=uuid4(),
    )
    session = AgentSession(
        id=uuid4(),
        workspace_id=role.workspace_id,
        title="Test",
        entity_type=AgentSessionEntity.WORKSPACE_CHAT.value,
        entity_id=role.workspace_id,
        harness_type="external",
    )
    return SessionTurnContext(
        db=AsyncMock(),
        session=session,
        role=role,
        config=AgentConfig(model_name="test", model_provider="anthropic"),
        prompt="Hello",
        run_id=uuid4(),
        stream_id=uuid4(),
        search_attributes=TypedSearchAttributes.empty,
    )


@pytest.mark.anyio
async def test_builtin_dispatch_preserves_workflow_contract():
    ctx = context()
    assert isinstance(ctx.db, AsyncMock)
    client = AsyncMock()
    with patch(
        "tracecat.agent.session.backends.claude.get_temporal_client",
        return_value=client,
    ):
        await ClaudeSessionBackend().start_turn(ctx)
    assert ctx.session.curr_run_id == ctx.run_id
    assert ctx.session.active_stream_id == ctx.stream_id
    ctx.db.commit.assert_awaited_once()
    call = client.start_workflow.await_args
    assert call.args[0] == "DurableAgentWorkflow"
    assert call.args[1].agent_args.active_stream_id == ctx.stream_id
    assert call.kwargs["id"] == f"agent/{ctx.run_id}"
    assert call.kwargs["search_attributes"] == ctx.search_attributes


@pytest.mark.anyio
async def test_service_dispatches_resolved_config_and_propagates_uncertainty():
    ctx = context()
    assert isinstance(ctx.db, AsyncMock)
    service = AgentSessionService(ctx.db, ctx.role)
    provider = Mock(spec=ClaudeSessionBackend)
    provider.start_turn = AsyncMock(side_effect=SessionDispatchUncertain("uncertain"))

    @contextlib.asynccontextmanager
    async def configuration(_):
        yield ctx.config

    with (
        patch.object(service, "validate_turn_request", return_value=ctx.session),
        patch.object(service, "_build_agent_config", configuration),
        patch(
            "tracecat.agent.session.service.get_session_backend", return_value=provider
        ) as resolve,
        pytest.raises(SessionDispatchUncertain),
    ):
        await service.run_turn(
            ctx.session.id,
            BasicChatRequest(message="Hello"),
            active_stream_id=ctx.stream_id,
            is_first_prompt=False,
        )
    resolve.assert_called_once_with("external")
    submitted = provider.start_turn.await_args.args[0]
    assert submitted.prompt == "Hello"
    assert submitted.stream_id == ctx.stream_id
    assert submitted.config is ctx.config
    assert submitted.role.workspace_id == ctx.role.workspace_id
    # Reservation belongs to dispatch; the service must not clear an uncertain turn.
    ctx.db.commit.assert_not_awaited()


@pytest.mark.anyio
async def test_session_creation_and_backend_changes_fail_before_database_writes():
    ctx = context()
    assert isinstance(ctx.db, AsyncMock)
    service = AgentSessionService(ctx.db, ctx.role)
    with pytest.raises(TracecatValidationError):
        await service.create_session(
            AgentSessionCreate(
                entity_type=AgentSessionEntity.WORKSPACE_CHAT,
                entity_id=uuid4(),
                harness_type="missing",
            )
        )
    with pytest.raises(TracecatValidationError):
        await service.update_session(
            ctx.session, params=AgentSessionUpdate(harness_type="claude_code")
        )
    with pytest.raises(TracecatValidationError):
        await service.update_session(
            ctx.session, params=AgentSessionUpdate(harness_type=None)
        )
    ctx.db.commit.assert_not_awaited()


@pytest.mark.anyio
async def test_lifecycle_and_cancel_use_selected_backend():
    ctx = context()
    assert isinstance(ctx.db, AsyncMock)
    ctx.session.curr_run_id = ctx.run_id
    service = AgentSessionService(ctx.db, ctx.role)
    provider = Mock(spec=ClaudeSessionBackend)
    provider.workflow_id.return_value = f"external/{ctx.run_id}"
    provider.cancel = AsyncMock()
    handle = SimpleNamespace(
        describe=AsyncMock(
            return_value=SimpleNamespace(status=WorkflowExecutionStatus.RUNNING)
        )
    )
    client = SimpleNamespace(get_workflow_handle=Mock(return_value=handle))
    with (
        patch.object(service, "get_session", return_value=ctx.session),
        patch.object(service, "require_entitlement", return_value=None),
        patch(
            "tracecat.agent.session.service.get_session_backend", return_value=provider
        ),
        patch(
            "tracecat.agent.session.service.get_temporal_client", return_value=client
        ),
    ):
        result = await service.get_turn_lifecycle(ctx.session)
        assert result.lifecycle == TurnLifecycle.RUNNING
        await service.request_cancel(ctx.session.id)
    client.get_workflow_handle.assert_called_with(f"external/{ctx.run_id}")
    provider.cancel.assert_awaited_once_with(client, ctx.run_id)


@pytest.mark.anyio
async def test_backend_capabilities_guard_fork_and_caller_owned_dispatch():
    ctx = context()
    assert isinstance(ctx.db, AsyncMock)
    service = AgentSessionService(ctx.db, ctx.role)
    provider = Mock(spec=ClaudeSessionBackend)
    provider.supports_fork = False
    provider.supports_caller_owned_workflows = False
    with (
        patch.object(service, "get_session", return_value=ctx.session),
        patch.object(service, "validate_turn_request", return_value=ctx.session),
        patch(
            "tracecat.agent.session.service.get_session_backend", return_value=provider
        ),
    ):
        with pytest.raises(TracecatValidationError, match="fork"):
            await service.fork_session(ctx.session.id)
        with pytest.raises(TracecatValidationError, match="caller-owned"):
            await service.prepare_new_turn(ctx.session.id, "Hello")
    ctx.db.commit.assert_not_awaited()
