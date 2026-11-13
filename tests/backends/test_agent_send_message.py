"""send_message admission: one run per request ID across crashes and retries.

Real PostgreSQL with Temporal replaced by an in-memory model of workflow IDs:
starts reject a taken ID, describe reports type and status, terminate closes.
"""

from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from contextlib import asynccontextmanager, contextmanager, nullcontext
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any, Literal
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

import pytest
from google.protobuf.message import Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession
from temporalio.api.common.v1 import WorkflowType
from temporalio.api.enums.v1 import WorkflowExecutionStatus as Status
from temporalio.api.workflow.v1 import WorkflowExecutionInfo
from temporalio.api.workflowservice.v1 import (
    DescribeWorkflowExecutionRequest,
    DescribeWorkflowExecutionResponse,
    StartWorkflowExecutionRequest,
    StartWorkflowExecutionResponse,
    TerminateWorkflowExecutionRequest,
    TerminateWorkflowExecutionResponse,
)
from temporalio.service import RPCError, RPCStatusCode

from tests.backends.test_agent_dispatch_transaction import (
    age_reservation,
    already_started,
    assert_reserved_by,
    make_context,
    not_found,
    temporal_client,
    unavailable,
)
from tracecat.agent.backends.default import DefaultBackend
from tracecat.agent.backends.schemas import AgentWorkflowArgs
from tracecat.agent.backends.types import (
    AgentControlRejected,
    SessionDispatchUncertain,
    SessionTurnActiveError,
    SessionTurnContext,
)
from tracecat.agent.session.results import (
    MessageResult,
    RunAlreadyFinished,
    RunBusy,
    RunStarted,
    RunSteered,
    RunStopping,
    RunSuperseded,
)
from tracecat.agent.session.service import AgentSessionService, message_run_id
from tracecat.agent.session.types import TurnLifecycle, TurnLifecycleResult
from tracecat.agent.types import AgentConfig
from tracecat.db.models import AgentSession
from tracecat.dsl._converter import get_data_converter
from tracecat.exceptions import TracecatConflictError, TracecatValidationError

pytestmark = [pytest.mark.anyio, pytest.mark.dbtest]

AGENT_WORKFLOW = "DurableAgentWorkflow"
TOMBSTONE_WORKFLOW = "AgentTurnStartTombstone"


@dataclass
class FakeTemporal:
    """Workflow executions by ID; `fault` breaks the next start once."""

    executions: dict[str, tuple[str, Status.ValueType]] = field(default_factory=dict)
    starts: list[str] = field(default_factory=list)
    # lost: never reaches Temporal. ack_lost: starts, but the answer is lost.
    fault: Literal["lost", "ack_lost"] | None = None
    # Runs as a start arrives, before Temporal handles it.
    on_start: Callable[[StartWorkflowExecutionRequest], Awaitable[None]] | None = None

    async def rpc(self, rpc: str, req: Message, *_args, **_kwargs) -> Message:
        match req:
            case StartWorkflowExecutionRequest():
                if self.on_start is not None:
                    await self.on_start(req)
                self.starts.append(req.workflow_id)
                fault, self.fault = self.fault, None
                if fault == "lost":
                    raise unavailable()
                if req.workflow_id in self.executions:
                    raise already_started()
                self.executions[req.workflow_id] = (
                    req.workflow_type.name,
                    Status.WORKFLOW_EXECUTION_STATUS_RUNNING,
                )
                if fault == "ack_lost":
                    raise unavailable()
                return StartWorkflowExecutionResponse(run_id=f"run-{len(self.starts)}")
            case DescribeWorkflowExecutionRequest():
                if (found := self.executions.get(req.execution.workflow_id)) is None:
                    raise not_found()
                workflow_type, status = found
                return DescribeWorkflowExecutionResponse(
                    workflow_execution_info=WorkflowExecutionInfo(
                        type=WorkflowType(name=workflow_type), status=status
                    )
                )
            case TerminateWorkflowExecutionRequest():
                workflow_id = req.workflow_execution.workflow_id
                self.executions[workflow_id] = (
                    self.executions[workflow_id][0],
                    Status.WORKFLOW_EXECUTION_STATUS_TERMINATED,
                )
                return TerminateWorkflowExecutionResponse()
        raise AssertionError(f"Unexpected Temporal call: {rpc}")

    def agent_runs(self) -> list[str]:
        return [
            workflow_id
            for workflow_id, (workflow_type, _) in self.executions.items()
            if workflow_type == AGENT_WORKFLOW
        ]

    def complete(self, run_id: UUID) -> None:
        self.executions[f"agent/{run_id}"] = (
            AGENT_WORKFLOW,
            Status.WORKFLOW_EXECUTION_STATUS_COMPLETED,
        )


def message(
    session_id: UUID, request_id: str = "slack:Ev1", **extra: Any
) -> dict[str, Any]:
    """Keyword arguments for the send, start and steer message verbs."""
    return {
        "session_id": session_id,
        "request_id": request_id,
        "text": "Investigate this alert",
        **extra,
    }


@contextmanager
def temporal(fake: FakeTemporal) -> Iterator[None]:
    with patch(
        "tracecat.agent.backends.base.get_temporal_client",
        return_value=temporal_client(AsyncMock(side_effect=fake.rpc)),
    ):
        yield


@contextmanager
def steering(steer: AsyncMock) -> Iterator[None]:
    """Let the default backend steer, through ``steer``."""
    with (
        patch.object(DefaultBackend, "steering", True),
        patch.object(DefaultBackend, "steer", steer),
    ):
        yield


@asynccontextmanager
async def channel_service(
    db: AsyncSession, context: SessionTurnContext
) -> AsyncIterator[AgentSessionService]:
    """The real admission path; config, entitlement and approvals are stubbed."""
    service = AgentSessionService(db, context.role)

    @asynccontextmanager
    async def agent_config(_session: AgentSession) -> AsyncIterator[AgentConfig]:
        yield context.config

    with (
        patch(
            "tracecat.agent.session.service.require_session_backend_entitlement",
            AsyncMock(),
        ),
        patch.object(service, "has_pending_approvals", AsyncMock(return_value=False)),
        patch.object(service, "_build_agent_config", agent_config),
        patch.object(
            service, "is_first_prompt_for_session", AsyncMock(return_value=False)
        ),
    ):
        yield service


async def release(db: AsyncSession, session_id: UUID) -> None:
    """Clear the turn pointers, as the finished run's finalize does."""
    session = await db.scalar(select(AgentSession).where(AgentSession.id == session_id))
    assert session is not None
    session.curr_run_id = None
    session.active_stream_id = None
    session.reserved_run_id = None
    session.reserved_at = None
    await db.commit()


@pytest.mark.parametrize("fault", ["lost", "ack_lost"])
async def test_crash_before_the_start_then_retry_runs_one_turn(
    connection: AsyncConnection,
    fault: Literal["lost", "ack_lost"],
) -> None:
    fake = FakeTemporal(fault=fault)
    async with AsyncSession(connection, expire_on_commit=False) as db:
        context = await make_context(db)
        session_id = context.session.id
        run_id = message_run_id(session_id, "slack:Ev1", 0)
        with temporal(fake):
            async with channel_service(db, context) as service:
                with pytest.raises(SessionDispatchUncertain):
                    await service.send_message(**message(session_id))
                reserved = await assert_reserved_by(connection, session_id, run_id)
                # Slack retries the unacknowledged event.
                retried = await service.send_message(**message(session_id))

    assert retried == RunStarted(run_id=run_id)
    assert fake.agent_runs() == [f"agent/{run_id}"]
    # A lost start is re-issued on the reservation's own stream; a landed one is not.
    expected_starts = 2 if fault == "lost" else 1
    assert fake.starts == [f"agent/{run_id}"] * expected_starts
    saved = await assert_reserved_by(connection, session_id, run_id)
    assert saved.active_stream_id == reserved.active_stream_id


@pytest.mark.parametrize("when", ["during", "after"])
async def test_retry_during_or_after_the_run_starts_no_second_turn(
    connection: AsyncConnection,
    when: Literal["during", "after"],
) -> None:
    fake = FakeTemporal()
    async with AsyncSession(connection, expire_on_commit=False) as db:
        context = await make_context(db)
        session_id = context.session.id
        with temporal(fake):
            async with channel_service(db, context) as service:
                first = await service.send_message(**message(session_id))
                assert isinstance(first, RunStarted)
                if when == "after":
                    fake.complete(first.run_id)
                    await release(db, session_id)
                retried = await service.send_message(**message(session_id))

    # Accepted, not busy, and never started twice.
    assert retried == first
    assert fake.starts == [f"agent/{first.run_id}"]


async def test_a_slow_first_start_keeps_the_reservation_of_the_retry_that_won(
    connection: AsyncConnection,
) -> None:
    """The retry re-issues while the first start is in flight and lands first."""
    fake = FakeTemporal()
    retried: list[MessageResult] = []
    async with (
        AsyncSession(connection, expire_on_commit=False) as db,
        AsyncSession(connection, expire_on_commit=False) as retry_db,
    ):
        context = await make_context(db)
        session_id = context.session.id
        run_id = message_run_id(session_id, "slack:Ev1", 0)
        async with (
            channel_service(db, context) as service,
            channel_service(retry_db, context) as retry_service,
        ):

            async def retry_mid_start(_req: StartWorkflowExecutionRequest) -> None:
                fake.on_start = None
                retried.append(await retry_service.send_message(**message(session_id)))

            fake.on_start = retry_mid_start
            with temporal(fake):
                first = await service.send_message(**message(session_id))
                reserved = await assert_reserved_by(connection, session_id, run_id)

    assert retried == [RunStarted(run_id=run_id)]
    assert first == RunStarted(run_id=run_id)
    assert fake.agent_runs() == [f"agent/{run_id}"]
    # The running turn still owns the session, on the stream it writes to.
    assert reserved.active_stream_id is not None


async def test_duplicate_start_after_a_racing_check_is_already_accepted(
    connection: AsyncConnection,
) -> None:
    """Temporal's duplicate rejection settles a retry that read the ID as free."""
    fake = FakeTemporal()
    async with AsyncSession(connection, expire_on_commit=False) as db:
        context = await make_context(db)
        session_id = context.session.id
        run_id = message_run_id(session_id, "slack:Ev1", 0)
        with temporal(fake):
            async with channel_service(db, context) as service:
                await service.send_message(**message(session_id))
                fake.complete(run_id)
                await release(db, session_id)
                # Hide the run from the first check only, as a race would.
                taken = fake.executions.pop(f"agent/{run_id}")

                async def reveal(req: StartWorkflowExecutionRequest) -> None:
                    fake.on_start = None
                    fake.executions[req.workflow_id] = taken

                fake.on_start = reveal
                retried = await service.send_message(**message(session_id))
                # The retry's reservation names the closed run until the next
                # message's recovery releases it.
                await assert_reserved_by(connection, session_id, run_id)
                following = await service.send_message(
                    **message(session_id, "slack:Ev2")
                )

    assert retried == RunStarted(run_id=run_id)
    next_run_id = message_run_id(session_id, "slack:Ev2", 0)
    assert following == RunStarted(run_id=next_run_id)
    assert fake.agent_runs() == [f"agent/{run_id}", f"agent/{next_run_id}"]


async def test_tombstoned_run_is_replaced_by_the_next_attempt(
    connection: AsyncConnection,
) -> None:
    fake = FakeTemporal(fault="lost")
    async with AsyncSession(connection, expire_on_commit=False) as db:
        context = await make_context(db)
        session_id = context.session.id
        lost = message_run_id(session_id, "slack:Ev1", 0)
        with temporal(fake):
            async with channel_service(db, context) as service:
                with pytest.raises(SessionDispatchUncertain):
                    await service.send_message(**message(session_id))
                await age_reservation(db, session_id, timedelta(minutes=1))
                # Another message recovers the session: it tombstones the lost run.
                other = await service.send_message(**message(session_id, "slack:Ev2"))
                assert isinstance(other, RunStarted)
                fake.complete(other.run_id)
                await release(db, session_id)
                retried = await service.send_message(**message(session_id))

    replacement = message_run_id(session_id, "slack:Ev1", 1)
    assert retried == RunStarted(run_id=replacement)
    assert fake.executions[f"agent/{lost}"][0] == TOMBSTONE_WORKFLOW
    assert sorted(fake.agent_runs()) == sorted(
        [f"agent/{other.run_id}", f"agent/{replacement}"]
    )


async def test_instructions_reach_every_start_of_the_turn(
    connection: AsyncConnection,
) -> None:
    """A lost start's re-issue carries the same instructions; the text stays clean."""
    starts: list[StartWorkflowExecutionRequest] = []

    async def record(req: StartWorkflowExecutionRequest) -> None:
        starts.append(req)

    fake = FakeTemporal(fault="lost", on_start=record)
    async with AsyncSession(connection, expire_on_commit=False) as db:
        context = await make_context(db)
        assert not context.config.instructions
        sent = message(
            context.session.id,
            instructions="This message came from Slack, from Dana.",
        )
        with temporal(fake):
            async with channel_service(db, context) as service:
                with pytest.raises(SessionDispatchUncertain):
                    await service.send_message(**sent)
                assert isinstance(await service.send_message(**sent), RunStarted)

    assert len(starts) == 2
    for req in starts:
        (args,) = await get_data_converter().decode(
            list(req.input.payloads), [AgentWorkflowArgs]
        )
        assert args.agent_args.config.instructions == sent["instructions"]
        assert args.agent_args.user_prompt == "Investigate this alert"


async def test_message_while_another_run_is_going_is_busy_on_claude(
    connection: AsyncConnection,
) -> None:
    fake = FakeTemporal()
    async with AsyncSession(connection, expire_on_commit=False) as db:
        context = await make_context(db)
        session_id = context.session.id
        with temporal(fake):
            async with channel_service(db, context) as service:
                first = await service.send_message(**message(session_id))
                assert isinstance(first, RunStarted)
                busy = await service.send_message(**message(session_id, "slack:Ev2"))

    assert busy == RunBusy(run_id=first.run_id)
    assert fake.starts == [f"agent/{first.run_id}"]
    await assert_reserved_by(connection, session_id, first.run_id)


async def test_stop_targets_only_the_expected_run(
    connection: AsyncConnection,
) -> None:
    fake = FakeTemporal()

    async with AsyncSession(connection, expire_on_commit=False) as db:
        context = await make_context(db)
        session_id = context.session.id
        with (
            temporal(fake),
            patch.object(DefaultBackend, "cancel", AsyncMock()) as cancel,
        ):
            async with channel_service(db, context) as service:
                first = await service.send_message(**message(session_id))
                assert isinstance(first, RunStarted)
                stale_run_id = uuid4()
                stale = await service.stop_run(session_id, run_id=stale_run_id)
                stopping = await service.stop_run(session_id, run_id=first.run_id)
                fake.complete(first.run_id)
                finished = await service.stop_run(session_id, run_id=first.run_id)

    assert stale == RunSuperseded(current_run_id=first.run_id)
    assert stopping == RunStopping(run_id=first.run_id)
    assert finished == RunAlreadyFinished(run_id=first.run_id)
    cancel.assert_awaited_once_with(first.run_id)


def test_run_ids_are_stable_per_request_and_attempt() -> None:
    session_id = uuid4()
    ids = {
        message_run_id(session_id, request_id, attempt)
        for request_id in ("slack:Ev1", "slack:Ev2")
        for attempt in range(3)
    }
    assert len(ids) == 6
    assert message_run_id(session_id, "slack:Ev1", 0) == message_run_id(
        session_id, "slack:Ev1", 0
    )
    assert message_run_id(uuid4(), "slack:Ev1", 0) != message_run_id(
        session_id, "slack:Ev1", 0
    )


@pytest.mark.parametrize(
    ("error", "raised"),
    [
        (unavailable(), SessionDispatchUncertain),
        (RPCError("denied", RPCStatusCode.PERMISSION_DENIED, b""), RPCError),
    ],
    ids=["transient", "permanent"],
)
async def test_only_a_transient_describe_failure_is_retryable(
    error: RPCError, raised: type[Exception]
) -> None:
    client = temporal_client(AsyncMock(side_effect=error))
    with patch("tracecat.agent.backends.base.get_temporal_client", return_value=client):
        with pytest.raises(raised) as caught:
            await DefaultBackend().turn_start_state(uuid4())
    assert type(caught.value) is raised


@pytest.mark.parametrize(
    ("steer", "result"),
    [
        (AsyncMock(return_value=True), RunSteered),
        (AsyncMock(return_value=False), RunBusy),
        (AsyncMock(side_effect=AgentControlRejected("refused")), RunBusy),
        (None, RunBusy),
    ],
    ids=["steered", "declined", "refused", "cannot-steer"],
)
async def test_a_message_to_a_running_turn_steers_or_is_busy(
    connection: AsyncConnection, steer: AsyncMock | None, result: type[MessageResult]
) -> None:
    """The message's run id is the steer id; a backend that cannot steer is busy."""
    fake = FakeTemporal()
    async with AsyncSession(connection, expire_on_commit=False) as db:
        context = await make_context(db)
        session_id = context.session.id
        with temporal(fake), steering(steer) if steer else nullcontext():
            async with channel_service(db, context) as service:
                first = await service.start_message(**message(session_id))
                outcome = await service.steer_message(
                    **message(session_id, "slack:Ev2")
                )

    assert outcome == result(run_id=first.run_id)
    assert fake.starts == [f"agent/{first.run_id}"]
    if steer is not None:
        steer.assert_awaited_once_with(
            first.run_id,
            message_run_id(session_id, "slack:Ev2", 0),
            "Investigate this alert",
        )


@pytest.mark.parametrize("request_id", ["", "  "])
async def test_a_blank_request_id_is_refused(
    connection: AsyncConnection, request_id: str
) -> None:
    fake = FakeTemporal()
    async with AsyncSession(connection, expire_on_commit=False) as db:
        context = await make_context(db)
        with temporal(fake), pytest.raises(TracecatValidationError, match="blank"):
            async with channel_service(db, context) as service:
                await service.send_message(
                    **message(context.session.id, request_id=request_id)
                )
    assert fake.starts == []


async def test_start_reports_the_active_run_and_steers_nothing(
    connection: AsyncConnection,
) -> None:
    fake = FakeTemporal()
    steer = AsyncMock(return_value=True)
    async with AsyncSession(connection, expire_on_commit=False) as db:
        context = await make_context(db)
        session_id = context.session.id
        with temporal(fake), steering(steer):
            async with channel_service(db, context) as service:
                first = await service.start_message(**message(session_id))
                with pytest.raises(SessionTurnActiveError) as active:
                    await service.start_message(**message(session_id, "slack:Ev2"))

    assert active.value.run_id == first.run_id
    assert fake.starts == [f"agent/{first.run_id}"]
    steer.assert_not_awaited()


async def test_steer_of_a_started_message_returns_its_run_and_steers_nothing(
    connection: AsyncConnection,
) -> None:
    """A retry through the other verb gets the first verb's outcome."""
    fake = FakeTemporal()
    steer = AsyncMock(return_value=True)
    async with AsyncSession(connection, expire_on_commit=False) as db:
        context = await make_context(db)
        session_id = context.session.id
        with temporal(fake), steering(steer):
            async with channel_service(db, context) as service:
                first = await service.start_message(**message(session_id))
                fake.complete(first.run_id)
                await release(db, session_id)
                newer = await service.start_message(**message(session_id, "slack:Ev2"))
                retried = await service.steer_message(**message(session_id))

    assert retried == first
    steer.assert_not_awaited()
    assert fake.starts == [f"agent/{first.run_id}", f"agent/{newer.run_id}"]


@pytest.mark.parametrize("seen_by", ["pre-check", "lock"])
async def test_a_duplicate_whose_run_ran_meanwhile_is_never_steered(
    connection: AsyncConnection, seen_by: Literal["pre-check", "lock"]
) -> None:
    """The duplicate read its run as free; the original then ran and finished,
    and a newer message's run holds the session when the duplicate starts."""
    fake = FakeTemporal()
    steer = AsyncMock(return_value=True)
    async with AsyncSession(connection, expire_on_commit=False) as db:
        context = await make_context(db)
        session_id = context.session.id
        with temporal(fake), steering(steer):
            async with channel_service(db, context) as service:
                first = await service.send_message(**message(session_id))
                fake.complete(first.run_id)
                await release(db, session_id)
                newer = await service.send_message(**message(session_id, "slack:Ev2"))
                assert isinstance(newer, RunStarted)
                # On the lock path the newer run took the session after the pre-check.
                pre_check_misses = (
                    patch.object(
                        service,
                        "get_turn_lifecycle",
                        AsyncMock(
                            return_value=TurnLifecycleResult(TurnLifecycle.NONE, None)
                        ),
                    )
                    if seen_by == "lock"
                    else nullcontext()
                )
                with (
                    pre_check_misses,
                    patch.object(
                        DefaultBackend,
                        "turn_start_state",
                        AsyncMock(side_effect=["missing", "started"]),
                    ),
                ):
                    duplicate = await service.send_message(**message(session_id))

    assert duplicate == first
    steer.assert_not_awaited()
    assert fake.starts == [f"agent/{first.run_id}", f"agent/{newer.run_id}"]


def tombstone(fake: FakeTemporal, run_id: UUID) -> None:
    """Close ``run_id``'s workflow ID as recovery does for a lost start."""
    fake.executions[f"agent/{run_id}"] = (
        TOMBSTONE_WORKFLOW,
        Status.WORKFLOW_EXECUTION_STATUS_COMPLETED,
    )


async def test_a_start_raced_after_a_tombstone_settles_on_the_replacement(
    connection: AsyncConnection,
) -> None:
    """Attempt 0 is tombstoned; attempt 1 read as free is taken before its start."""
    fake = FakeTemporal()
    async with AsyncSession(connection, expire_on_commit=False) as db:
        context = await make_context(db)
        session_id = context.session.id
        tombstone(fake, message_run_id(session_id, "slack:Ev1", 0))
        replacement = message_run_id(session_id, "slack:Ev1", 1)

        async def taken_meanwhile(req: StartWorkflowExecutionRequest) -> None:
            fake.on_start = None
            fake.executions[req.workflow_id] = (
                AGENT_WORKFLOW,
                Status.WORKFLOW_EXECUTION_STATUS_RUNNING,
            )

        fake.on_start = taken_meanwhile
        with temporal(fake):
            async with channel_service(db, context) as service:
                started = await service.start_message(**message(session_id))

    assert started == RunStarted(run_id=replacement)
    assert fake.starts == [f"agent/{replacement}"]


async def test_a_steer_after_a_lost_start_uses_the_replacement_run_as_steer_id(
    connection: AsyncConnection,
) -> None:
    fake = FakeTemporal()
    steer = AsyncMock(return_value=True)
    async with AsyncSession(connection, expire_on_commit=False) as db:
        context = await make_context(db)
        session_id = context.session.id
        tombstone(fake, message_run_id(session_id, "slack:Ev2", 0))
        with temporal(fake), steering(steer):
            async with channel_service(db, context) as service:
                first = await service.start_message(**message(session_id))
                steered = await service.steer_message(
                    **message(session_id, "slack:Ev2")
                )

    assert steered == RunSteered(run_id=first.run_id)
    steer.assert_awaited_once_with(
        first.run_id,
        message_run_id(session_id, "slack:Ev2", 1),
        "Investigate this alert",
    )


@pytest.mark.parametrize(
    ("released", "lifecycle"),
    [(True, TurnLifecycle.NONE), (False, TurnLifecycle.COMPLETED)],
    ids=["released", "finished"],
)
async def test_steer_with_no_running_turn_is_the_control_conflict(
    connection: AsyncConnection, released: bool, lifecycle: TurnLifecycle
) -> None:
    fake = FakeTemporal()
    steer = AsyncMock(return_value=True)
    async with AsyncSession(connection, expire_on_commit=False) as db:
        context = await make_context(db)
        session_id = context.session.id
        with temporal(fake), steering(steer):
            async with channel_service(db, context) as service:
                first = await service.start_message(**message(session_id))
                fake.complete(first.run_id)
                if released:
                    await release(db, session_id)
                with pytest.raises(
                    TracecatConflictError, match="does not have an active turn"
                ) as conflict:
                    await service.steer_message(**message(session_id, "slack:Ev2"))

    assert conflict.value.detail == {"lifecycle": lifecycle.value}
    steer.assert_not_awaited()


async def test_steer_never_reaches_a_run_that_released_the_session(
    connection: AsyncConnection,
) -> None:
    """The loaded row still names a run that released the session while its
    workflow closes; a newer run, started elsewhere, now holds the session."""
    fake = FakeTemporal()
    steer = AsyncMock(return_value=True)
    async with (
        AsyncSession(connection, expire_on_commit=False) as db,
        AsyncSession(connection, expire_on_commit=False) as other_db,
    ):
        context = await make_context(db)
        session_id = context.session.id
        with temporal(fake), steering(steer):
            async with (
                channel_service(db, context) as service,
                channel_service(other_db, context) as other_service,
            ):
                first = await service.start_message(**message(session_id))
                await release(other_db, session_id)
                newer = await other_service.start_message(
                    **message(session_id, "slack:Ev2")
                )
                steered = await service.steer_message(
                    **message(session_id, "slack:Ev3")
                )

    assert steered == RunSteered(run_id=newer.run_id)
    steer.assert_awaited_once_with(
        newer.run_id,
        message_run_id(session_id, "slack:Ev3", 0),
        "Investigate this alert",
    )
    assert first.run_id != newer.run_id


async def test_send_is_busy_when_the_holder_finishes_before_the_steer(
    connection: AsyncConnection,
) -> None:
    fake = FakeTemporal()
    async with AsyncSession(connection, expire_on_commit=False) as db:
        context = await make_context(db)
        session_id = context.session.id
        with temporal(fake):
            async with channel_service(db, context) as service:
                first = await service.send_message(**message(session_id))
                assert isinstance(first, RunStarted)
                start = service.start_message

                async def holder_finishes_after_refusing(
                    session_id: UUID, **kwargs: Any
                ) -> RunStarted:
                    try:
                        return await start(session_id, **kwargs)
                    except SessionTurnActiveError as e:
                        fake.complete(e.run_id)
                        await release(db, session_id)
                        raise

                with patch.object(
                    service, "start_message", holder_finishes_after_refusing
                ):
                    sent = await service.send_message(
                        **message(session_id, "slack:Ev2")
                    )

    assert sent == RunBusy(run_id=first.run_id)
    assert fake.starts == [f"agent/{first.run_id}"]
