"""Real PostgreSQL rollback coverage with the Temporal transport isolated.

Run against local PostgreSQL with:
uv run pytest --noconftest tests/backends/test_agent_dispatch_transaction.py
"""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from uuid import UUID, uuid4

import pytest
from google.protobuf import any_pb2
from google.protobuf.message import Message
from sqlalchemy import Table, func, select, text, update
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool
from sqlalchemy.schema import CreateTable
from temporalio.api.common.v1 import GrpcStatus
from temporalio.api.enums.v1 import WorkflowExecutionStatus as ExecutionStatusProto
from temporalio.api.enums.v1 import WorkflowIdReusePolicy
from temporalio.api.errordetails.v1 import WorkflowExecutionAlreadyStartedFailure
from temporalio.api.workflow.v1 import WorkflowExecutionInfo
from temporalio.api.workflowservice.v1 import (
    DescribeWorkflowExecutionResponse,
    StartWorkflowExecutionRequest,
    StartWorkflowExecutionResponse,
    TerminateWorkflowExecutionRequest,
    TerminateWorkflowExecutionResponse,
)
from temporalio.client import Client, WorkflowExecutionStatus
from temporalio.common import RetryPolicy, WorkflowIDReusePolicy
from temporalio.exceptions import WorkflowAlreadyStartedError
from temporalio.service import (
    ConnectConfig,
    RPCError,
    RPCStatusCode,
    ServiceClient,
    WorkflowService,
)

from tests.database import TEST_DB_CONFIG
from tracecat.agent.backends.default import DefaultBackend
from tracecat.agent.backends.schemas import AgentWorkflowArgs
from tracecat.agent.backends.types import (
    SessionDispatchUncertain,
    SessionTurnContext,
    SessionWorkflowContext,
)
from tracecat.agent.session.service import AgentSessionService
from tracecat.agent.types import AgentConfig
from tracecat.auth.types import Role
from tracecat.db.models import AgentSession, AgentSessionHistory
from tracecat.dsl._converter import get_data_converter
from tracecat.exceptions import TracecatConflictError

pytestmark = [pytest.mark.anyio, pytest.mark.dbtest]


@pytest.fixture
async def connection() -> AsyncIterator[AsyncConnection]:
    engine = create_async_engine(TEST_DB_CONFIG.sys_url, poolclass=NullPool)
    try:
        async with engine.connect() as connection:
            # Temporary ORM-shaped tables isolate every test from app data.
            # Workspace/user foreign keys are irrelevant to dispatch atomicity.
            for table in (AgentSession.__table__, AgentSessionHistory.__table__):
                assert isinstance(table, Table)
                ddl = str(
                    CreateTable(table, include_foreign_key_constraints=[]).compile(
                        dialect=engine.dialect
                    )
                ).replace("CREATE TABLE", "CREATE TEMPORARY TABLE", 1)
                await connection.execute(text(ddl))
            await connection.commit()
            yield connection
    finally:
        await engine.dispose()


class PreparationBackend(DefaultBackend):
    fail_preparation = False

    async def build_workflow_args(
        self, context: SessionWorkflowContext
    ) -> AgentWorkflowArgs:
        assert not hasattr(context, "db")
        assert not hasattr(context, "session")
        if self.fail_preparation:
            raise ValueError("preparation failed")
        return await super().build_workflow_args(context)


async def make_context(db: AsyncSession) -> SessionTurnContext:
    workspace_id = uuid4()
    session = AgentSession(
        workspace_id=workspace_id,
        title="Test chat",
        entity_type="copilot",
        entity_id=workspace_id,
        backend_id="oss",
        harness_type="claude_code",
        last_error="Previous turn failed",
    )
    db.add(session)
    await db.flush()
    db.add(
        AgentSessionHistory(
            workspace_id=workspace_id,
            session_id=session.id,
            content={"role": "assistant", "text": "Earlier message"},
        )
    )
    await db.commit()
    return SessionTurnContext(
        db=db,
        session=session,
        role=Role(
            type="service",
            service_id="tracecat-api",
            organization_id=uuid4(),
            workspace_id=workspace_id,
        ),
        config=AgentConfig(model_name="test", model_provider="anthropic"),
        prompt="Investigate this alert",
        run_id=uuid4(),
        stream_id=uuid4(),
    )


def temporal_client(rpc: AsyncMock) -> Client:
    service = Mock(spec=ServiceClient)
    service.config = ConnectConfig(target_host="localhost:7233", identity="test-client")
    service._rpc_call = rpc
    # Recovery's describe and tombstone calls use the process-wide client.
    service.workflow_service = WorkflowService(service)
    return Client(service, data_converter=get_data_converter())


class ScriptedTemporal:
    """Answer each Temporal RPC from a per-method script and record requests."""

    def __init__(self, **scripts: list[Message | Exception]) -> None:
        self.scripts = scripts
        self.starts: list[StartWorkflowExecutionRequest] = []
        self.terminations: list[TerminateWorkflowExecutionRequest] = []

    async def rpc(self, rpc: str, req: Message, *_args, **_kwargs) -> Message:
        match req:
            case StartWorkflowExecutionRequest():
                self.starts.append(req)
            case TerminateWorkflowExecutionRequest():
                self.terminations.append(req)
        outcome = self.scripts[rpc].pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def unavailable() -> RPCError:
    return RPCError("Temporal frontend paused", RPCStatusCode.UNAVAILABLE, b"")


def not_found() -> RPCError:
    return RPCError("workflow not found", RPCStatusCode.NOT_FOUND, b"")


def already_started() -> RPCError:
    detail = any_pb2.Any()
    detail.Pack(WorkflowExecutionAlreadyStartedFailure(run_id="original"))
    status = GrpcStatus(code=RPCStatusCode.ALREADY_EXISTS, details=[detail])
    return RPCError(
        "workflow already started",
        RPCStatusCode.ALREADY_EXISTS,
        status.SerializeToString(),
    )


async def lose_first_start(
    backend: DefaultBackend, connection: AsyncConnection, context: SessionTurnContext
) -> None:
    """Fail the turn's start ambiguously and check it keeps the session."""
    with pytest.raises(SessionDispatchUncertain):
        await backend.start_turn(context)
    await assert_reserved_by(connection, context.session.id, context.run_id)


async def age_reservation(
    db: AsyncSession,
    session_id: UUID,
    by: timedelta,
    *,
    reserved_at: bool = True,
    updated_at: bool = True,
) -> None:
    """Move the reservation's stamps back, as if time had passed.

    A stamp that is not aged here is left as is, except ``updated_at``, which
    the ORM sets to the transaction's now() on every update.
    """
    statement = update(AgentSession).where(AgentSession.id == session_id)
    if reserved_at:
        statement = statement.values(reserved_at=AgentSession.reserved_at - by)
    if updated_at:
        statement = statement.values(updated_at=AgentSession.updated_at - by)
    await db.execute(statement.execution_options(synchronize_session=False))
    await db.commit()


async def assert_reserved_by(
    connection: AsyncConnection, session_id: UUID, run_id: UUID | None
) -> AgentSession:
    async with AsyncSession(connection) as reader:
        saved = await reader.scalar(
            select(AgentSession).where(AgentSession.id == session_id)
        )
    assert saved is not None
    assert saved.curr_run_id == run_id
    assert (saved.active_stream_id is None) == (run_id is None)
    return saved


@pytest.mark.parametrize("failure", ["preparation", "encoding", "validation", "rpc"])
async def test_failed_start_preserves_history_and_retry_reserves_once(
    connection: AsyncConnection, failure: str
) -> None:
    backend = PreparationBackend()
    rpc = AsyncMock(return_value=StartWorkflowExecutionResponse(run_id="run-1"))
    client = temporal_client(rpc)
    async with AsyncSession(connection, expire_on_commit=False) as db:
        context = await make_context(db)
        session_id = context.session.id
        with patch(
            "tracecat.agent.backends.base.get_temporal_client", return_value=client
        ):
            with pytest.MonkeyPatch.context() as patcher:
                if failure == "preparation":
                    patcher.setattr(backend, "fail_preparation", True)
                elif failure == "encoding":
                    patcher.setattr(
                        "tracecat.dsl._converter.orjson.dumps",
                        Mock(side_effect=TypeError("invalid value")),
                    )
                elif failure == "validation":
                    # Retry-policy validation runs after payload encoding.
                    patcher.setattr(
                        backend, "retry_policy", RetryPolicy(maximum_attempts=-1)
                    )
                else:
                    rpc.side_effect = RPCError(
                        "Rejected", RPCStatusCode.PERMISSION_DENIED, b""
                    )
                message = "was rejected" if failure == "rpc" else "before dispatch"
                with pytest.raises(RuntimeError, match=message):
                    await backend.start_turn(context)
            if failure == "rpc":
                rpc.assert_awaited_once()
                rpc.reset_mock()
                rpc.side_effect = None
            else:
                rpc.assert_not_awaited()
            assert not db.in_transaction()

            # Read persisted state through a fresh ORM session after rollback.
            async with AsyncSession(connection) as reader:
                saved = await reader.scalar(
                    select(AgentSession).where(AgentSession.id == session_id)
                )
                assert saved is not None
                assert saved.curr_run_id is None
                assert saved.active_stream_id is None
                assert saved.sdk_session_id is None
                assert saved.last_error == (
                    None if failure == "rpc" else "Previous turn failed"
                )
                assert list(
                    await reader.scalars(select(AgentSessionHistory.content))
                ) == [{"role": "assistant", "text": "Earlier message"}]

            session = await db.scalar(
                select(AgentSession).where(AgentSession.id == session_id)
            )
            assert session is not None
            retry = replace(context, session=session, run_id=uuid4(), stream_id=uuid4())
            await backend.start_turn(retry)
            rpc.assert_awaited_once()

        async with AsyncSession(connection) as reader:
            saved = await reader.scalar(
                select(AgentSession).where(AgentSession.id == session_id)
            )
            assert saved is not None
            assert saved.curr_run_id == retry.run_id
            assert saved.active_stream_id == retry.stream_id
            assert saved.sdk_session_id is None
            rows = list(await reader.scalars(select(AgentSessionHistory)))
            assert len(rows) == 1
            assert rows[0].content == {"role": "assistant", "text": "Earlier message"}


async def test_uncertain_dispatch_keeps_history_and_blocks_another_turn(
    connection: AsyncConnection,
) -> None:
    backend = PreparationBackend()
    rpc = AsyncMock(side_effect=TimeoutError("lost acknowledgement"))
    client = temporal_client(rpc)
    async with AsyncSession(connection, expire_on_commit=False) as db:
        context = await make_context(db)
        session_id = context.session.id
        with patch(
            "tracecat.agent.backends.base.get_temporal_client", return_value=client
        ):
            with pytest.raises(SessionDispatchUncertain):
                await backend.start_turn(context)
            assert not db.in_transaction()
            async with AsyncSession(connection) as reader:
                saved = await reader.scalar(
                    select(AgentSession).where(AgentSession.id == session_id)
                )
                assert saved is not None
                assert saved.curr_run_id == context.run_id
                assert saved.active_stream_id == context.stream_id
                assert len(list(await reader.scalars(select(AgentSessionHistory)))) == 1

            describe = AsyncMock(side_effect=TimeoutError("lookup unavailable"))
            with (
                patch.object(backend, "handle", return_value=Mock(describe=describe)),
                pytest.raises(TracecatConflictError),
            ):
                await backend.start_turn(
                    replace(context, run_id=uuid4(), stream_id=uuid4())
                )
            describe.assert_awaited_once()
            rpc.assert_awaited_once()


@pytest.mark.parametrize(
    "status",
    [
        WorkflowExecutionStatus.COMPLETED,
        WorkflowExecutionStatus.FAILED,
        WorkflowExecutionStatus.CANCELED,
        WorkflowExecutionStatus.TERMINATED,
        WorkflowExecutionStatus.TIMED_OUT,
    ],
)
@pytest.mark.parametrize("preparation_fails", [False, True])
async def test_terminal_turn_recovery_persists_before_new_preparation(
    connection: AsyncConnection,
    status: WorkflowExecutionStatus,
    preparation_fails: bool,
) -> None:
    backend = PreparationBackend()
    backend.fail_preparation = preparation_fails
    rpc = AsyncMock(return_value=StartWorkflowExecutionResponse(run_id="run-2"))
    handle = Mock(describe=AsyncMock(return_value=SimpleNamespace(status=status)))
    async with AsyncSession(connection, expire_on_commit=False) as db:
        context = await make_context(db)
        session_id = context.session.id
        stale_run_id = uuid4()
        context.session.curr_run_id = stale_run_id
        context.session.active_stream_id = uuid4()
        context.session.last_error = None
        await db.commit()
        with (
            patch(
                "tracecat.agent.backends.base.get_temporal_client",
                return_value=temporal_client(rpc),
            ),
            patch.object(backend, "handle", return_value=handle) as resolve,
        ):
            if preparation_fails:
                with pytest.raises(RuntimeError, match="before dispatch"):
                    await backend.start_turn(context)
                rpc.assert_not_awaited()
            else:
                await backend.start_turn(context)
                rpc.assert_awaited_once()
        assert resolve.await_args is not None
        assert resolve.await_args.args == (stale_run_id,)
        async with AsyncSession(connection) as reader:
            saved = await reader.scalar(
                select(AgentSession).where(AgentSession.id == session_id)
            )
            assert saved is not None
            if preparation_fails:
                assert saved.curr_run_id is None
                assert saved.active_stream_id is None
                assert saved.last_error == (
                    "Previous agent turn ended before session cleanup completed"
                )
            else:
                assert saved.curr_run_id == context.run_id
                assert saved.active_stream_id == context.stream_id
                assert saved.last_error is None


@pytest.mark.parametrize(
    "cancel_at,outcome",
    [
        ("before_commit", "success"),
        ("after_commit", "success"),
        ("after_commit", "rejected"),
        ("after_commit", "uncertain"),
        ("rpc", "success"),
        ("rpc", "rejected"),
        ("before_commit", "commit_failure"),
    ],
)
async def test_caller_cancellation_settles_commit_dispatch_and_cleanup(
    connection: AsyncConnection, cancel_at: str, outcome: str
) -> None:
    backend = PreparationBackend()
    paused = asyncio.Event()
    proceed = asyncio.Event()

    async def pause() -> None:
        paused.set()
        await proceed.wait()

    async def start_rpc(*_args, **_kwargs):
        if cancel_at == "rpc":
            await pause()
        if outcome == "rejected":
            raise RPCError(
                "private rejection details", RPCStatusCode.INVALID_ARGUMENT, b""
            )
        if outcome == "uncertain":
            raise TimeoutError("private transport details")
        return StartWorkflowExecutionResponse(run_id="run-1")

    rpc = AsyncMock(side_effect=start_rpc)
    client = temporal_client(rpc)
    async with AsyncSession(connection, expire_on_commit=False) as db:
        context = await make_context(db)
        session_id = context.session.id
        commit = db.commit
        rollback = AsyncMock(wraps=db.rollback)
        first_commit = True

        async def commit_at_boundary() -> None:
            nonlocal first_commit
            if not first_commit:
                await commit()
                return
            first_commit = False
            if cancel_at == "before_commit":
                await pause()
            if outcome == "commit_failure":
                raise RuntimeError("private commit details")
            await commit()
            if cancel_at == "after_commit":
                # Ownership is already persisted, but the
                # commit await has not returned to TurnDispatchClient yet.
                await pause()

        with (
            patch(
                "tracecat.agent.backends.base.get_temporal_client", return_value=client
            ),
            patch.object(db, "commit", side_effect=commit_at_boundary),
            patch.object(db, "rollback", rollback),
            patch(
                "tracecat.agent.backends.base.get_async_session_context_manager",
                side_effect=lambda: AsyncSession(connection),
            ),
        ):
            turn = asyncio.create_task(backend.start_turn(context))
            try:
                await asyncio.wait_for(paused.wait(), timeout=5)
                for _ in range(2):
                    turn.cancel()
                    await asyncio.sleep(0)
                    assert not turn.done()
                rollback.assert_not_awaited()
            finally:
                proceed.set()
                with pytest.raises(asyncio.CancelledError) as caught:
                    await asyncio.wait_for(turn, timeout=5)
            assert caught.value.__context__ is None
            assert caught.value.__cause__ is None

        assert not db.in_transaction()
        if outcome == "commit_failure":
            rpc.assert_not_awaited()
            rollback.assert_awaited_once()
        else:
            rpc.assert_awaited_once()
            rollback.assert_not_awaited()
        async with AsyncSession(connection) as reader:
            saved = await reader.scalar(
                select(AgentSession).where(AgentSession.id == session_id)
            )
            assert saved is not None
            if outcome in {"success", "uncertain"}:
                assert saved.curr_run_id == context.run_id
                assert saved.active_stream_id == context.stream_id
            else:
                assert saved.curr_run_id is None
                assert saved.active_stream_id is None
            rows = list(await reader.scalars(select(AgentSessionHistory)))
            assert len(rows) == 1


@pytest.mark.parametrize(
    "mismatch", [None, "workspace_id", "curr_run_id", "active_stream_id"]
)
async def test_lost_commit_acknowledgement_releases_only_matching_reservation(
    connection: AsyncConnection, mismatch: str | None
) -> None:
    async with AsyncSession(connection, expire_on_commit=False) as db:
        context = await make_context(db)
        session_id = context.session.id
        commit = db.commit
        replacement_id = uuid4()

        async def commit_then_lose_acknowledgement() -> None:
            await commit()
            if mismatch is not None:
                # Simulate ownership changing before reconciliation.
                async with AsyncSession(connection) as writer:
                    await writer.execute(
                        update(AgentSession)
                        .where(AgentSession.id == session_id)
                        .values(**{mismatch: replacement_id})
                    )
                    await writer.commit()
            raise ConnectionError("lost commit acknowledgement")

        rpc = AsyncMock(return_value=StartWorkflowExecutionResponse(run_id="run-1"))
        with (
            patch(
                "tracecat.agent.backends.base.get_temporal_client",
                return_value=temporal_client(rpc),
            ),
            patch.object(db, "commit", side_effect=commit_then_lose_acknowledgement),
            patch(
                "tracecat.agent.backends.base.get_async_session_context_manager",
                side_effect=lambda: AsyncSession(connection),
            ) as fresh_session,
            pytest.raises(RuntimeError) as caught,
        ):
            await DefaultBackend().start_turn(context)
        assert isinstance(caught.value, SessionDispatchUncertain) == (
            mismatch is not None
        )
        fresh_session.assert_called_once()
        rpc.assert_not_awaited()
        async with AsyncSession(connection) as reader:
            saved = await reader.scalar(
                select(AgentSession).where(AgentSession.id == session_id)
            )
            assert saved is not None
            if mismatch is None:
                assert saved.curr_run_id is None
                assert saved.active_stream_id is None
            else:
                assert saved.curr_run_id is not None
                assert saved.active_stream_id is not None


@pytest.mark.parametrize(
    "mismatch", [None, "workspace_id", "curr_run_id", "active_stream_id"]
)
async def test_rpc_rejection_releases_only_matching_reservation(
    connection: AsyncConnection, mismatch: str | None
) -> None:
    backend = DefaultBackend()
    async with AsyncSession(connection, expire_on_commit=False) as db:
        context = await make_context(db)
        session_id = context.session.id
        # Another session with the same ownership values must remain untouched.
        other = AgentSession(
            workspace_id=context.role.workspace_id,
            title="Another chat",
            entity_type="copilot",
            entity_id=uuid4(),
            backend_id="oss",
            harness_type="claude_code",
            curr_run_id=context.run_id,
            active_stream_id=context.stream_id,
        )
        db.add(other)
        await db.commit()
        other_id = other.id

        async def reject(*_args, **_kwargs):
            if mismatch is not None:
                await db.execute(
                    update(AgentSession)
                    .where(AgentSession.id == session_id)
                    .values(**{mismatch: uuid4()})
                    .execution_options(synchronize_session=False)
                )
                await db.commit()
            raise RPCError("request rejected", RPCStatusCode.INVALID_ARGUMENT, b"")

        rpc = AsyncMock(side_effect=reject)
        client = temporal_client(rpc)
        with patch(
            "tracecat.agent.backends.base.get_temporal_client", return_value=client
        ):
            error = RuntimeError if mismatch is None else SessionDispatchUncertain
            message = "was rejected" if mismatch is None else "requires reconciliation"
            with pytest.raises(error, match=message):
                await backend.start_turn(context)
            assert not db.in_transaction()
            async with AsyncSession(connection) as reader:
                saved = await reader.scalar(
                    select(AgentSession).where(AgentSession.id == session_id)
                )
                assert saved is not None
                if mismatch is None:
                    assert saved.curr_run_id is None
                    assert saved.active_stream_id is None
                else:
                    assert saved.curr_run_id is not None
                    assert saved.active_stream_id is not None
                untouched = await reader.scalar(
                    select(AgentSession).where(AgentSession.id == other_id)
                )
                assert untouched is not None
                assert untouched.curr_run_id == context.run_id
                assert untouched.active_stream_id == context.stream_id
            rpc.assert_awaited_once()

            if mismatch is None:
                rpc.side_effect = None
                rpc.return_value = StartWorkflowExecutionResponse(run_id="run-2")
                retry = replace(context, run_id=uuid4(), stream_id=uuid4())
                await backend.start_turn(retry)
                async with AsyncSession(connection) as reader:
                    saved = await reader.scalar(
                        select(AgentSession).where(AgentSession.id == session_id)
                    )
                    assert saved is not None
                    assert saved.curr_run_id == retry.run_id
                    assert saved.active_stream_id == retry.stream_id


@pytest.mark.parametrize("preparation_fails", [False, True])
async def test_lost_start_is_released_without_replay_once_a_tombstone_claims_it(
    connection: AsyncConnection, preparation_fails: bool
) -> None:
    """A start lost to UNAVAILABLE is released, never replayed, once its ID is claimed."""
    backend = PreparationBackend()
    temporal = ScriptedTemporal(
        start_workflow_execution=[
            unavailable(),
            StartWorkflowExecutionResponse(run_id="tombstone"),
            StartWorkflowExecutionResponse(run_id="next"),
        ],
        describe_workflow_execution=[not_found()],
        terminate_workflow_execution=[TerminateWorkflowExecutionResponse()],
    )
    async with AsyncSession(connection, expire_on_commit=False) as db:
        context = await make_context(db)
        session_id = context.session.id
        next_turn = replace(context, run_id=uuid4(), stream_id=uuid4())
        with patch(
            "tracecat.agent.backends.base.get_temporal_client",
            return_value=temporal_client(AsyncMock(side_effect=temporal.rpc)),
        ):
            await lose_first_start(backend, connection, context)
            await age_reservation(db, session_id, timedelta(minutes=1))
            backend.fail_preparation = preparation_fails
            if preparation_fails:
                with pytest.raises(RuntimeError, match="before dispatch"):
                    await backend.start_turn(next_turn)
            else:
                await backend.start_turn(next_turn)

    original, tombstone, *dispatched = temporal.starts
    assert original.workflow_id == tombstone.workflow_id == f"agent/{context.run_id}"
    # Both reject duplicates, so the claim still binds once the tombstone closes.
    reject = WorkflowIdReusePolicy.WORKFLOW_ID_REUSE_POLICY_REJECT_DUPLICATE
    assert original.workflow_id_reuse_policy == reject
    assert tombstone.workflow_id_reuse_policy == reject
    assert tombstone.workflow_type.name == "AgentTurnStartTombstone"
    assert tombstone.task_queue.name == "agent-turn-start-tombstone"
    assert not tombstone.input.payloads
    (termination,) = temporal.terminations
    assert termination.workflow_execution.workflow_id == tombstone.workflow_id
    assert termination.first_execution_run_id == "tombstone"
    if preparation_fails:
        assert dispatched == []
        saved = await assert_reserved_by(connection, session_id, None)
        assert saved.last_error == "Agent turn could not be started"
        assert saved.reserved_at is None
    else:
        assert [start.workflow_id for start in dispatched] == [
            f"agent/{next_turn.run_id}"
        ]
        await assert_reserved_by(connection, session_id, next_turn.run_id)


async def test_lost_acknowledgement_keeps_the_session_until_the_original_ends(
    connection: AsyncConnection,
) -> None:
    """When the original start holds its ID, the session stays reserved until it ends."""
    backend = PreparationBackend()
    completed = DescribeWorkflowExecutionResponse(
        workflow_execution_info=WorkflowExecutionInfo(
            status=ExecutionStatusProto.WORKFLOW_EXECUTION_STATUS_COMPLETED
        )
    )
    temporal = ScriptedTemporal(
        start_workflow_execution=[
            unavailable(),
            already_started(),
            StartWorkflowExecutionResponse(run_id="next"),
        ],
        describe_workflow_execution=[not_found(), completed],
        terminate_workflow_execution=[],
    )
    async with AsyncSession(connection, expire_on_commit=False) as db:
        context = await make_context(db)
        session_id = context.session.id
        next_turn = replace(context, run_id=uuid4(), stream_id=uuid4())
        with patch(
            "tracecat.agent.backends.base.get_temporal_client",
            return_value=temporal_client(AsyncMock(side_effect=temporal.rpc)),
        ):
            await lose_first_start(backend, connection, context)
            await age_reservation(db, session_id, timedelta(minutes=1))
            with pytest.raises(TracecatConflictError):
                await backend.start_turn(next_turn)
            await assert_reserved_by(connection, session_id, context.run_id)
            # The existing terminal-status path releases the original once it ends.
            await backend.start_turn(next_turn)

    assert [start.workflow_type.name for start in temporal.starts] == [
        "DurableAgentWorkflow",
        "AgentTurnStartTombstone",
        "DurableAgentWorkflow",
    ]
    assert temporal.terminations == []
    await assert_reserved_by(connection, session_id, next_turn.run_id)


async def test_unknown_tombstone_outcome_keeps_the_session_and_next_send_retries(
    connection: AsyncConnection,
) -> None:
    """An ambiguous tombstone start keeps the session; the next send claims again."""
    backend = PreparationBackend()
    temporal = ScriptedTemporal(
        start_workflow_execution=[
            unavailable(),
            unavailable(),
            StartWorkflowExecutionResponse(run_id="tombstone"),
            StartWorkflowExecutionResponse(run_id="next"),
        ],
        describe_workflow_execution=[not_found(), not_found()],
        terminate_workflow_execution=[TerminateWorkflowExecutionResponse()],
    )
    async with AsyncSession(connection, expire_on_commit=False) as db:
        context = await make_context(db)
        session_id = context.session.id
        next_turn = replace(context, run_id=uuid4(), stream_id=uuid4())
        with patch(
            "tracecat.agent.backends.base.get_temporal_client",
            return_value=temporal_client(AsyncMock(side_effect=temporal.rpc)),
        ):
            await lose_first_start(backend, connection, context)
            await age_reservation(db, session_id, timedelta(minutes=1))
            with pytest.raises(TracecatConflictError):
                await backend.start_turn(next_turn)
            await assert_reserved_by(connection, session_id, context.run_id)
            assert temporal.terminations == []
            await backend.start_turn(next_turn)

    lost_id = f"agent/{context.run_id}"
    assert [start.workflow_id for start in temporal.starts] == [
        lost_id,
        lost_id,
        lost_id,
        f"agent/{next_turn.run_id}",
    ]
    assert len(temporal.terminations) == 1
    await assert_reserved_by(connection, session_id, next_turn.run_id)


@pytest.mark.parametrize("reserved_ago", [timedelta(0), timedelta(seconds=34)])
async def test_fresh_reservation_is_never_tombstoned(
    connection: AsyncConnection, reserved_ago: timedelta
) -> None:
    """A missing turn younger than its 30 s start deadline plus margin keeps the session."""
    backend = PreparationBackend()
    temporal = ScriptedTemporal(
        start_workflow_execution=[
            unavailable(),
            StartWorkflowExecutionResponse(run_id="tombstone"),
            StartWorkflowExecutionResponse(run_id="next"),
        ],
        describe_workflow_execution=[not_found(), not_found()],
        terminate_workflow_execution=[TerminateWorkflowExecutionResponse()],
    )
    async with AsyncSession(connection, expire_on_commit=False) as db:
        context = await make_context(db)
        session_id = context.session.id
        next_turn = replace(context, run_id=uuid4(), stream_id=uuid4())
        with patch(
            "tracecat.agent.backends.base.get_temporal_client",
            return_value=temporal_client(AsyncMock(side_effect=temporal.rpc)),
        ):
            await lose_first_start(backend, connection, context)
            await age_reservation(db, session_id, reserved_ago)
            # The first start may still be in flight, so a quick second send
            # gets today's conflict instead of tombstoning that turn.
            with pytest.raises(TracecatConflictError):
                await backend.start_turn(next_turn)
            assert len(temporal.starts) == 1
            await assert_reserved_by(connection, session_id, context.run_id)

            await age_reservation(db, session_id, timedelta(minutes=1))
            await backend.start_turn(next_turn)

    assert [start.workflow_type.name for start in temporal.starts] == [
        "DurableAgentWorkflow",
        "AgentTurnStartTombstone",
        "DurableAgentWorkflow",
    ]
    await assert_reserved_by(connection, session_id, next_turn.run_id)


async def test_reservation_is_stamped_at_commit_not_transaction_start(
    connection: AsyncConnection,
) -> None:
    """The start deadline counts from the reservation commit, not its transaction."""
    rpc = AsyncMock(return_value=StartWorkflowExecutionResponse(run_id="run-1"))
    async with AsyncSession(connection, expire_on_commit=False) as db:
        context = await make_context(db)
        session_id = context.session.id
        # Request preparation runs in the same transaction before dispatch.
        began = await db.scalar(select(func.now()))
        assert began is not None
        await asyncio.sleep(0.2)
        with patch(
            "tracecat.agent.backends.base.get_temporal_client",
            return_value=temporal_client(rpc),
        ):
            await PreparationBackend().start_turn(context)

    saved = await assert_reserved_by(connection, session_id, context.run_id)
    assert saved.reserved_at is not None
    assert saved.reserved_at - began >= timedelta(seconds=0.2)


@pytest.mark.parametrize("fresh", ["reserved_at", "updated_at"])
async def test_reservation_fresh_by_either_stamp_is_never_tombstoned(
    connection: AsyncConnection, fresh: str
) -> None:
    """A missing turn is tombstoned only once reserved_at and updated_at are both old.

    An old updated_at, such as a concurrent title update's transaction-start
    now(), cannot age a fresh reservation. A fresh updated_at covers writers that
    reserve without stamping reserved_at, like the agent runtime's handoff.
    """
    backend = PreparationBackend()
    temporal = ScriptedTemporal(
        start_workflow_execution=[unavailable()],
        describe_workflow_execution=[not_found()],
        terminate_workflow_execution=[],
    )
    async with AsyncSession(connection, expire_on_commit=False) as db:
        context = await make_context(db)
        session_id = context.session.id
        with patch(
            "tracecat.agent.backends.base.get_temporal_client",
            return_value=temporal_client(AsyncMock(side_effect=temporal.rpc)),
        ):
            await lose_first_start(backend, connection, context)
            await age_reservation(
                db,
                session_id,
                timedelta(minutes=1),
                reserved_at=fresh != "reserved_at",
                updated_at=fresh != "updated_at",
            )
            with pytest.raises(TracecatConflictError):
                await backend.start_turn(
                    replace(context, run_id=uuid4(), stream_id=uuid4())
                )

    assert len(temporal.starts) == 1
    await assert_reserved_by(connection, session_id, context.run_id)


async def test_caller_owned_reservation_stays_fresh_after_slow_preparation(
    connection: AsyncConnection,
) -> None:
    """prepare_new_turn stamps reserved_at at commit, so a direct send conflicts."""
    temporal = ScriptedTemporal(
        start_workflow_execution=[],
        describe_workflow_execution=[not_found()],
        terminate_workflow_execution=[],
    )
    async with AsyncSession(connection, expire_on_commit=False) as db:
        context = await make_context(db)
        session_id = context.session.id
        service = AgentSessionService(db, context.role)

        @asynccontextmanager
        async def agent_config(_session: AgentSession) -> AsyncIterator[AgentConfig]:
            yield context.config

        with (
            patch.object(
                service,
                "validate_turn_request",
                AsyncMock(return_value=context.session),
            ),
            patch.object(service, "_build_agent_config", agent_config),
        ):
            began = await db.scalar(select(func.now()))
            assert began is not None
            await asyncio.sleep(0.2)
            prepared = await service.prepare_new_turn(session_id, "Investigate")
        saved = await assert_reserved_by(connection, session_id, prepared.run_id)
        assert saved.reserved_at is not None
        assert saved.reserved_at - began >= timedelta(seconds=0.2)
        # Model preparation that took over the start deadline: its transaction,
        # and so updated_at, began long before the reservation committed.
        await age_reservation(db, session_id, timedelta(minutes=1), reserved_at=False)
        with (
            patch(
                "tracecat.agent.backends.base.get_temporal_client",
                return_value=temporal_client(AsyncMock(side_effect=temporal.rpc)),
            ),
            pytest.raises(TracecatConflictError),
        ):
            await PreparationBackend().start_turn(
                replace(context, run_id=uuid4(), stream_id=uuid4())
            )

    assert temporal.starts == []
    await assert_reserved_by(connection, session_id, prepared.run_id)


async def reserve_lost_turn(db: AsyncSession) -> tuple[SessionTurnContext, UUID]:
    """Commit a reservation whose start never reached Temporal and timed out."""
    context = await make_context(db)
    lost_run_id = uuid4()
    context.session.curr_run_id = lost_run_id
    context.session.active_stream_id = uuid4()
    context.session.last_error = None
    await db.commit()
    await age_reservation(db, context.session.id, timedelta(minutes=1))
    return context, lost_run_id


@pytest.mark.temporal
async def test_real_tombstone_rejects_a_delayed_start_of_the_lost_turn(
    connection: AsyncConnection, temporal_client: Client
) -> None:
    """On real Temporal, a claimed ID releases the session and rejects the original."""
    backend = PreparationBackend()
    # Admission is enough; never dispatch a real agent turn from this test.
    backend.fail_preparation = True
    queue = f"test-agent-turn-{uuid4()}"
    async with AsyncSession(connection, expire_on_commit=False) as db:
        context, lost_run_id = await reserve_lost_turn(db)
        session_id = context.session.id
        with (
            patch(
                "tracecat.agent.backends.base.get_temporal_client",
                return_value=temporal_client,
            ),
            pytest.raises(RuntimeError, match="before dispatch"),
        ):
            await backend.start_turn(context)

    saved = await assert_reserved_by(connection, session_id, None)
    assert saved.last_error == "Agent turn could not be started"
    lost_id = backend.workflow_id(lost_run_id)
    description = await temporal_client.get_workflow_handle(lost_id).describe()
    assert description.workflow_type == "AgentTurnStartTombstone"
    assert description.status == WorkflowExecutionStatus.TERMINATED
    with pytest.raises(WorkflowAlreadyStartedError):
        await temporal_client.start_workflow(
            "DurableAgentWorkflow",
            id=lost_id,
            task_queue=queue,
            id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE,
        )
    # The closed claim binds only REJECT_DUPLICATE starts, so every turn uses it.
    duplicate = await temporal_client.start_workflow(
        "DurableAgentWorkflow",
        id=lost_id,
        task_queue=queue,
        id_reuse_policy=WorkflowIDReusePolicy.ALLOW_DUPLICATE,
    )
    await duplicate.terminate(reason="test cleanup")


@pytest.mark.temporal
async def test_real_tombstone_never_displaces_an_original_that_landed(
    connection: AsyncConnection, temporal_client: Client
) -> None:
    """On real Temporal, an original that landed after describe keeps the session."""
    backend = PreparationBackend()
    queue = f"test-agent-turn-{uuid4()}"
    async with AsyncSession(connection, expire_on_commit=False) as db:
        context, lost_run_id = await reserve_lost_turn(db)
        session_id = context.session.id
        original = await temporal_client.start_workflow(
            "DurableAgentWorkflow",
            id=backend.workflow_id(lost_run_id),
            task_queue=queue,
            id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE,
        )
        try:
            # Describe ran just before the original start landed.
            missing = Mock(describe=AsyncMock(side_effect=not_found()))
            with (
                patch(
                    "tracecat.agent.backends.base.get_temporal_client",
                    return_value=temporal_client,
                ),
                patch.object(backend, "handle", return_value=missing),
                pytest.raises(TracecatConflictError),
            ):
                await backend.start_turn(
                    replace(context, run_id=uuid4(), stream_id=uuid4())
                )
            await assert_reserved_by(connection, session_id, lost_run_id)
            description = await original.describe()
            assert description.workflow_type == "DurableAgentWorkflow"
            assert description.status == WorkflowExecutionStatus.RUNNING
        finally:
            await original.terminate(reason="test cleanup")
