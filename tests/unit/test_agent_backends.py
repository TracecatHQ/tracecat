"""Backend registration and service delegation without a private implementation."""

import contextlib
from collections.abc import Iterator
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from uuid import uuid4

import pytest
from temporalio.client import (
    WorkflowExecutionStatus,
    WorkflowUpdateFailedError,
    WorkflowUpdateRPCTimeoutOrCancelledError,
)
from temporalio.exceptions import ApplicationError
from temporalio.service import RPCError, RPCStatusCode
from tracecat_ee.agent.approvals.service import ApprovalService
from tracecat_ee.agent.workflows.durable import DurableAgentWorkflow
from tracecat_ee.inbox.providers.agent_runs import AgentRunsInboxProvider

from tracecat.agent.backends import registry
from tracecat.agent.backends.default import DefaultBackend
from tracecat.agent.backends.schemas import WorkflowApprovalSubmission
from tracecat.agent.backends.types import (
    AgentBackendCapability,
    AgentControlRejected,
    AgentControlUncertain,
    SessionDispatchUncertain,
    SessionTurnContext,
)
from tracecat.agent.session.activities import (
    CreateSessionInput,
    create_session_activity,
)
from tracecat.agent.session.schemas import AgentSessionCreate, AgentSessionUpdate
from tracecat.agent.session.service import AgentSessionService
from tracecat.agent.session.types import AgentSessionEntity, TurnLifecycle
from tracecat.agent.types import AgentConfig
from tracecat.auth.types import Role
from tracecat.chat.schemas import BasicChatRequest
from tracecat.db.models import AgentSession
from tracecat.exceptions import TracecatConflictError, TracecatValidationError


@pytest.fixture(autouse=True)
def clean_backend_registry() -> Iterator[None]:
    registry.get_agent_backends.cache_clear()
    yield
    registry.get_agent_backends.cache_clear()


def entry(name: str, factory: object) -> SimpleNamespace:
    return SimpleNamespace(name=name, load=Mock(return_value=factory))


def test_discovery_loads_factories_once_and_rejects_unknown_backends():
    provider = DefaultBackend()
    factory = Mock(return_value=provider)
    with patch.object(
        registry, "entry_points", return_value=[entry("external", factory)]
    ):
        assert registry.get_agent_backend("external") is provider
        assert registry.get_agent_backend("external") is provider
        assert isinstance(registry.get_agent_backend(None), DefaultBackend)
        factory.assert_called_once_with()
        with pytest.raises(TracecatValidationError, match="unavailable"):
            registry.get_agent_backend("missing")


@pytest.mark.parametrize(
    "entries, error",
    [
        ([entry("oss", DefaultBackend)], ValueError),
        (
            [
                entry("external", DefaultBackend),
                entry("external", DefaultBackend),
            ],
            ValueError,
        ),
        ([entry("invalid.name", DefaultBackend)], ValueError),
        ([entry("x" * 51, DefaultBackend)], ValueError),
        ([entry("external", object())], TypeError),
        ([entry("external", lambda: object())], TypeError),
    ],
)
def test_invalid_plugins_fail_closed(entries, error):
    with (
        patch.object(registry, "entry_points", return_value=entries),
        pytest.raises(error),
    ):
        registry.get_agent_backends()


def test_disabled_plugins_cannot_be_selected():
    provider = DefaultBackend()
    with (
        patch.object(provider, "is_enabled", return_value=False),
        patch.object(
            registry, "entry_points", return_value=[entry("external", lambda: provider)]
        ),
        pytest.raises(TracecatValidationError, match="unavailable"),
    ):
        registry.get_agent_backend("external")


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
        db=AsyncMock(add=Mock(), scalar=AsyncMock(return_value=session)),
        session=session,
        role=role,
        config=AgentConfig(model_name="test", model_provider="anthropic"),
        prompt="Hello",
        run_id=uuid4(),
        stream_id=uuid4(),
    )


@pytest.mark.anyio
async def test_builtin_dispatch_preserves_workflow_contract():
    ctx = context()
    assert isinstance(ctx.db, AsyncMock)
    client = AsyncMock()

    async def start_workflow(*_args, **_kwargs):
        assert isinstance(ctx.db, AsyncMock)
        ctx.db.commit.assert_awaited_once()

    client.start_workflow.side_effect = start_workflow
    with patch(
        "tracecat.agent.backends.base.get_temporal_client",
        return_value=client,
    ):
        await DefaultBackend().start_turn(ctx)
    assert ctx.session.curr_run_id == ctx.run_id
    assert ctx.session.active_stream_id == ctx.stream_id
    ctx.db.commit.assert_awaited_once()
    call = client.start_workflow.await_args
    assert call.args[0] is DefaultBackend.workflow.run
    assert call.args[1].harness_type == "claude_code"
    assert call.args[1].agent_args.active_stream_id == ctx.stream_id
    assert call.kwargs["id"] == f"agent/{ctx.run_id}"


@pytest.mark.anyio
@pytest.mark.parametrize("missing", [False, True])
async def test_dispatch_rejects_missing_or_owned_session_before_preparation(missing):
    ctx = context()
    assert isinstance(ctx.db, AsyncMock)
    ctx.session.curr_run_id = uuid4()
    if missing:
        ctx.db.scalar.return_value = None
    backend = DefaultBackend()
    client = AsyncMock()
    with (
        patch("tracecat.agent.backends.base.get_temporal_client", return_value=client),
        patch.object(backend, "build_workflow_args") as prepare,
        pytest.raises(TracecatConflictError),
    ):
        await backend.start_turn(ctx)
    prepare.assert_not_called()
    ctx.db.commit.assert_not_awaited()
    client.start_workflow.assert_not_awaited()


@pytest.mark.anyio
async def test_preparation_failure_does_not_reserve_or_dispatch():
    ctx = context()
    assert isinstance(ctx.db, AsyncMock)
    backend = DefaultBackend()
    client = AsyncMock()
    with (
        patch("tracecat.agent.backends.base.get_temporal_client", return_value=client),
        patch.object(backend, "build_workflow_args", side_effect=ValueError("invalid")),
        pytest.raises(ValueError, match="invalid"),
    ):
        await backend.start_turn(ctx)
    assert ctx.session.curr_run_id is None
    ctx.db.commit.assert_not_awaited()
    client.start_workflow.assert_not_awaited()


@pytest.mark.anyio
async def test_service_dispatches_resolved_config_and_propagates_uncertainty():
    ctx = context()
    assert isinstance(ctx.db, AsyncMock)
    service = AgentSessionService(ctx.db, ctx.role)
    provider = Mock(spec=DefaultBackend)
    provider.start_turn = AsyncMock(side_effect=SessionDispatchUncertain("uncertain"))

    @contextlib.asynccontextmanager
    async def configuration(_):
        yield ctx.config

    with (
        patch.object(service, "validate_turn_request", return_value=ctx.session),
        patch.object(service, "_build_agent_config", configuration),
        patch(
            "tracecat.agent.session.service.get_agent_backend", return_value=provider
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
            ctx.session, params=AgentSessionUpdate(backend_id="oss")
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
    provider = Mock(spec=DefaultBackend)
    provider.get_turn_lifecycle = AsyncMock(return_value=TurnLifecycle.RUNNING)
    provider.cancel = AsyncMock()
    with (
        patch.object(service, "get_session", return_value=ctx.session),
        patch.object(service, "require_entitlement", return_value=None),
        patch(
            "tracecat.agent.session.service.get_agent_backend", return_value=provider
        ),
        patch(
            "tracecat.agent.session.service.find_agent_backend", return_value=provider
        ),
    ):
        result = await service.get_turn_lifecycle(ctx.session)
        assert result.lifecycle == TurnLifecycle.RUNNING
        await service.request_cancel(ctx.session.id)
    provider.get_turn_lifecycle.assert_awaited_with(ctx.run_id)
    provider.cancel.assert_awaited_once_with(ctx.run_id)


@pytest.mark.anyio
async def test_backend_capabilities_guard_fork_and_caller_owned_dispatch():
    ctx = context()
    ctx.session.backend_id = "oss"
    assert isinstance(ctx.db, AsyncMock)
    service = AgentSessionService(ctx.db, ctx.role)
    provider = Mock(spec=DefaultBackend)
    provider.capabilities = frozenset()
    with (
        patch.object(service, "get_session", return_value=ctx.session),
        patch.object(service, "validate_turn_request", return_value=ctx.session),
        patch(
            "tracecat.agent.session.service.get_agent_backend", return_value=provider
        ),
    ):
        with pytest.raises(TracecatValidationError, match="fork"):
            await service.fork_session(ctx.session.id)
        with pytest.raises(TracecatValidationError, match="caller-owned"):
            await service.prepare_new_turn(ctx.session.id, "Hello")
    ctx.db.commit.assert_not_awaited()


@pytest.mark.anyio
@pytest.mark.parametrize(
    "capabilities,allowed",
    [
        (frozenset({AgentBackendCapability.FORK}), True),
        (frozenset({AgentBackendCapability.CALLER_OWNED_WORKFLOWS}), False),
    ],
)
async def test_fork_requires_its_specific_capability(capabilities, allowed):
    ctx = context()
    assert isinstance(ctx.db, AsyncMock)
    service = AgentSessionService(ctx.db, ctx.role)
    provider = Mock(spec=DefaultBackend)
    provider.capabilities = capabilities
    with (
        patch.object(service, "get_session", return_value=ctx.session),
        patch(
            "tracecat.agent.session.service.get_agent_backend", return_value=provider
        ),
    ):
        if allowed:
            fork = await service.fork_session(ctx.session.id)
            assert fork.backend_id == ctx.session.backend_id
            assert fork.parent_session_id == ctx.session.id
            ctx.db.commit.assert_awaited_once()
        else:
            with pytest.raises(TracecatValidationError, match="fork"):
                await service.fork_session(ctx.session.id)
            ctx.db.commit.assert_not_awaited()


@pytest.mark.anyio
async def test_backend_and_harness_are_independent_and_defaults_resolve_internally():
    ctx = context()
    service = AgentSessionService(ctx.db, ctx.role)

    class CustomHarnessBackend(DefaultBackend):
        default_harness = "custom_harness"
        supported_harnesses = frozenset({"custom_harness", "another_harness"})

    provider = CustomHarnessBackend()
    with patch.object(
        registry, "entry_points", return_value=[entry("ee", lambda: provider)]
    ):
        created = await service.create_session(
            AgentSessionCreate(
                backend_id="ee",
                entity_type=AgentSessionEntity.WORKSPACE_CHAT,
                entity_id=uuid4(),
            )
        )
        assert created.backend_id == "ee"
        assert created.harness_type == "custom_harness"
        assert (
            registry.get_agent_backend("ee", harness_type="another_harness") is provider
        )
        with pytest.raises(TracecatValidationError, match="unsupported"):
            registry.get_agent_backend("oss", harness_type="custom_harness")
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
        "tracecat.agent.backends.base.get_temporal_client",
        return_value=client,
    ):
        with pytest.raises(SessionDispatchUncertain) as caught:
            await DefaultBackend().start_turn(ctx)
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
    provider = Mock(spec=DefaultBackend)
    provider.is_enabled.return_value = False
    provider.history = Mock(load=AsyncMock(return_value=[]))
    result = Mock()
    result.scalars.return_value.all.return_value = []
    assert isinstance(ctx.db, AsyncMock)
    ctx.db.execute.return_value = result
    with (
        patch.object(service, "get_session", return_value=ctx.session),
        patch.object(
            registry, "get_agent_backends", return_value={"external": provider}
        ),
    ):
        assert not registry.agent_backend_available("external", "claude_code")
        assert await service.list_messages(ctx.session.id) == []
        provider.history.load.assert_awaited_once()


@pytest.mark.anyio
@pytest.mark.parametrize(
    "backend_id,harness", [("ee", "claude_code"), ("oss", "other_harness")]
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
    ctx.db.scalar.return_value = "ee"
    approvals = ApprovalService(ctx.db, ctx.role)
    with patch.object(approvals, "handle", new_callable=AsyncMock) as handle:
        assert await approvals.get_session(ctx.session.id) is None
        handle.assert_not_awaited()
    inbox = AgentRunsInboxProvider(ctx.db, ctx.role)
    ctx.db.scalar.return_value = 0
    assert await inbox.count_pending_items() == 0
    query = ctx.db.scalar.await_args.args[0]
    compiled = query.compile(compile_kwargs={"literal_binds": True})
    assert "agent_session.backend_id = 'oss'" in str(compiled)


@pytest.mark.anyio
@pytest.mark.parametrize(
    "status,expected",
    [
        (WorkflowExecutionStatus.RUNNING, TurnLifecycle.RUNNING),
        (WorkflowExecutionStatus.CONTINUED_AS_NEW, TurnLifecycle.RUNNING),
        (WorkflowExecutionStatus.COMPLETED, TurnLifecycle.COMPLETED),
        (WorkflowExecutionStatus.CANCELED, TurnLifecycle.CANCELLED),
        (WorkflowExecutionStatus.FAILED, TurnLifecycle.FAILED),
        (WorkflowExecutionStatus.TERMINATED, TurnLifecycle.FAILED),
        (WorkflowExecutionStatus.TIMED_OUT, TurnLifecycle.FAILED),
    ],
)
async def test_backend_maps_execution_lifecycle(status, expected):
    run_id = uuid4()
    handle = Mock(describe=AsyncMock(return_value=SimpleNamespace(status=status)))
    client = Mock(get_workflow_handle=Mock(return_value=handle))
    with patch("tracecat.agent.backends.base.get_temporal_client", return_value=client):
        assert await DefaultBackend().get_turn_lifecycle(run_id) == expected
    client.get_workflow_handle.assert_called_once_with(f"agent/{run_id}")


@pytest.mark.anyio
async def test_backend_missing_execution_keeps_reconnect_terminal():
    handle = Mock(
        describe=AsyncMock(side_effect=RPCError("gone", RPCStatusCode.NOT_FOUND, b""))
    )
    client = Mock(get_workflow_handle=Mock(return_value=handle))
    with patch("tracecat.agent.backends.base.get_temporal_client", return_value=client):
        assert (
            await DefaultBackend().get_turn_lifecycle(uuid4()) == TurnLifecycle.FAILED
        )


@pytest.mark.anyio
@pytest.mark.parametrize(
    "failure,expected",
    [
        (WorkflowUpdateRPCTimeoutOrCancelledError(), AgentControlUncertain),
        (
            RPCError("private transport details", RPCStatusCode.UNAVAILABLE, b""),
            AgentControlUncertain,
        ),
        (
            WorkflowUpdateFailedError(ApplicationError("private rejection")),
            AgentControlRejected,
        ),
    ],
)
async def test_backend_approval_errors_preserve_outcome_without_sdk_context(
    failure, expected
):
    submission = WorkflowApprovalSubmission(approvals={}, new_stream_id=uuid4())
    handle = Mock(execute_update=AsyncMock(side_effect=failure))
    client = Mock(get_workflow_handle=Mock(return_value=handle))
    with patch("tracecat.agent.backends.base.get_temporal_client", return_value=client):
        with pytest.raises(expected) as caught:
            await DefaultBackend().submit_approvals(uuid4(), submission)
    assert caught.value.__context__ is None
    assert caught.value.__cause__ is None


@pytest.mark.anyio
async def test_backend_connection_failure_is_definitive_before_submission():
    submission = WorkflowApprovalSubmission(approvals={}, new_stream_id=uuid4())
    with patch(
        "tracecat.agent.backends.base.get_temporal_client",
        side_effect=RPCError("connection unavailable", RPCStatusCode.UNAVAILABLE, b""),
    ):
        with pytest.raises(AgentControlRejected):
            await DefaultBackend().submit_approvals(uuid4(), submission)


@pytest.mark.anyio
async def test_default_cancel_keeps_workflow_control_when_executor_signal_fails():
    run_id = uuid4()
    handle = Mock(execute_update=AsyncMock())
    client = Mock(get_workflow_handle=Mock(return_value=handle))
    with (
        patch("tracecat.agent.backends.base.get_temporal_client", return_value=client),
        patch(
            "tracecat.agent.backends.default.signal_turn_cancel",
            side_effect=RuntimeError,
        ),
    ):
        await DefaultBackend().cancel(run_id)
    client.get_workflow_handle.assert_called_once_with(f"agent/{run_id}")
    assert (
        handle.execute_update.await_args.args[0] is DurableAgentWorkflow.request_cancel
    )
