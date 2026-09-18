"""Backend registration and service delegation without a private implementation."""

import contextlib
from collections.abc import Iterator
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from uuid import uuid4

import pytest
from temporalio.client import WorkflowExecutionStatus
from temporalio.common import (
    Priority,
    SearchAttributeKey,
    SearchAttributePair,
    TypedSearchAttributes,
)
from temporalio.exceptions import ApplicationError
from tracecat_ee.agent.approvals.service import ApprovalService
from tracecat_ee.agent.types import AgentWorkflowID
from tracecat_ee.inbox.providers.agent_runs import AgentRunsInboxProvider

from tracecat.agent.session.activities import (
    CreateSessionInput,
    create_session_activity,
)
from tracecat.agent.session.backends import registry
from tracecat.agent.session.backends.durable import DurableSessionBackend
from tracecat.agent.session.backends.types import (
    SessionDispatchUncertain,
    SessionTurnContext,
)
from tracecat.agent.session.schemas import AgentSessionCreate, AgentSessionUpdate
from tracecat.agent.session.service import (
    AgentSessionService,
    ApprovalContinuationAttempt,
)
from tracecat.agent.session.types import AgentSessionEntity, TurnLifecycle
from tracecat.agent.types import AgentConfig
from tracecat.agent.workflow_id import agent_workflow_id
from tracecat.auth.types import Role
from tracecat.chat.schemas import ApprovalDecision, BasicChatRequest, ContinueRunRequest
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
    provider = DurableSessionBackend()
    factory = Mock(return_value=provider)
    with patch.object(
        registry, "entry_points", return_value=[entry("external", factory)]
    ):
        assert registry.get_session_backend("external") is provider
        assert registry.get_session_backend("external") is provider
        assert isinstance(registry.get_session_backend(None), DurableSessionBackend)
        factory.assert_called_once_with()
        with pytest.raises(TracecatValidationError, match="unavailable"):
            registry.get_session_backend("missing")


@pytest.mark.parametrize(
    "entries, error",
    [
        ([entry("v1", DurableSessionBackend)], ValueError),
        (
            [
                entry("external", DurableSessionBackend),
                entry("external", DurableSessionBackend),
            ],
            ValueError,
        ),
        ([entry("invalid.name", DurableSessionBackend)], ValueError),
        ([entry("x" * 51, DurableSessionBackend)], ValueError),
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
    provider = DurableSessionBackend()
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
        backend_id="external",
        harness_type="claude_code",
    )
    return SessionTurnContext(
        db=AsyncMock(add=Mock()),
        session=session,
        role=role,
        config=AgentConfig(model_name="test", model_provider="anthropic"),
        prompt="Hello",
        run_id=uuid4(),
        stream_id=uuid4(),
        search_attributes=TypedSearchAttributes(
            [
                SearchAttributePair(
                    SearchAttributeKey.for_keyword("WorkspaceId"),
                    str(role.workspace_id),
                ),
                SearchAttributePair(
                    SearchAttributeKey.for_keyword("CorrelationId"), str(session.id)
                ),
            ]
        ),
    )


@pytest.mark.anyio
async def test_builtin_dispatch_preserves_workflow_contract():
    ctx = context()
    assert isinstance(ctx.db, AsyncMock)
    client = AsyncMock()
    with patch(
        "tracecat.agent.session.backends.durable.get_temporal_client",
        return_value=client,
    ):
        await DurableSessionBackend().start_turn(ctx)
    assert ctx.session.curr_run_id == ctx.run_id
    assert ctx.session.active_stream_id == ctx.stream_id
    ctx.db.commit.assert_awaited_once()
    call = client.start_workflow.await_args
    assert call.args[0] == "DurableAgentWorkflow"
    assert call.args[1].harness_type == "claude_code"
    assert call.args[1].agent_args.active_stream_id == ctx.stream_id
    assert call.kwargs["id"] == f"agent/{ctx.run_id}"
    assert call.kwargs["search_attributes"] == ctx.search_attributes
    assert call.kwargs["priority"] == Priority(priority_key=1)
    assert call.kwargs["retry_policy"].maximum_attempts == 1


@pytest.mark.anyio
async def test_service_dispatches_resolved_config_and_propagates_uncertainty():
    ctx = context()
    assert isinstance(ctx.db, AsyncMock)
    service = AgentSessionService(ctx.db, ctx.role)
    provider = Mock(spec=DurableSessionBackend)
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
    resolve.assert_called_once_with("external", harness_type="claude_code")
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
                backend_id="missing",
            )
        )
    with pytest.raises(TracecatValidationError):
        await service.update_session(
            ctx.session, params=AgentSessionUpdate(backend_id="v1")
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
    provider = Mock(spec=DurableSessionBackend)
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
            "tracecat.agent.session.service.find_session_backend", return_value=provider
        ),
        patch(
            "tracecat.agent.session.service.get_temporal_client", return_value=client
        ),
    ):
        result = await service.get_turn_lifecycle(ctx.session)
        assert result.lifecycle == TurnLifecycle.RUNNING
        await service.request_cancel(ctx.session.id)
    client.get_workflow_handle.assert_called_with(f"agent/{ctx.run_id}")
    provider.cancel.assert_awaited_once_with(client, ctx.run_id)


@pytest.mark.anyio
async def test_backend_capabilities_guard_fork_and_caller_owned_dispatch():
    ctx = context()
    assert isinstance(ctx.db, AsyncMock)
    service = AgentSessionService(ctx.db, ctx.role)
    provider = Mock(spec=DurableSessionBackend)
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


@pytest.mark.anyio
async def test_backend_and_harness_are_independent_and_defaults_resolve_internally():
    ctx = context()
    service = AgentSessionService(ctx.db, ctx.role)
    provider = DurableSessionBackend()
    provider.default_harness = "custom_harness"
    provider.supported_harnesses = frozenset({"custom_harness", "another_harness"})
    with patch.object(
        registry, "entry_points", return_value=[entry("v2", lambda: provider)]
    ):
        created = await service.create_session(
            AgentSessionCreate(
                backend_id="v2",
                entity_type=AgentSessionEntity.WORKSPACE_CHAT,
                entity_id=uuid4(),
            )
        )
        assert created.backend_id == "v2"
        assert created.harness_type == "custom_harness"
        assert (
            registry.get_session_backend("v2", harness_type="another_harness")
            is provider
        )
        with pytest.raises(TracecatValidationError, match="unsupported"):
            registry.get_session_backend("v1", harness_type="custom_harness")
        with pytest.raises(TracecatValidationError):
            await service.update_session(
                created, params=AgentSessionUpdate(backend_id=None)
            )
        with pytest.raises(TracecatValidationError):
            await service.update_session(
                created, params=AgentSessionUpdate(harness_type="another_harness")
            )


@pytest.mark.anyio
async def test_builtin_lost_ack_retains_ownership_without_raw_exception_context():
    ctx = context()
    client = AsyncMock()
    client.start_workflow.side_effect = TimeoutError("private SDK details")
    with patch(
        "tracecat.agent.session.backends.durable.get_temporal_client",
        return_value=client,
    ):
        with pytest.raises(SessionDispatchUncertain) as caught:
            await DurableSessionBackend().start_turn(ctx)
    assert caught.value.__context__ is None
    assert ctx.session.curr_run_id == ctx.run_id
    assert ctx.session.active_stream_id == ctx.stream_id


@pytest.mark.anyio
async def test_missing_backend_history_and_lifecycle_remain_readable():
    ctx = context()
    ctx.session.curr_run_id = ctx.run_id
    service = AgentSessionService(ctx.db, ctx.role)
    result = Mock()
    result.scalars.return_value.all.return_value = []
    assert isinstance(ctx.db, AsyncMock)
    ctx.db.execute.return_value = result
    with patch.object(service, "get_session", return_value=ctx.session):
        assert await service.list_messages(ctx.session.id) == []
        lifecycle = await service.get_turn_lifecycle(ctx.session)
        assert lifecycle.lifecycle == TurnLifecycle.UNAVAILABLE
        assert lifecycle.run_id == ctx.run_id
        with pytest.raises(TracecatValidationError, match="unavailable"):
            await service.validate_turn_request(
                ctx.session.id, BasicChatRequest(message="Hello")
            )
    ctx.db.commit.assert_not_awaited()


@pytest.mark.anyio
async def test_disabled_backend_keeps_history_projection():
    ctx = context()
    service = AgentSessionService(ctx.db, ctx.role)
    provider = Mock(spec=DurableSessionBackend)
    provider.is_enabled.return_value = False
    provider.history = Mock(load=AsyncMock(return_value=[]))
    result = Mock()
    result.scalars.return_value.all.return_value = []
    assert isinstance(ctx.db, AsyncMock)
    ctx.db.execute.return_value = result
    with (
        patch.object(service, "get_session", return_value=ctx.session),
        patch.object(
            registry, "get_session_backends", return_value={"external": provider}
        ),
    ):
        assert not registry.session_backend_available("external", "claude_code")
        assert await service.list_messages(ctx.session.id) == []
        provider.history.load.assert_awaited_once()


@pytest.mark.anyio
@pytest.mark.parametrize(
    "backend_id,harness", [("v2", "claude_code"), ("v1", "other_harness")]
)
async def test_durable_activity_rejects_existing_session_with_different_identity(
    backend_id, harness
):
    ctx = context()
    ctx.session.backend_id = backend_id
    ctx.session.harness_type = harness
    service = AsyncMock()
    service.get_session.return_value = ctx.session
    manager = AsyncMock()
    manager.__aenter__.return_value = service
    with patch.object(AgentSessionService, "with_session", return_value=manager):
        with pytest.raises(ApplicationError):
            await create_session_activity(
                CreateSessionInput(
                    role=ctx.role,
                    session_id=ctx.session.id,
                    require_existing=True,
                    entity_type=AgentSessionEntity.WORKSPACE_CHAT,
                    entity_id=uuid4(),
                    curr_run_id=uuid4(),
                )
            )
    service.session.commit.assert_not_awaited()


@pytest.mark.anyio
async def test_legacy_workflow_views_do_not_target_another_backend():
    ctx = context()
    assert isinstance(ctx.db, AsyncMock)
    ctx.db.scalar.return_value = "v2"
    approvals = ApprovalService(ctx.db, ctx.role)
    with patch.object(approvals, "handle", new_callable=AsyncMock) as handle:
        assert await approvals.get_session(ctx.session.id) is None
        handle.assert_not_awaited()
    inbox = AgentRunsInboxProvider(ctx.db, ctx.role)
    ctx.db.scalar.return_value = 0
    assert await inbox.count_pending_items() == 0
    query = ctx.db.scalar.await_args.args[0]
    compiled = query.compile(compile_kwargs={"literal_binds": True})
    assert "agent_session.backend_id = 'v1'" in str(compiled)


def test_workflow_identity_is_shared_with_existing_durable_ids():
    run_id = uuid4()
    assert agent_workflow_id(run_id) == f"agent/{run_id}"
    assert agent_workflow_id(run_id) == AgentWorkflowID(run_id)


@pytest.mark.anyio
async def test_builtin_cancel_targets_shared_run_identity():
    ctx = context()
    handle = Mock(execute_update=AsyncMock())
    client = Mock(get_workflow_handle=Mock(return_value=handle))
    with patch(
        "tracecat.agent.session.backends.durable.signal_turn_cancel",
        new_callable=AsyncMock,
    ):
        await DurableSessionBackend().cancel(client, ctx.run_id)
    client.get_workflow_handle.assert_called_once_with(f"agent/{ctx.run_id}")
    assert handle.execute_update.await_args.args[0] == "request_cancel"


@pytest.mark.anyio
async def test_approvals_use_shared_identity_with_backend_specific_update():
    ctx = context()
    ctx.session.curr_run_id = ctx.run_id
    service = AgentSessionService(ctx.db, ctx.role)
    provider = Mock(spec=DurableSessionBackend)
    provider.approval_update_name = "approve"
    handle = Mock(execute_update=AsyncMock(return_value=True))
    client = Mock(get_workflow_handle=Mock(return_value=handle))
    attempt = ApprovalContinuationAttempt(
        stream_id=ctx.stream_id,
        previous_stream_id=None,
        stream=Mock(),
    )
    with (
        patch.object(service, "get_session", return_value=ctx.session),
        patch.object(
            service, "_pending_approval_tool_call_ids", return_value={"call-1"}
        ),
        patch.object(service, "_settled_approval_decisions", return_value={}),
        patch.object(
            service, "_existing_approval_continuation_attempt", return_value=attempt
        ),
        patch(
            "tracecat.agent.session.service.get_session_backend", return_value=provider
        ),
        patch(
            "tracecat.agent.session.service.get_temporal_client", return_value=client
        ),
    ):
        await service._continue_with_approvals(
            ctx.session.id,
            ContinueRunRequest(
                decisions=[ApprovalDecision(tool_call_id="call-1", action="approve")]
            ),
        )
    client.get_workflow_handle.assert_called_once_with(f"agent/{ctx.run_id}")
    assert handle.execute_update.await_args.args[0] == "approve"
