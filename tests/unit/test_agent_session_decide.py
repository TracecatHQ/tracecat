"""Per-click approval decisions: recorded on the row, then delivered to the run."""

from __future__ import annotations

import asyncio
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from unittest.mock import AsyncMock, patch

import orjson
import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from tracecat_ee.agent.activities import (
    PersistApprovalsActivityInputs,
    ToolApprovalPayload,
)
from tracecat_ee.agent.approvals.service import ApprovalManager, ApprovalService

from tests.unit.test_agent_session_approval_sink import (
    _ApprovalContinuationRedis,
    _mock_approval_continuation_dependencies,
)
from tracecat.agent.approvals.enums import ApprovalStatus
from tracecat.agent.backends.base import AgentBackend
from tracecat.agent.backends.schemas import WorkflowApprovalSubmission
from tracecat.agent.backends.types import AgentControlRejected
from tracecat.agent.session.results import (
    DecisionRecorded,
    DecisionRejected,
    RunRef,
)
from tracecat.agent.session.service import (
    AgentSessionService,
    _approval_continuation_stream_id,
)
from tracecat.agent.session.types import AgentSessionEntity
from tracecat.auth.types import Role
from tracecat.chat import tokens
from tracecat.chat.schemas import ApprovalDecision, ContinueRunRequest
from tracecat.db.engine import get_async_session_context_manager
from tracecat.db.models import AgentSession, Approval, User

pytestmark = pytest.mark.usefixtures("db")

TOOL_CALLS = ("tool_call_123", "tool_call_456")


class _ProcessCrash(BaseException):
    """Stands in for the API process dying mid-request."""


class _FakeTemporalHandle:
    """Applies each update id once, as Temporal deduplicates updates."""

    def __init__(self) -> None:
        self.outcomes: dict[str, bool] = {}
        self.received: list[WorkflowApprovalSubmission] = []
        self.awaiting = set(TOOL_CALLS)
        self.execute_update = AsyncMock(side_effect=self._execute_update)

    async def _execute_update(
        self, _update: object, submission: WorkflowApprovalSubmission, *, id: str
    ) -> bool:
        if id not in self.outcomes:
            self.received.append(submission)
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
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Keyword arguments for ``AgentSessionService.decide``."""
    assert agent_session.curr_run_id is not None
    return {
        "session_id": agent_session.id,
        "batch_id": batch_id or agent_session.curr_run_id,
        "decision": ApprovalDecision(
            tool_call_id=tool_call_id,
            action=action,
            metadata=metadata or {"source": "web"},
        ),
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
                metadata={"source": "channel", "bot_id": str(bot_id)},
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
        pytest.param({"batch_id": uuid.uuid4()}, "no_longer_active", id="stale-run"),
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
async def test_decide_matching_replay_redelivers_and_conflicting_replay_loses(
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

    assert first == matching
    assert conflicting == DecisionRejected(
        reason="no_longer_active", tool_call_id="tool_call_123"
    )
    # The replay wrote nothing and kept the first approver.
    row = await _row(session, agent_session, "tool_call_123")
    assert row.status is ApprovalStatus.APPROVED
    assert row.approved_at == recorded_at
    assert row.approved_by == approver.id
    # It retried delivery under the same update id; the run applied it once.
    assert handle.execute_update.await_count == 2
    first_id, replay_id = (
        call.kwargs["id"] for call in handle.execute_update.await_args_list
    )
    assert first_id == replay_id
    assert len(handle.received) == 1


@pytest.mark.anyio
async def test_decide_recovers_a_delivery_lost_after_the_commit(
    session: AsyncSession, svc_role: Role
) -> None:
    """Commit the final decision, die before set_approvals, then retry the click."""
    agent_session = await _seed_batch(session, svc_role)
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
        if "tool_call_456" in submission.approvals and not crashed:
            crashed = True
            raise _ProcessCrash
        return await submit_approvals(self, run_id, submission)

    assert agent_session.curr_run_id is not None
    stream_id = _approval_continuation_stream_id(agent_session.curr_run_id, TOOL_CALLS)
    with (
        _mock_approval_continuation_dependencies(
            _ApprovalContinuationRedis(), handle.execute_update
        ),
        patch.object(AgentBackend, "submit_approvals", crash_once),
    ):
        await service.decide(**_click(agent_session, tool_call_id="tool_call_123"))
        with pytest.raises(_ProcessCrash):
            await service.decide(**_click(agent_session, tool_call_id="tool_call_456"))
        # The decision committed; the run never saw it.
        row = await _row(session, agent_session, "tool_call_456")
        assert row.status is ApprovalStatus.APPROVED
        assert handle.awaiting == {"tool_call_456"}

        retried = await service.decide(
            **_click(agent_session, tool_call_id="tool_call_456")
        )
        replayed = await service.decide(
            **_click(agent_session, tool_call_id="tool_call_456")
        )

    resumed = RunRef(run_id=agent_session.curr_run_id, stream_id=stream_id)
    assert retried == DecisionRecorded(pending_tool_call_ids=[], resumed=resumed)
    assert replayed == retried
    # The run received the recorded winner once.
    assert [set(sub.approvals) for sub in handle.received] == [
        {"tool_call_123"},
        set(TOOL_CALLS),
    ]
    assert handle.received[-1].approvals == {
        "tool_call_123": True,
        "tool_call_456": True,
    }


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


def _intercept_first_delivery(
    on_first: Any,
) -> Any:
    """Run ``on_first`` while the first single-decision delivery is in flight."""
    submit_approvals = AgentBackend.submit_approvals
    seen = False

    async def intercept(
        self: AgentBackend[Any, Any],
        run_id: uuid.UUID,
        submission: WorkflowApprovalSubmission,
    ) -> bool:
        nonlocal seen
        if set(submission.approvals) == {"tool_call_123"} and not seen:
            seen = True
            return await on_first(lambda: submit_approvals(self, run_id, submission))
        return await submit_approvals(self, run_id, submission)

    return patch.object(AgentBackend, "submit_approvals", intercept)


@pytest.mark.anyio
async def test_late_delivery_of_a_superseded_set_follows_the_newer_stream(
    session: AsyncSession, svc_role: Role
) -> None:
    """A newer set resumes the run first; the late one reports nothing stale."""
    agent_session = await _seed_batch(session, svc_role)
    service = AgentSessionService(session=session, role=svc_role)
    handle = _FakeTemporalHandle()
    redis = _ApprovalContinuationRedis()
    newer: list[Any] = []

    async def newer_click_first(deliver: Any) -> bool:
        newer.append(
            await service.decide(**_click(agent_session, tool_call_id="tool_call_456"))
        )
        return await deliver()

    assert agent_session.curr_run_id is not None
    run_id = agent_session.curr_run_id
    late_stream = _approval_continuation_stream_id(run_id, ["tool_call_123"])
    newer_stream = _approval_continuation_stream_id(run_id, TOOL_CALLS)
    with (
        _mock_approval_continuation_dependencies(
            redis, handle.execute_update
        ) as publish,
        _intercept_first_delivery(newer_click_first),
    ):
        late = await service.decide(**_click(agent_session))

    assert newer == [
        DecisionRecorded(
            pending_tool_call_ids=[],
            resumed=RunRef(run_id=run_id, stream_id=newer_stream),
        )
    ]
    assert late == DecisionRecorded(pending_tool_call_ids=[], resumed=None)
    # Only the newer set announced the run; the late segment was closed.
    assert [call.kwargs["stream_id"] for call in publish.await_args_list] == [
        newer_stream
    ]
    late_key = f"agent-stream:{svc_role.workspace_id}:{agent_session.id}:{late_stream}"
    last = orjson.loads(redis.streams[late_key][-1][1][tokens.DATA_KEY])
    assert last[tokens.END_TOKEN] == tokens.END_TOKEN_VALUE
    assert last["terminal"] is False
    await session.refresh(agent_session)
    assert agent_session.active_stream_id == newer_stream


@pytest.mark.anyio
async def test_rejected_set_keeps_decisions_a_newer_set_recorded_on_top(
    session: AsyncSession, svc_role: Role
) -> None:
    agent_session = await _seed_batch(session, svc_role)
    service = AgentSessionService(session=session, role=svc_role)
    handle = _FakeTemporalHandle()

    async def newer_click_then_reject(_deliver: Any) -> bool:
        await service.decide(**_click(agent_session, tool_call_id="tool_call_456"))
        raise AgentControlRejected("run refused the decision")

    assert agent_session.curr_run_id is not None
    newer_stream = _approval_continuation_stream_id(
        agent_session.curr_run_id, TOOL_CALLS
    )
    with (
        _mock_approval_continuation_dependencies(
            _ApprovalContinuationRedis(), handle.execute_update
        ),
        _intercept_first_delivery(newer_click_then_reject),
        pytest.raises(AgentControlRejected),
    ):
        await service.decide(**_click(agent_session))

    for tool_call_id in TOOL_CALLS:
        row = await _row(session, agent_session, tool_call_id)
        assert row.status is ApprovalStatus.APPROVED
    await session.refresh(agent_session)
    assert agent_session.active_stream_id == newer_stream


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
        set(TOOL_CALLS),
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
    assert set(handle.received[-1].approvals) == set(TOOL_CALLS)


@pytest.mark.anyio
async def test_concurrent_contradictory_clicks_keep_one_winner(svc_role: Role) -> None:
    """Independent transactions race; the session lock lets exactly one record."""
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
            return await service.decide(**_click(agent_session, action=action))

    execute_update = AsyncMock(return_value=True)
    try:
        with (
            _mock_approval_continuation_dependencies(
                _ApprovalContinuationRedis(), execute_update
            ),
            patch.object(AgentSessionService, "_existing_user_id", existing_user_id),
        ):
            approve, deny = await asyncio.gather(click("approve"), click("deny"))

        results = {"approve": approve, "deny": deny}
        winners = [k for k, r in results.items() if isinstance(r, DecisionRecorded)]
        losers = [r for r in results.values() if isinstance(r, DecisionRejected)]
        assert len(winners) == 1
        assert [r.reason for r in losers] == ["no_longer_active"]
        execute_update.assert_awaited_once()

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
