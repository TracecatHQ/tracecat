"""Per-click approval decisions: submitted to the run, then recorded on the row."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Sequence
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import delete, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession
from temporalio.client import WorkflowUpdateFailedError
from temporalio.exceptions import ApplicationError
from tracecat_ee.agent.activities import (
    PersistApprovalsActivityInputs,
    ToolApprovalPayload,
)
from tracecat_ee.agent.approvals.service import ApprovalManager, ApprovalService
from tracecat_ee.agent.context import AgentContext

from tests.unit.test_agent_session_approval_sink import (
    _ApprovalContinuationRedis,
    _mock_approval_continuation_dependencies,
)
from tracecat.agent.approvals import submission as submission_module
from tracecat.agent.approvals.decisions import decision_outcome
from tracecat.agent.approvals.enums import ApprovalStatus
from tracecat.agent.approvals.submission import (
    ApprovalSubmissionService,
    _approval_continuation_stream_id,
)
from tracecat.agent.backends.base import AgentBackend
from tracecat.agent.backends.schemas import WorkflowApprovalSubmission
from tracecat.agent.backends.types import AgentControlUncertain
from tracecat.agent.channel_publisher import (
    ApprovalPending,
    RunUpdate,
    approval_batch_id,
)
from tracecat.agent.common.stream_types import ToolCallContent
from tracecat.agent.session.results import (
    DecisionRecorded,
    DecisionRejected,
    RunRef,
)
from tracecat.agent.session.service import AgentSessionService
from tracecat.agent.session.types import AgentSessionEntity
from tracecat.auth.types import Role
from tracecat.chat.schemas import ApprovalDecision, ContinueRunRequest
from tracecat.db.engine import get_async_session_context_manager
from tracecat.db.models import AgentSession, Approval, User

pytestmark = pytest.mark.usefixtures("db")

TOOL_CALLS = ("tool_call_123", "tool_call_456")


class _ProcessCrash(BaseException):
    """Stands in for the API process dying mid-request."""


class _FakeTemporalHandle:
    """Applies each update id once; refuses a conflicting or nothing-new update."""

    def __init__(self, awaiting: Sequence[str] = TOOL_CALLS) -> None:
        self.outcomes: dict[str, bool] = {}
        self.received: list[WorkflowApprovalSubmission] = []
        self.decided: dict[str, Any] = {}
        self.awaiting = set(awaiting)
        self.execute_update = AsyncMock(side_effect=self._execute_update)

    async def _execute_update(
        self, _update: object, submission: WorkflowApprovalSubmission, *, id: str
    ) -> bool:
        if id in self.outcomes:
            return self.outcomes[id]
        for tool_call_id, result in submission.approvals.items():
            if tool_call_id in self.decided and decision_outcome(
                result
            ) != decision_outcome(self.decided[tool_call_id]):
                raise WorkflowUpdateFailedError(ApplicationError("decided differently"))
        if submission.approvals.keys() <= self.decided.keys():
            raise WorkflowUpdateFailedError(ApplicationError("already decided"))
        self.received.append(submission)
        self.decided.update(submission.approvals)
        self.awaiting -= set(submission.approvals)
        self.outcomes[id] = not self.awaiting
        return self.outcomes[id]


async def _seed_batch(
    session: AsyncSession, role: Role, *, commit_per_row: bool = False
) -> AgentSession:
    agent_session = AgentSession(
        id=uuid.uuid4(),
        title="Slack thread",
        workspace_id=role.workspace_id,
        entity_type=AgentSessionEntity.AGENT_PRESET.value,
        entity_id=uuid.uuid4(),
        curr_run_id=uuid.uuid4(),
        active_stream_id=uuid.uuid4(),
    )
    session.add(agent_session)
    await session.flush()
    for offset, tool_call_id in enumerate(TOOL_CALLS):
        approval = Approval(
            workspace_id=role.workspace_id,
            session_id=agent_session.id,
            tool_call_id=tool_call_id,
            tool_name="core.http_request",
            tool_call_args={"url": "https://example.com"},
            status=ApprovalStatus.PENDING,
        )
        if commit_per_row:
            # Rows recorded one per transaction, before batches were written
            # together. The fixture's outer transaction pins now(), so set it.
            approval.created_at = datetime.now(UTC) + timedelta(seconds=offset)
        session.add(approval)
    await session.commit()
    await session.refresh(agent_session)
    return agent_session


def _click(
    agent_session: AgentSession,
    *,
    action: Literal["approve", "deny"] = "approve",
    tool_call_id: str = "tool_call_123",
    user_id: uuid.UUID | None = None,
    batch_id: uuid.UUID | None = None,
    source: str = "web",
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Keyword arguments for ``AgentSessionService.decide``."""
    assert agent_session.curr_run_id is not None
    return {
        "session_id": agent_session.id,
        "batch_id": batch_id
        or approval_batch_id(agent_session.curr_run_id, TOOL_CALLS),
        "decision": ApprovalDecision(
            tool_call_id=tool_call_id,
            action=action,
            metadata=metadata,
        ),
        "source": source,
        "approved_by": user_id,
    }


async def _row(session: AsyncSession, agent_session: AgentSession, tool_call_id: str):
    row = await session.scalar(
        select(Approval).where(
            Approval.session_id == agent_session.id,
            Approval.tool_call_id == tool_call_id,
        )
    )
    assert row is not None
    await session.refresh(row)
    return row


def _approval_service_on(session: AsyncSession, default_role: Role) -> Any:
    """Run the EE approval activities on the test's session."""

    @asynccontextmanager
    async def with_session(role: Role | None = None):
        yield ApprovalService(session, role=role or default_role)

    return patch.object(ApprovalService, "with_session", with_session)


@pytest.fixture
async def approver(session: AsyncSession) -> User:
    user = User(
        id=uuid.uuid4(),
        email=f"approver-{uuid.uuid4()}@example.com",
        hashed_password="unused",
        is_active=True,
        is_verified=True,
        is_superuser=False,
        last_login_at=None,
    )
    session.add(user)
    await session.commit()
    return user


@pytest.mark.anyio
async def test_decide_records_actor_and_channel_origin_without_provider_ids(
    session: AsyncSession, svc_role: Role, approver: User
) -> None:
    agent_session = await _seed_batch(session, svc_role)
    service = AgentSessionService(session=session, role=svc_role)
    bot_id = uuid.uuid4()
    execute_update = AsyncMock(return_value=False)

    with _mock_approval_continuation_dependencies(
        _ApprovalContinuationRedis(), execute_update
    ):
        result = await service.decide(
            **_click(
                agent_session,
                user_id=approver.id,
                source="channel",
                metadata={"bot_id": str(bot_id)},
            )
        )

    assert result == DecisionRecorded(
        pending_tool_call_ids=["tool_call_456"], resumed=None
    )
    row = await _row(session, agent_session, "tool_call_123")
    assert row.status is ApprovalStatus.APPROVED
    assert row.approved_by == approver.id
    assert row.decision == {
        "value": True,
        "metadata": {
            "source": "channel",
            "bot_id": str(bot_id),
        },
    }
    execute_update.assert_awaited_once()


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("click", "reason"),
    [
        pytest.param(
            {"batch_id": approval_batch_id(uuid.uuid4(), TOOL_CALLS)},
            "no_longer_active",
            id="earlier-run",
        ),
        pytest.param({"tool_call_id": "tool_call_unknown"}, "not_valid", id="unknown"),
    ],
)
async def test_decide_validates_against_the_pending_row(
    session: AsyncSession,
    svc_role: Role,
    click: dict[str, Any],
    reason: str,
) -> None:
    agent_session = await _seed_batch(session, svc_role)
    service = AgentSessionService(session=session, role=svc_role)
    execute_update = AsyncMock(return_value=False)

    with _mock_approval_continuation_dependencies(
        _ApprovalContinuationRedis(), execute_update
    ):
        result = await service.decide(**_click(agent_session, **click))

    assert isinstance(result, DecisionRejected)
    assert result.reason == reason
    execute_update.assert_not_awaited()
    for tool_call_id in TOOL_CALLS:
        row = await _row(session, agent_session, tool_call_id)
        assert row.status is ApprovalStatus.PENDING


@pytest.mark.anyio
async def test_decide_matches_the_card_batch_id_not_the_run_id(
    session: AsyncSession, svc_role: Role
) -> None:
    agent_session = await _seed_batch(session, svc_role)
    service = AgentSessionService(session=session, role=svc_role)
    run_id = agent_session.curr_run_id
    assert run_id is not None
    execute_update = AsyncMock(return_value=False)

    with _mock_approval_continuation_dependencies(
        _ApprovalContinuationRedis(), execute_update
    ):
        run_as_batch = await service.decide(**_click(agent_session, batch_id=run_id))
        earlier_pause = await service.decide(
            **_click(
                agent_session,
                batch_id=approval_batch_id(run_id, ["tool_call_earlier"]),
            )
        )
        execute_update.assert_not_awaited()
        current = await service.decide(**_click(agent_session))

    stale = DecisionRejected(reason="no_longer_active", tool_call_id="tool_call_123")
    assert run_as_batch == stale
    assert earlier_pause == stale
    assert current == DecisionRecorded(
        pending_tool_call_ids=["tool_call_456"], resumed=None
    )


@pytest.mark.anyio
async def test_channel_card_batch_id_is_the_one_decide_accepts(
    session: AsyncSession, svc_role: Role
) -> None:
    """Notify and persist one pause as the workflow does; the card's id decides."""
    agent_session = AgentSession(
        id=uuid.uuid4(),
        title="Round trip",
        workspace_id=svc_role.workspace_id,
        entity_type=AgentSessionEntity.AGENT_PRESET.value,
        entity_id=uuid.uuid4(),
        curr_run_id=uuid.uuid4(),
        active_stream_id=uuid.uuid4(),
    )
    session.add(agent_session)
    await session.commit()
    assert agent_session.curr_run_id is not None
    items = [
        ToolCallContent(id=tool_call_id, name="core.http_request")
        for tool_call_id in reversed(TOOL_CALLS)
    ]
    updates: list[RunUpdate] = []

    class _Publisher:
        async def publish(self, role: Role, update: RunUpdate) -> str | None:
            del role
            updates.append(update)
            return "msg-1"

    async def execute_activity(activity: Any, arg: Any, **_: Any) -> Any:
        return await activity(arg)

    AgentContext.set(session_id=agent_session.id)
    service = AgentSessionService(session=session, role=svc_role)
    handle = _FakeTemporalHandle()
    with (
        _mock_approval_continuation_dependencies(
            _ApprovalContinuationRedis(), handle.execute_update
        ),
        _approval_service_on(session, svc_role),
        patch("temporalio.workflow.patched", return_value=True),
        patch("temporalio.workflow.execute_activity", execute_activity),
        patch(
            "tracecat.agent.channel_publisher.get_channel_publisher",
            return_value=_Publisher(),
        ),
    ):
        await ApprovalManager(role=svc_role).prepare(
            items, publish_to=agent_session.curr_run_id
        )
        assert isinstance(updates[0], ApprovalPending)
        result = await service.decide(
            **_click(agent_session, batch_id=updates[0].batch_id)
        )

    assert result == DecisionRecorded(
        pending_tool_call_ids=["tool_call_456"], resumed=None
    )


@pytest.mark.anyio
async def test_decide_matching_replay_is_a_noop_and_conflicting_replay_loses(
    session: AsyncSession, svc_role: Role, approver: User
) -> None:
    agent_session = await _seed_batch(session, svc_role)
    service = AgentSessionService(session=session, role=svc_role)
    handle = _FakeTemporalHandle()

    with _mock_approval_continuation_dependencies(
        _ApprovalContinuationRedis(), handle.execute_update
    ):
        first = await service.decide(**_click(agent_session, user_id=approver.id))
        recorded = await _row(session, agent_session, "tool_call_123")
        recorded_at = recorded.approved_at

        matching = await service.decide(**_click(agent_session))
        conflicting = await service.decide(**_click(agent_session, action="deny"))

    assert first == DecisionRecorded(
        pending_tool_call_ids=["tool_call_456"], resumed=None
    )
    assert matching == first
    assert conflicting == DecisionRejected(
        reason="no_longer_active", tool_call_id="tool_call_123"
    )
    # A recorded row means the run has it: the replay sent nothing.
    handle.execute_update.assert_awaited_once()
    row = await _row(session, agent_session, "tool_call_123")
    assert row.status is ApprovalStatus.APPROVED
    assert row.approved_at == recorded_at
    assert row.approved_by == approver.id


@pytest.mark.anyio
async def test_a_crash_after_the_run_accepted_is_recorded_by_the_retry(
    session: AsyncSession, svc_role: Role
) -> None:
    """The run takes the decision, the API dies before the row, the click retries."""
    agent_session = await _seed_batch(session, svc_role)
    service = AgentSessionService(session=session, role=svc_role)
    handle = _FakeTemporalHandle()
    record = ApprovalSubmissionService._record_accepted_decisions
    crashed = False

    async def crash_once(self: ApprovalSubmissionService, **kwargs: Any) -> None:
        nonlocal crashed
        if not crashed:
            crashed = True
            raise _ProcessCrash
        await record(self, **kwargs)

    run_id = agent_session.curr_run_id
    assert run_id is not None
    with (
        _mock_approval_continuation_dependencies(
            _ApprovalContinuationRedis(), handle.execute_update
        ),
        patch.object(
            ApprovalSubmissionService, "_record_accepted_decisions", crash_once
        ),
    ):
        with pytest.raises(_ProcessCrash):
            await service.decide(**_click(agent_session))
        await session.rollback()
        await session.refresh(agent_session)
        # The run has the decision; the row has not caught up.
        assert handle.awaiting == {"tool_call_456"}
        row = await _row(session, agent_session, "tool_call_123")
        assert row.status is ApprovalStatus.PENDING

        retried = await service.decide(**_click(agent_session))
        last = await service.decide(
            **_click(agent_session, tool_call_id="tool_call_456")
        )

    assert retried == DecisionRecorded(
        pending_tool_call_ids=["tool_call_456"], resumed=None
    )
    row = await _row(session, agent_session, "tool_call_123")
    assert row.status is ApprovalStatus.APPROVED
    # The retry reused the update id, so the run applied it once.
    assert [set(sub.approvals) for sub in handle.received] == [
        {"tool_call_123"},
        {"tool_call_456"},
    ]
    stream_id = _approval_continuation_stream_id(run_id, {"tool_call_456": True})
    assert last == DecisionRecorded(
        pending_tool_call_ids=[],
        resumed=RunRef(run_id=run_id, stream_id=stream_id),
    )
    await session.refresh(agent_session)
    assert agent_session.active_stream_id == stream_id


@pytest.mark.anyio
async def test_web_submission_spans_rows_recorded_one_per_commit(
    session: AsyncSession, svc_role: Role
) -> None:
    agent_session = await _seed_batch(session, svc_role, commit_per_row=True)
    service = AgentSessionService(session=session, role=svc_role)
    handle = _FakeTemporalHandle()

    with _mock_approval_continuation_dependencies(
        _ApprovalContinuationRedis(), handle.execute_update
    ):
        response = await service.run_turn(
            agent_session.id,
            ContinueRunRequest(
                decisions=[
                    ApprovalDecision(tool_call_id=tool_call_id, action="approve")
                    for tool_call_id in TOOL_CALLS
                ],
                source="inbox",
            ),
        )

    assert response is not None
    assert [set(sub.approvals) for sub in handle.received] == [set(TOOL_CALLS)]


@pytest.mark.anyio
async def test_replay_of_an_earlier_batch_does_not_announce_the_next_pause(
    session: AsyncSession, svc_role: Role
) -> None:
    """The run paused again on a new batch; the old set's stream is still current."""
    agent_session = await _seed_batch(session, svc_role)
    service = AgentSessionService(session=session, role=svc_role)
    handle = _FakeTemporalHandle()

    with _mock_approval_continuation_dependencies(
        _ApprovalContinuationRedis(), handle.execute_update
    ) as publish:
        for tool_call_id in TOOL_CALLS:
            await service.decide(**_click(agent_session, tool_call_id=tool_call_id))
        session.add(
            Approval(
                workspace_id=svc_role.workspace_id,
                session_id=agent_session.id,
                tool_call_id="tool_call_789",
                tool_name="core.http_request",
                tool_call_args={},
                status=ApprovalStatus.PENDING,
                # The fixture's outer transaction pins now().
                created_at=datetime.now(UTC) + timedelta(minutes=1),
            )
        )
        await session.commit()
        deliveries = handle.execute_update.await_count
        announcements = publish.await_count

        replay = await service.decide(
            **_click(agent_session, tool_call_id="tool_call_456")
        )

    assert replay == DecisionRecorded(pending_tool_call_ids=[], resumed=None)
    assert handle.execute_update.await_count == deliveries
    assert publish.await_count == announcements


@pytest.mark.anyio
async def test_clicks_group_by_the_batch_record_approval_requests_wrote(
    session: AsyncSession, svc_role: Role
) -> None:
    agent_session = AgentSession(
        id=uuid.uuid4(),
        title="Batch writer",
        workspace_id=svc_role.workspace_id,
        entity_type=AgentSessionEntity.AGENT_PRESET.value,
        entity_id=uuid.uuid4(),
        curr_run_id=uuid.uuid4(),
        active_stream_id=uuid.uuid4(),
    )
    session.add(agent_session)
    await session.commit()
    service = AgentSessionService(session=session, role=svc_role)
    handle = _FakeTemporalHandle()

    with (
        _mock_approval_continuation_dependencies(
            _ApprovalContinuationRedis(), handle.execute_update
        ),
        _approval_service_on(session, svc_role),
    ):
        await ApprovalManager.record_approval_requests(
            PersistApprovalsActivityInputs(
                role=svc_role,
                session_id=agent_session.id,
                approvals=[
                    ToolApprovalPayload(
                        tool_call_id=tool_call_id,
                        tool_name="core.http_request",
                        args={},
                    )
                    for tool_call_id in TOOL_CALLS
                ],
            )
        )
        first = await service.decide(**_click(agent_session))
        second = await service.decide(
            **_click(agent_session, tool_call_id="tool_call_456")
        )

    assert first == DecisionRecorded(
        pending_tool_call_ids=["tool_call_456"], resumed=None
    )
    assert isinstance(second, DecisionRecorded)
    assert second.resumed is not None
    assert [set(sub.approvals) for sub in handle.received] == [
        {"tool_call_123"},
        {"tool_call_456"},
    ]


@pytest.mark.anyio
async def test_partial_decisions_survive_re_persisting_the_requests(
    session: AsyncSession, svc_role: Role, approver: User
) -> None:
    agent_session = await _seed_batch(session, svc_role)
    service = AgentSessionService(session=session, role=svc_role)
    handle = _FakeTemporalHandle()

    with (
        _mock_approval_continuation_dependencies(
            _ApprovalContinuationRedis(), handle.execute_update
        ),
        _approval_service_on(session, svc_role),
    ):
        await service.decide(**_click(agent_session, user_id=approver.id))
        await ApprovalManager.record_approval_requests(
            PersistApprovalsActivityInputs(
                role=svc_role,
                session_id=agent_session.id,
                approvals=[
                    ToolApprovalPayload(
                        tool_call_id=tool_call_id,
                        tool_name="core.http_request",
                        args={"url": "https://example.com"},
                    )
                    for tool_call_id in TOOL_CALLS
                ],
            )
        )
        decided = await _row(session, agent_session, "tool_call_123")
        assert decided.status is ApprovalStatus.APPROVED
        assert decided.approved_by == approver.id
        assert decided.decision == {
            "value": True,
            "metadata": {"source": "web"},
        }

        result = await service.decide(
            **_click(agent_session, tool_call_id="tool_call_456")
        )

    assert isinstance(result, DecisionRecorded)
    assert result.resumed is not None
    assert [set(sub.approvals) for sub in handle.received] == [
        {"tool_call_123"},
        {"tool_call_456"},
    ]


@pytest.mark.anyio
async def test_concurrent_contradictory_clicks_keep_one_winner(svc_role: Role) -> None:
    """Independent transactions race; the run accepts exactly one decision."""
    run_id = uuid.uuid4()
    async with get_async_session_context_manager() as setup:
        agent_session = AgentSession(
            id=uuid.uuid4(),
            title="Concurrency",
            workspace_id=svc_role.workspace_id,
            entity_type=AgentSessionEntity.AGENT_PRESET.value,
            entity_id=uuid.uuid4(),
            curr_run_id=run_id,
            active_stream_id=uuid.uuid4(),
        )
        setup.add(agent_session)
        await setup.flush()
        setup.add(
            Approval(
                workspace_id=svc_role.workspace_id,
                session_id=agent_session.id,
                tool_call_id="tool_call_123",
                tool_name="core.http_request",
                tool_call_args={},
                status=ApprovalStatus.PENDING,
            )
        )
        await setup.commit()

    both_started = asyncio.Barrier(2)

    async def existing_user_id(_self: object, _user_id: uuid.UUID | None) -> None:
        await both_started.wait()

    async def click(action: Literal["approve", "deny"]):
        async with get_async_session_context_manager() as db:
            service = AgentSessionService(session=db, role=svc_role)
            return await service.decide(
                **_click(
                    agent_session,
                    action=action,
                    batch_id=approval_batch_id(run_id, ["tool_call_123"]),
                )
            )

    handle = _FakeTemporalHandle(awaiting=["tool_call_123"])
    try:
        with (
            _mock_approval_continuation_dependencies(
                _ApprovalContinuationRedis(), handle.execute_update
            ),
            patch.object(AgentSessionService, "_existing_user_id", existing_user_id),
        ):
            approve, deny = await asyncio.gather(click("approve"), click("deny"))

        results = {"approve": approve, "deny": deny}
        winners = [k for k, r in results.items() if isinstance(r, DecisionRecorded)]
        losers = [r for r in results.values() if isinstance(r, DecisionRejected)]
        assert len(winners) == 1
        assert [r.reason for r in losers] == ["no_longer_active"]
        assert len(handle.received) == 1

        async with get_async_session_context_manager() as verify:
            status = await verify.scalar(
                select(Approval.status).where(
                    Approval.session_id == agent_session.id,
                    Approval.tool_call_id == "tool_call_123",
                )
            )
        expected = {
            "approve": ApprovalStatus.APPROVED,
            "deny": ApprovalStatus.REJECTED,
        }[winners[0]]
        assert status is expected
    finally:
        async with get_async_session_context_manager() as cleanup:
            await cleanup.execute(
                delete(Approval).where(Approval.session_id == agent_session.id)
            )
            await cleanup.execute(
                delete(AgentSession).where(AgentSession.id == agent_session.id)
            )
            await cleanup.commit()


def _approve_all() -> ContinueRunRequest:
    return ContinueRunRequest(
        decisions=[
            ApprovalDecision(tool_call_id=tool_call_id, action="approve")
            for tool_call_id in TOOL_CALLS
        ],
        source="inbox",
    )


@pytest.mark.anyio
async def test_a_crash_before_the_run_heard_leaves_the_decisions_to_retry(
    session: AsyncSession, svc_role: Role
) -> None:
    """Die before set_approvals; nothing is recorded, and the retry delivers once."""
    agent_session = await _seed_batch(session, svc_role)
    run_id = agent_session.curr_run_id
    assert run_id is not None
    service = AgentSessionService(session=session, role=svc_role)
    handle = _FakeTemporalHandle()
    submit_approvals = AgentBackend.submit_approvals
    crashed = False

    async def crash_once(
        self: AgentBackend[Any, Any],
        run_id: uuid.UUID,
        submission: WorkflowApprovalSubmission,
    ) -> bool:
        nonlocal crashed
        if not crashed:
            crashed = True
            raise _ProcessCrash
        return await submit_approvals(self, run_id, submission)

    stream_id = _approval_continuation_stream_id(
        run_id, dict.fromkeys(TOOL_CALLS, True)
    )
    with (
        _mock_approval_continuation_dependencies(
            _ApprovalContinuationRedis(), handle.execute_update
        ),
        patch.object(AgentBackend, "submit_approvals", crash_once),
    ):
        with pytest.raises(_ProcessCrash):
            await service.run_turn(agent_session.id, _approve_all())
        await session.rollback()
        await session.refresh(agent_session)
        for tool_call_id in TOOL_CALLS:
            row = await _row(session, agent_session, tool_call_id)
            assert row.status is ApprovalStatus.PENDING

        response = await service.run_turn(agent_session.id, _approve_all())

    assert response is not None
    assert response.active_stream_id == stream_id
    assert [set(sub.approvals) for sub in handle.received] == [set(TOOL_CALLS)]


@pytest.mark.anyio
@pytest.mark.parametrize(
    "run_has",
    [
        pytest.param(False, id="decided-differently"),
        # Another surface's submission carried it and is still recording.
        pytest.param(True, id="already-decided"),
    ],
)
async def test_a_refused_decision_records_nothing_and_restores_the_stream(
    session: AsyncSession, svc_role: Role, run_has: bool
) -> None:
    agent_session = await _seed_batch(session, svc_role)
    previous_stream_id = agent_session.active_stream_id
    service = AgentSessionService(session=session, role=svc_role)
    handle = _FakeTemporalHandle()
    handle.decided = {"tool_call_123": run_has}

    with _mock_approval_continuation_dependencies(
        _ApprovalContinuationRedis(), handle.execute_update
    ):
        refused = await service.decide(**_click(agent_session))

    assert refused == DecisionRejected(
        reason="no_longer_active", tool_call_id="tool_call_123"
    )
    await session.refresh(agent_session)
    row = await _row(session, agent_session, "tool_call_123")
    assert row.status is ApprovalStatus.PENDING
    assert agent_session.active_stream_id == previous_stream_id


@pytest.mark.anyio
async def test_a_submission_that_never_reached_temporal_keeps_its_stream(
    session: AsyncSession, svc_role: Role
) -> None:
    """An identical click may be in flight on the same update id: never restore."""
    agent_session = await _seed_batch(session, svc_role)
    run_id = agent_session.curr_run_id
    assert run_id is not None
    service = AgentSessionService(session=session, role=svc_role)
    handle = _FakeTemporalHandle()

    with (
        _mock_approval_continuation_dependencies(
            _ApprovalContinuationRedis(), handle.execute_update
        ),
        patch(
            "tracecat.agent.backends.base.get_temporal_client",
            AsyncMock(side_effect=RuntimeError("temporal unavailable")),
        ),
        pytest.raises(AgentControlUncertain),
    ):
        await service.decide(**_click(agent_session))

    await session.refresh(agent_session)
    row = await _row(session, agent_session, "tool_call_123")
    assert row.status is ApprovalStatus.PENDING
    assert agent_session.active_stream_id == _approval_continuation_stream_id(
        run_id, {"tool_call_123": True}
    )


@pytest.mark.anyio
async def test_a_replay_matches_a_legacy_row_by_its_status(
    session: AsyncSession, svc_role: Role
) -> None:
    """Rows decided before decision payloads existed keep only the outcome."""
    agent_session = await _seed_batch(session, svc_role)
    legacy = await _row(session, agent_session, "tool_call_123")
    legacy.status = ApprovalStatus.APPROVED
    legacy.decision = None
    await session.commit()
    service = AgentSessionService(session=session, role=svc_role)
    handle = _FakeTemporalHandle()

    with _mock_approval_continuation_dependencies(
        _ApprovalContinuationRedis(), handle.execute_update
    ):
        matching = await service.decide(**_click(agent_session))
        conflicting = await service.decide(**_click(agent_session, action="deny"))

    assert isinstance(matching, DecisionRecorded)
    assert conflicting == DecisionRejected(
        reason="no_longer_active", tool_call_id="tool_call_123"
    )


@pytest.mark.anyio
async def test_a_submission_the_run_does_not_answer_is_uncertain(
    session: AsyncSession, svc_role: Role
) -> None:
    """A stuck update releases its connection and turn; the retry reuses its id."""
    agent_session = await _seed_batch(session, svc_role)
    service = AgentSessionService(session=session, role=svc_role)

    async def hang(*_args: Any, **_kwargs: Any) -> bool:
        await asyncio.Event().wait()
        return False

    with (
        _mock_approval_continuation_dependencies(
            _ApprovalContinuationRedis(), AsyncMock(side_effect=hang)
        ),
        patch("tracecat.agent.approvals.submission._SUBMIT_TIMEOUT_SECONDS", 0.05),
        pytest.raises(AgentControlUncertain),
    ):
        await service.decide(**_click(agent_session))

    await session.refresh(agent_session)
    row = await _row(session, agent_session, "tool_call_123")
    assert row.status is ApprovalStatus.PENDING


@pytest.mark.anyio
async def test_a_failed_record_still_reports_the_accepted_decision(
    session: AsyncSession, svc_role: Role
) -> None:
    """The run has it; a retry records the row without a second delivery."""
    agent_session = await _seed_batch(session, svc_role)
    service = AgentSessionService(session=session, role=svc_role)
    handle = _FakeTemporalHandle()
    record_decision = submission_module.record_decision
    failed = False

    def fail_once(*args: Any, **kwargs: Any) -> None:
        nonlocal failed
        if not failed:
            failed = True
            raise OperationalError("UPDATE approval", {}, Exception("db gone"))
        record_decision(*args, **kwargs)

    with (
        _mock_approval_continuation_dependencies(
            _ApprovalContinuationRedis(), handle.execute_update
        ),
        patch.object(submission_module, "record_decision", fail_once),
    ):
        first = await service.decide(**_click(agent_session))
        await session.refresh(agent_session)
        row = await _row(session, agent_session, "tool_call_123")
        assert row.status is ApprovalStatus.PENDING
        retried = await service.decide(**_click(agent_session))

    expected = DecisionRecorded(pending_tool_call_ids=["tool_call_456"], resumed=None)
    assert first == expected
    assert retried == expected
    row = await _row(session, agent_session, "tool_call_123")
    assert row.status is ApprovalStatus.APPROVED
    assert len(handle.received) == 1


@pytest.mark.anyio
async def test_a_crash_before_the_announcement_leaves_the_retry_to_announce(
    session: AsyncSession, svc_role: Role
) -> None:
    """Rows commit with the announcement, so a retry is not mistaken for a repeat."""
    agent_session = await _seed_batch(session, svc_role)
    service = AgentSessionService(session=session, role=svc_role)
    handle = _FakeTemporalHandle()

    with _mock_approval_continuation_dependencies(
        _ApprovalContinuationRedis(), handle.execute_update
    ) as publish:
        publish.side_effect = [_ProcessCrash(), None]
        with pytest.raises(_ProcessCrash):
            await service.decide(**_click(agent_session))
        await session.rollback()
        await session.refresh(agent_session)
        row = await _row(session, agent_session, "tool_call_123")
        assert row.status is ApprovalStatus.PENDING

        retried = await service.decide(**_click(agent_session))

    assert retried == DecisionRecorded(
        pending_tool_call_ids=["tool_call_456"], resumed=None
    )
    assert publish.await_count == 2
    assert len(handle.received) == 1
    row = await _row(session, agent_session, "tool_call_123")
    assert row.status is ApprovalStatus.APPROVED
