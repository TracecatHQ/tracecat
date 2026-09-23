"""Backend registration and service delegation without a private implementation."""

import asyncio
import contextlib
from collections.abc import Iterator
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from uuid import uuid4

import pytest
from claude_agent_sdk.types import UserMessage
from temporalio.api.workflowservice.v1 import StartWorkflowExecutionResponse
from temporalio.client import (
    Client,
    WorkflowExecutionStatus,
    WorkflowUpdateFailedError,
    WorkflowUpdateRPCTimeoutOrCancelledError,
)
from temporalio.converter import PayloadCodec
from temporalio.exceptions import ApplicationError, WorkflowAlreadyStartedError
from temporalio.service import ConnectConfig, RPCError, RPCStatusCode, ServiceClient
from tracecat_ee.agent.approvals.service import ApprovalService
from tracecat_ee.agent.workflows.durable import DurableAgentWorkflow

from tracecat.agent.backends import registry
from tracecat.agent.backends.base import AgentBackend
from tracecat.agent.backends.default import DefaultBackend
from tracecat.agent.backends.schemas import (
    AgentWorkflowArgs,
    WorkflowApprovalSubmission,
)
from tracecat.agent.backends.types import (
    AgentControlRejected,
    AgentControlUncertain,
    SessionDispatchUncertain,
    SessionForkContext,
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
from tracecat.db.models import AgentSession, AgentSessionHistory
from tracecat.dsl._converter import get_data_converter
from tracecat.exceptions import (
    TracecatConflictError,
    TracecatServiceError,
    TracecatValidationError,
)
from tracecat.temporal.codec import TemporalPayloadCodecError


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


@pytest.mark.parametrize(
    "missing",
    ["name", "task_queue", "default_harness", "supported_harnesses", "workflow"],
)
def test_discovery_rejects_missing_required_attributes(missing: str):
    class IncompleteBackend(AgentBackend[None, None]):
        async def prepare_fork(self, context: SessionForkContext) -> None:
            pass

        async def build_workflow_args(self, context: SessionTurnContext) -> None:
            return None

    attributes: dict[str, object] = {
        "name": "Example backend",
        "task_queue": "example-queue",
        "default_harness": "custom",
        "supported_harnesses": frozenset({"custom"}),
        "workflow": DurableAgentWorkflow,
    }
    for field, value in attributes.items():
        if field != missing:
            setattr(IncompleteBackend, field, value)
    with (
        patch.object(
            registry,
            "entry_points",
            return_value=[entry("external", IncompleteBackend)],
        ),
        pytest.raises((TypeError, ValueError), match=f"external: {missing}"),
    ):
        registry.get_agent_backends()


@pytest.mark.parametrize(
    "field,value",
    [
        ("name", ""),
        ("name", "  "),
        ("name", 123),
        ("task_queue", ""),
        ("task_queue", "  "),
        ("task_queue", 123),
        ("default_harness", None),
        ("default_harness", 123),
        ("supported_harnesses", "claude_code"),
        ("supported_harnesses", ["claude_code"]),
        ("supported_harnesses", frozenset({"claude_code", 123})),
        ("workflow", None),
        ("workflow", object()),
    ],
)
def test_discovery_rejects_malformed_required_attributes(field: str, value: object):
    class MalformedBackend(DefaultBackend):
        pass

    setattr(MalformedBackend, field, value)
    with (
        patch.object(
            registry, "entry_points", return_value=[entry("external", MalformedBackend)]
        ),
        pytest.raises((TypeError, ValueError), match=f"external: {field}"),
    ):
        registry.get_agent_backends()


@pytest.mark.parametrize("method", ["run", "set_approvals", "request_cancel"])
@pytest.mark.parametrize("missing", [False, True])
def test_discovery_rejects_missing_or_noncallable_workflow_methods(
    method: str, missing: bool
):
    methods: dict[str, object] = {
        "run": AsyncMock(),
        "set_approvals": Mock(),
        "request_cancel": Mock(),
    }
    if missing:
        del methods[method]
    else:
        methods[method] = None
    provider = DefaultBackend()
    with (
        patch.object(provider, "workflow", type("IncompleteWorkflow", (), methods)),
        patch.object(
            registry, "entry_points", return_value=[entry("external", lambda: provider)]
        ),
        pytest.raises(TypeError, match=f"external: workflow.{method}"),
    ):
        registry.get_agent_backends()


def test_discovery_accepts_inherited_backend_metadata():
    class InheritedBackend(DefaultBackend):
        pass

    with patch.object(
        registry, "entry_points", return_value=[entry("external", InheritedBackend)]
    ):
        assert isinstance(registry.get_agent_backend("external"), InheritedBackend)


@pytest.mark.parametrize(
    "harness", ["", "x" * 51, "custom.harness", "Custom", "1custom"]
)
@pytest.mark.parametrize("use_as_default", [False, True])
def test_invalid_harness_identifiers_fail_registration(harness, use_as_default):
    class CustomHarnessBackend(DefaultBackend):
        default_harness = harness if use_as_default else "claude_code"
        supported_harnesses = frozenset({"claude_code", harness})

    with (
        patch.object(
            registry,
            "entry_points",
            return_value=[entry("external", CustomHarnessBackend)],
        ),
        pytest.raises(ValueError, match="Invalid harness identifier"),
    ):
        registry.get_agent_backends()


def test_harness_identifier_accepts_maximum_length():
    class CustomHarnessBackend(DefaultBackend):
        default_harness = "a" + "_1" * 24 + "z"
        supported_harnesses = frozenset({default_harness})

    with patch.object(
        registry,
        "entry_points",
        return_value=[entry("external", CustomHarnessBackend)],
    ):
        backend = registry.get_agent_backend("external")
    assert backend.default_harness == CustomHarnessBackend.default_harness


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


def temporal_client() -> tuple[Client, AsyncMock]:
    service = Mock(spec=ServiceClient)
    service.config = ConnectConfig(target_host="localhost:7233", identity="test-client")
    rpc = AsyncMock(return_value=StartWorkflowExecutionResponse(run_id="run-1"))
    service._rpc_call = rpc
    return Client(service, data_converter=get_data_converter()), rpc


@pytest.mark.anyio
async def test_builtin_dispatch_preserves_workflow_contract():
    ctx = context()
    assert isinstance(ctx.db, AsyncMock)
    client, rpc = temporal_client()

    async def start_workflow(*_args, **_kwargs):
        assert isinstance(ctx.db, AsyncMock)
        ctx.db.commit.assert_awaited_once()
        return StartWorkflowExecutionResponse(run_id="run-1")

    rpc.side_effect = start_workflow
    with patch(
        "tracecat.agent.backends.base.get_temporal_client",
        return_value=client,
    ):
        await DefaultBackend().start_turn(ctx)
    assert ctx.session.curr_run_id == ctx.run_id
    assert ctx.session.active_stream_id == ctx.stream_id
    ctx.db.commit.assert_awaited_once()
    assert rpc.await_args is not None
    request = rpc.await_args.args[1]
    (args,) = await client.data_converter.decode(
        list(request.input.payloads), [AgentWorkflowArgs]
    )
    assert args.harness_type == "claude_code"
    assert args.agent_args.active_stream_id == ctx.stream_id
    assert request.workflow_id == f"agent/{ctx.run_id}"
    assert request.workflow_type.name == "DurableAgentWorkflow"
    assert rpc.await_args.kwargs["retry"] is False
    ctx.db.rollback.assert_not_awaited()


@pytest.mark.anyio
@pytest.mark.parametrize("missing", [False, True])
async def test_dispatch_rejects_missing_or_owned_session_before_preparation(missing):
    ctx = context()
    assert isinstance(ctx.db, AsyncMock)
    ctx.session.curr_run_id = uuid4()
    if missing:
        ctx.db.scalar.return_value = None
    backend = DefaultBackend()
    client, rpc = temporal_client()
    with (
        patch("tracecat.agent.backends.base.get_temporal_client", return_value=client),
        patch.object(backend, "build_workflow_args") as prepare,
        patch.object(backend, "handle", side_effect=TimeoutError),
        pytest.raises(TracecatConflictError),
    ):
        await backend.start_turn(ctx)
    prepare.assert_not_called()
    ctx.db.commit.assert_not_awaited()
    rpc.assert_not_awaited()


@pytest.mark.anyio
@pytest.mark.parametrize(
    "outcome",
    [
        WorkflowExecutionStatus.RUNNING,
        WorkflowExecutionStatus.CONTINUED_AS_NEW,
        None,
        RPCError("not found", RPCStatusCode.NOT_FOUND, b""),
        RPCError("unavailable", RPCStatusCode.UNAVAILABLE, b""),
        TimeoutError("lookup timed out"),
    ],
)
async def test_turn_admission_preserves_running_or_unconfirmed_ownership(outcome):
    ctx = context()
    assert isinstance(ctx.db, AsyncMock)
    old_run_id, old_stream_id = uuid4(), uuid4()
    ctx.session.curr_run_id = old_run_id
    ctx.session.active_stream_id = old_stream_id
    backend = DefaultBackend()
    client, rpc = temporal_client()
    handle = Mock()
    handle.describe = AsyncMock(return_value=SimpleNamespace(status=outcome))
    if isinstance(outcome, Exception):
        handle.describe.side_effect = outcome
    with (
        patch("tracecat.agent.backends.base.get_temporal_client", return_value=client),
        patch.object(backend, "handle", return_value=handle),
        patch.object(backend, "get_turn_lifecycle") as lifecycle,
        pytest.raises(TracecatConflictError),
    ):
        await backend.start_turn(ctx)
    lifecycle.assert_not_called()
    assert ctx.session.curr_run_id == old_run_id
    assert ctx.session.active_stream_id == old_stream_id
    ctx.db.commit.assert_not_awaited()
    ctx.db.scalar.assert_awaited_once()
    rpc.assert_not_awaited()


@pytest.mark.anyio
async def test_recovery_rechecks_ownership_after_releasing_the_lock():
    ctx = context()
    assert isinstance(ctx.db, AsyncMock)
    ctx.session.curr_run_id = uuid4()
    newer_session = AgentSession(id=ctx.session.id, curr_run_id=uuid4())
    ctx.db.scalar.side_effect = [ctx.session, ctx.session.id, newer_session]
    backend = DefaultBackend()
    client, rpc = temporal_client()
    handle = Mock(
        describe=AsyncMock(
            return_value=SimpleNamespace(status=WorkflowExecutionStatus.COMPLETED)
        )
    )
    with (
        patch("tracecat.agent.backends.base.get_temporal_client", return_value=client),
        patch.object(backend, "handle", return_value=handle),
        pytest.raises(TracecatConflictError),
    ):
        await backend.start_turn(ctx)
    ctx.db.commit.assert_awaited_once()
    assert ctx.db.scalar.await_count == 3
    assert newer_session.curr_run_id != ctx.run_id
    rpc.assert_not_awaited()


@pytest.mark.anyio
async def test_preparation_failure_does_not_reserve_or_dispatch():
    ctx = context()
    assert isinstance(ctx.db, AsyncMock)
    backend = DefaultBackend()
    client, rpc = temporal_client()
    with (
        patch("tracecat.agent.backends.base.get_temporal_client", return_value=client),
        patch.object(backend, "build_workflow_args", side_effect=ValueError("invalid")),
        pytest.raises(RuntimeError, match="before dispatch"),
    ):
        await backend.start_turn(ctx)
    assert ctx.session.curr_run_id is None
    ctx.db.commit.assert_not_awaited()
    ctx.db.rollback.assert_awaited_once()
    rpc.assert_not_awaited()


@pytest.mark.anyio
@pytest.mark.parametrize("after_commit", [False, True])
async def test_dispatch_cancellation_rolls_back_only_before_commit(after_commit):
    ctx = context()
    assert isinstance(ctx.db, AsyncMock)
    backend = DefaultBackend()
    client, rpc = temporal_client()
    if after_commit:
        rpc.side_effect = asyncio.CancelledError
    else:
        ctx.db.commit.side_effect = asyncio.CancelledError
    with (
        patch("tracecat.agent.backends.base.get_temporal_client", return_value=client),
        pytest.raises(asyncio.CancelledError),
    ):
        await backend.start_turn(ctx)
    if after_commit:
        ctx.db.rollback.assert_not_awaited()
        rpc.assert_awaited_once()
    else:
        ctx.db.rollback.assert_awaited_once()
        rpc.assert_not_awaited()


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
async def test_caller_owned_dispatch_remains_builtin_only():
    ctx = context()
    assert isinstance(ctx.db, AsyncMock)
    service = AgentSessionService(ctx.db, ctx.role)
    with (
        patch.object(service, "validate_turn_request", return_value=ctx.session),
    ):
        with pytest.raises(TracecatValidationError, match="caller-owned"):
            await service.prepare_new_turn(ctx.session.id, "Hello")
    ctx.db.commit.assert_not_awaited()


@pytest.mark.anyio
@pytest.mark.parametrize("preparation_fails", [False, True])
async def test_fork_delegates_to_required_backend_operation(preparation_fails):
    ctx = context()
    assert isinstance(ctx.db, AsyncMock)
    service = AgentSessionService(ctx.db, ctx.role)
    provider = Mock(spec=DefaultBackend)
    provider.prepare_fork = AsyncMock()
    if preparation_fails:
        provider.prepare_fork.side_effect = ValueError("Unable to prepare fork")
    with (
        patch.object(service, "get_session", return_value=ctx.session),
        patch(
            "tracecat.agent.session.service.get_agent_backend", return_value=provider
        ),
    ):
        if not preparation_fails:
            fork = await service.fork_session(ctx.session.id)
            assert fork.backend_id == ctx.session.backend_id
            assert fork.parent_session_id == ctx.session.id
            ctx.db.commit.assert_awaited_once()
            submitted = provider.prepare_fork.await_args.args[0]
            assert submitted.db is ctx.db
            assert submitted.parent is ctx.session
            assert submitted.fork is fork
            assert fork.id is not None
            assert submitted.role is ctx.role
        else:
            with pytest.raises(ValueError, match="Unable to prepare fork"):
                await service.fork_session(ctx.session.id)
            ctx.db.commit.assert_not_awaited()


def test_backend_contract_requires_fork_preparation():
    class NoForkBackend(AgentBackend[None, None]):
        async def build_workflow_args(self, context: SessionTurnContext) -> None:
            return None

    with (
        patch.object(
            registry, "entry_points", return_value=[entry("external", NoForkBackend)]
        ),
        pytest.raises(TypeError, match="prepare_fork"),
    ):
        registry.get_agent_backends()


@pytest.mark.anyio
async def test_builtin_fork_copies_snapshot_without_mutating_parent():
    ctx = context()
    assert isinstance(ctx.db, AsyncMock)
    ctx.session.work_dir_snapshot = {"files": {"example.txt": "original"}}
    service = AgentSessionService(ctx.db, ctx.role)
    with (
        patch.object(service, "get_session", return_value=ctx.session),
        patch(
            "tracecat.agent.session.service.get_agent_backend",
            return_value=DefaultBackend(),
        ),
    ):
        fork = await service.fork_session(ctx.session.id)
    assert fork.work_dir_snapshot == ctx.session.work_dir_snapshot
    assert fork.work_dir_snapshot is not ctx.session.work_dir_snapshot
    assert fork.work_dir_snapshot is not None
    fork.work_dir_snapshot["files"]["example.txt"] = "changed"
    assert ctx.session.work_dir_snapshot == {"files": {"example.txt": "original"}}


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
@pytest.mark.parametrize(
    "error",
    [
        TypeError("private SDK details"),
        ValueError("private SDK details"),
        RuntimeError("private SDK details"),
        TemporalPayloadCodecError("private SDK details"),
    ],
)
async def test_local_encoding_failure_rolls_back_preparation(error):
    ctx = context()
    assert isinstance(ctx.db, AsyncMock)
    client, rpc = temporal_client()
    codec = Mock(spec=PayloadCodec)
    codec.encode = AsyncMock(side_effect=error)
    client = Client(
        **{
            **client.config(),
            "data_converter": replace(client.data_converter, payload_codec=codec),
        }
    )
    with (
        patch("tracecat.agent.backends.base.get_temporal_client", return_value=client),
        pytest.raises(RuntimeError) as caught,
    ):
        await DefaultBackend().start_turn(ctx)
    assert not isinstance(caught.value, SessionDispatchUncertain)
    assert caught.value.__context__ is None
    assert "private SDK details" not in str(caught.value)
    ctx.db.rollback.assert_awaited_once()
    ctx.db.commit.assert_not_awaited()
    rpc.assert_not_awaited()


@pytest.mark.anyio
async def test_real_client_encoding_failure_releases_reservation_before_rpc():
    ctx = context()
    assert isinstance(ctx.db, AsyncMock)
    client, dispatch = temporal_client()
    with (
        patch("tracecat.agent.backends.base.get_temporal_client", return_value=client),
        patch("tracecat.dsl._converter.orjson.dumps", side_effect=TypeError("invalid")),
        pytest.raises(RuntimeError) as caught,
    ):
        await DefaultBackend().start_turn(ctx)
    assert not isinstance(caught.value, SessionDispatchUncertain)
    assert caught.value.__context__ is None
    dispatch.assert_not_awaited()
    ctx.db.rollback.assert_awaited_once()
    ctx.db.commit.assert_not_awaited()


@pytest.mark.anyio
@pytest.mark.parametrize(
    "status",
    [
        RPCStatusCode.INVALID_ARGUMENT,
        RPCStatusCode.NOT_FOUND,
        RPCStatusCode.PERMISSION_DENIED,
        RPCStatusCode.FAILED_PRECONDITION,
        RPCStatusCode.OUT_OF_RANGE,
        RPCStatusCode.UNIMPLEMENTED,
        RPCStatusCode.UNAUTHENTICATED,
    ],
)
async def test_rpc_rejection_releases_reservation_without_raw_exception_context(status):
    ctx = context()
    assert isinstance(ctx.db, AsyncMock)
    client, rpc = temporal_client()
    rpc.side_effect = RPCError("private SDK details", status, b"")
    with (
        patch("tracecat.agent.backends.base.get_temporal_client", return_value=client),
        pytest.raises(RuntimeError, match="was rejected") as caught,
    ):
        await DefaultBackend().start_turn(ctx)
    assert not isinstance(caught.value, SessionDispatchUncertain)
    assert caught.value.__context__ is None
    assert caught.value.__cause__ is None
    assert ctx.db.scalar.await_count == 2
    assert ctx.db.commit.await_count == 2
    ctx.db.rollback.assert_not_awaited()
    rpc.assert_awaited_once()
    assert rpc.await_args is not None
    assert rpc.await_args.kwargs["retry"] is False


@pytest.mark.anyio
@pytest.mark.parametrize("failure", ["execute", "commit", "ownership_changed"])
async def test_rejection_cleanup_failure_preserves_uncertainty(failure):
    ctx = context()
    assert isinstance(ctx.db, AsyncMock)
    client, rpc = temporal_client()
    rpc.side_effect = RPCError(
        "private SDK details", RPCStatusCode.INVALID_ARGUMENT, b""
    )
    if failure == "execute":
        ctx.db.scalar.side_effect = [ctx.session, RuntimeError("private DB details")]
    elif failure == "commit":
        ctx.db.commit.side_effect = [None, RuntimeError("private DB details")]
    else:
        ctx.db.scalar.side_effect = [ctx.session, None]
    with (
        patch("tracecat.agent.backends.base.get_temporal_client", return_value=client),
        pytest.raises(SessionDispatchUncertain) as caught,
    ):
        await DefaultBackend().start_turn(ctx)
    assert caught.value.__context__ is None
    assert caught.value.__cause__ is None
    assert "private" not in str(caught.value)


@pytest.mark.anyio
@pytest.mark.parametrize(
    "failure", [None, "rollback", "execute", "commit", "ownership"]
)
async def test_commit_failure_reconciles_with_a_fresh_session(failure):
    ctx = context()
    assert isinstance(ctx.db, AsyncMock)
    client, rpc = temporal_client()
    ctx.db.commit.side_effect = ConnectionError("private commit details")
    cleanup_db = AsyncMock()
    cleanup_db.scalar.return_value = ctx.session.id
    if failure == "rollback":
        ctx.db.rollback.side_effect = ConnectionError("broken connection")
    elif failure == "execute":
        cleanup_db.scalar.side_effect = ConnectionError("cleanup unavailable")
    elif failure == "commit":
        cleanup_db.commit.side_effect = ConnectionError("cleanup acknowledgement lost")
    elif failure == "ownership":
        cleanup_db.scalar.return_value = None

    @contextlib.asynccontextmanager
    async def cleanup_session():
        assert isinstance(ctx.db, AsyncMock)
        ctx.db.rollback.assert_awaited_once()
        yield cleanup_db

    with (
        patch("tracecat.agent.backends.base.get_temporal_client", return_value=client),
        patch(
            "tracecat.agent.backends.base.get_async_session_context_manager",
            cleanup_session,
        ),
        pytest.raises(RuntimeError) as caught,
    ):
        await DefaultBackend().start_turn(ctx)
    assert isinstance(caught.value, SessionDispatchUncertain) == (
        failure in {"execute", "commit", "ownership"}
    )
    assert caught.value.__context__ is None
    assert "private" not in str(caught.value)
    rpc.assert_not_awaited()
    ctx.db.commit.assert_awaited_once()
    if failure == "rollback":
        ctx.db.invalidate.assert_awaited_once()
    else:
        ctx.db.invalidate.assert_not_awaited()


@pytest.mark.anyio
async def test_rpc_error_after_successful_start_does_not_release_reservation():
    ctx = context()
    assert isinstance(ctx.db, AsyncMock)
    client, rpc = temporal_client()
    start_workflow = Client.start_workflow

    async def start_then_fail(client, *args, **kwargs):
        await start_workflow(client, *args, **kwargs)
        raise RPCError("post-dispatch failure", RPCStatusCode.INVALID_ARGUMENT, b"")

    with (
        patch("tracecat.agent.backends.base.get_temporal_client", return_value=client),
        patch.object(Client, "start_workflow", start_then_fail),
        pytest.raises(SessionDispatchUncertain),
    ):
        await DefaultBackend().start_turn(ctx)
    rpc.assert_awaited_once()
    ctx.db.execute.assert_not_awaited()
    ctx.db.commit.assert_awaited_once()


@pytest.mark.anyio
async def test_interceptor_cannot_retry_an_uncertain_start():
    ctx = context()
    assert isinstance(ctx.db, AsyncMock)
    client, rpc = temporal_client()
    # A hidden second attempt would turn the ambiguous outcome into a rejection.
    rpc.side_effect = [
        RPCError("lost acknowledgement", RPCStatusCode.UNAVAILABLE, b""),
        RPCError("permissions changed", RPCStatusCode.PERMISSION_DENIED, b""),
    ]
    start_workflow = Client.start_workflow

    async def retry_start(client, *args, **kwargs):
        try:
            return await start_workflow(client, *args, **kwargs)
        except RPCError:
            return await start_workflow(client, *args, **kwargs)

    with (
        patch("tracecat.agent.backends.base.get_temporal_client", return_value=client),
        patch.object(Client, "start_workflow", retry_start),
        pytest.raises(SessionDispatchUncertain),
    ):
        await DefaultBackend().start_turn(ctx)
    rpc.assert_awaited_once()
    ctx.db.execute.assert_not_awaited()
    ctx.db.commit.assert_awaited_once()


@pytest.mark.anyio
@pytest.mark.parametrize(
    "error",
    [
        TimeoutError("private SDK details"),
        RPCError("private SDK details", RPCStatusCode.UNAVAILABLE, b""),
        RPCError("private SDK details", RPCStatusCode.DEADLINE_EXCEEDED, b""),
        RPCError("private SDK details", RPCStatusCode.ALREADY_EXISTS, b""),
        RPCError("private SDK details", RPCStatusCode.CANCELLED, b""),
        RPCError("private SDK details", RPCStatusCode.UNKNOWN, b""),
        RPCError("private SDK details", RPCStatusCode.RESOURCE_EXHAUSTED, b""),
        RPCError("private SDK details", RPCStatusCode.ABORTED, b""),
        RPCError("private SDK details", RPCStatusCode.INTERNAL, b""),
        RPCError("private SDK details", RPCStatusCode.DATA_LOSS, b""),
        WorkflowAlreadyStartedError("workflow-1", "test"),
        RuntimeError("unknown outcome"),
        TypeError("failure after dispatch"),
        ValueError("failure after dispatch"),
    ],
)
async def test_builtin_lost_ack_retains_ownership_without_raw_exception_context(error):
    ctx = context()
    assert isinstance(ctx.db, AsyncMock)
    client, rpc = temporal_client()
    rpc.side_effect = error
    with patch(
        "tracecat.agent.backends.base.get_temporal_client",
        return_value=client,
    ):
        with pytest.raises(SessionDispatchUncertain) as caught:
            await DefaultBackend().start_turn(ctx)
    assert caught.value.__context__ is None
    assert ctx.session.curr_run_id == ctx.run_id
    assert ctx.session.active_stream_id == ctx.stream_id
    ctx.db.execute.assert_not_awaited()
    ctx.db.commit.assert_awaited_once()
    ctx.db.rollback.assert_not_awaited()


@pytest.mark.anyio
async def test_missing_backend_history_raises_and_lifecycle_reports_unavailable():
    ctx = context()
    ctx.session.curr_run_id = ctx.run_id
    service = AgentSessionService(ctx.db, ctx.role)
    result = Mock()
    result.scalars.return_value.all.return_value = []
    assert isinstance(ctx.db, AsyncMock)
    ctx.db.execute.return_value = result
    with patch.object(service, "get_session", return_value=ctx.session):
        with pytest.raises(TracecatServiceError, match="backend is not installed"):
            await service.list_messages(ctx.session.id)
        lifecycle = await service.get_turn_lifecycle(ctx.session)
        assert lifecycle.lifecycle == TurnLifecycle.UNAVAILABLE
        assert lifecycle.run_id == ctx.run_id
        with pytest.raises(TracecatValidationError, match="unavailable"):
            await service.validate_turn_request(
                ctx.session.id, BasicChatRequest(message="Hello")
            )
    ctx.db.commit.assert_not_awaited()


@pytest.mark.anyio
@pytest.mark.parametrize("native_history", [False, True])
async def test_disabled_backend_keeps_saved_history_readable(native_history):
    ctx = context()
    service = AgentSessionService(ctx.db, ctx.role)
    provider = Mock(spec=DefaultBackend)
    provider.is_enabled.return_value = False
    saved_content = {
        "type": "user",
        "message": {"type": "user", "content": "Saved message"},
    }
    entry = AgentSessionHistory(
        id=uuid4(),
        workspace_id=ctx.role.workspace_id,
        session_id=ctx.session.id,
        kind="chat-message",
        content={"native_text": "Saved message"} if native_history else saved_content,
    )
    history = Mock(
        load=AsyncMock(return_value=[entry]),
        project=Mock(return_value=saved_content),
    )
    provider.history = history if native_history else None
    result = Mock()
    result.scalars.return_value.all.return_value = []
    assert isinstance(ctx.db, AsyncMock)
    ctx.db.execute.return_value = result
    with (
        patch.object(service, "get_session", return_value=ctx.session),
        patch.object(
            service, "_visible_history_entries", return_value=[entry]
        ) as load_shared_history,
        patch.object(
            registry, "get_agent_backends", return_value={"external": provider}
        ),
    ):
        assert not registry.agent_backend_available("external", "claude_code")
        messages = await service.list_messages(ctx.session.id)
        assert len(messages) == 1
        assert isinstance(messages[0].message, UserMessage)
        assert messages[0].message.content == "Saved message"
        with pytest.raises(TracecatValidationError, match="unavailable"):
            await service.validate_turn_request(
                ctx.session.id, BasicChatRequest(message="New message")
            )
    if native_history:
        history.load.assert_awaited_once()
        history.project.assert_called_once_with(entry)
        load_shared_history.assert_not_awaited()
    else:
        load_shared_history.assert_awaited_once()
        history.load.assert_not_awaited()


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
async def test_legacy_approval_view_does_not_target_another_backend():
    ctx = context()
    assert isinstance(ctx.db, AsyncMock)
    ctx.db.scalar.return_value = "ee"
    approvals = ApprovalService(ctx.db, ctx.role)
    with patch.object(approvals, "handle", new_callable=AsyncMock) as handle:
        assert await approvals.get_session(ctx.session.id) is None
        handle.assert_not_awaited()


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
    client = Mock(get_workflow_handle_for=Mock(return_value=handle))
    with patch("tracecat.agent.backends.base.get_temporal_client", return_value=client):
        assert await DefaultBackend().get_turn_lifecycle(run_id) == expected
    client.get_workflow_handle_for.assert_called_once_with(
        DurableAgentWorkflow.run, f"agent/{run_id}"
    )


@pytest.mark.anyio
async def test_backend_missing_execution_keeps_reconnect_terminal():
    handle = Mock(
        describe=AsyncMock(side_effect=RPCError("gone", RPCStatusCode.NOT_FOUND, b""))
    )
    client = Mock(get_workflow_handle_for=Mock(return_value=handle))
    with patch("tracecat.agent.backends.base.get_temporal_client", return_value=client):
        assert (
            await DefaultBackend().get_turn_lifecycle(uuid4()) == TurnLifecycle.FAILED
        )


@pytest.mark.anyio
@pytest.mark.parametrize(
    "failure,expected",
    [
        (WorkflowUpdateRPCTimeoutOrCancelledError(), AgentControlUncertain),
        (ValueError("private result decoding details"), AgentControlUncertain),
        (TimeoutError("private transport timeout"), AgentControlUncertain),
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
    client = Mock(get_workflow_handle_for=Mock(return_value=handle))
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
    client = Mock(get_workflow_handle_for=Mock(return_value=handle))
    with (
        patch("tracecat.agent.backends.base.get_temporal_client", return_value=client),
        patch(
            "tracecat.agent.backends.base.signal_turn_cancel",
            side_effect=RuntimeError,
        ),
    ):
        await DefaultBackend().cancel(run_id)
    client.get_workflow_handle_for.assert_called_once_with(
        DurableAgentWorkflow.run, f"agent/{run_id}"
    )
    assert (
        handle.execute_update.await_args.args[0] is DurableAgentWorkflow.request_cancel
    )


@pytest.mark.anyio
@pytest.mark.parametrize("operation", ["approvals", "cancel", "lifecycle"])
async def test_backend_resolves_one_client_and_handle_per_operation(operation):
    run_id = uuid4()
    handle = Mock(
        execute_update=AsyncMock(return_value=True),
        describe=AsyncMock(
            return_value=SimpleNamespace(status=WorkflowExecutionStatus.RUNNING)
        ),
    )
    client = Mock(get_workflow_handle_for=Mock(return_value=handle))
    backend = DefaultBackend()
    with (
        patch(
            "tracecat.agent.backends.base.get_temporal_client", return_value=client
        ) as connect,
        patch("tracecat.agent.backends.base.signal_turn_cancel"),
    ):
        match operation:
            case "approvals":
                await backend.submit_approvals(
                    run_id,
                    WorkflowApprovalSubmission(approvals={}, new_stream_id=uuid4()),
                )
            case "cancel":
                await backend.cancel(run_id)
            case "lifecycle":
                with patch.object(
                    backend,
                    "_run_control",
                    side_effect=AssertionError("read used control path"),
                ):
                    assert (
                        await backend.get_turn_lifecycle(run_id)
                        == TurnLifecycle.RUNNING
                    )
    connect.assert_awaited_once()
    client.get_workflow_handle_for.assert_called_once_with(
        DurableAgentWorkflow.run, f"agent/{run_id}"
    )


@pytest.mark.anyio
async def test_handle_construction_failure_is_definitive_before_submission():
    client = Mock(
        get_workflow_handle_for=Mock(side_effect=ValueError("invalid workflow"))
    )
    with patch("tracecat.agent.backends.base.get_temporal_client", return_value=client):
        with pytest.raises(AgentControlRejected) as caught:
            await DefaultBackend().submit_approvals(
                uuid4(), WorkflowApprovalSubmission(approvals={}, new_stream_id=uuid4())
            )
    assert caught.value.__context__ is None


@pytest.mark.anyio
async def test_backend_handle_reuses_supplied_client():
    run_id = uuid4()
    handle = Mock()
    client = Mock(get_workflow_handle_for=Mock(return_value=handle))
    with patch("tracecat.agent.backends.base.get_temporal_client") as connect:
        assert await DefaultBackend().handle(run_id, client=client) is handle
    connect.assert_not_called()
    client.get_workflow_handle_for.assert_called_once_with(
        DurableAgentWorkflow.run, f"agent/{run_id}"
    )
