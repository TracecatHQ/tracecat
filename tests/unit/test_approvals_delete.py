"""Tests for DELETE /approvals/{session_id} (delete_approval endpoint)."""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, patch

import pytest
from tracecat_ee.agent.approvals.router import delete_approval

from tracecat.agent.approvals.enums import ApprovalStatus
from tracecat.agent.session.types import TurnLifecycle, TurnLifecycleResult
from tracecat.auth.types import Role
from tracecat.exceptions import TracecatConflictError


def _execute_role(workspace_id: uuid.UUID) -> Role:
    return Role(
        type="service",
        service_id="tracecat-api",
        workspace_id=workspace_id,
        organization_id=uuid.uuid4(),
        scopes=frozenset({"agent:execute"}),
    )


def _approval_stub(tool_call_id: str, status: ApprovalStatus) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        tool_call_id=tool_call_id,
        status=status,
    )


@pytest.mark.anyio
async def test_delete_approval_denies_pending_via_run_turn() -> None:
    """Workflow alive: pending approvals are denied via run_turn, session is kept."""
    workspace_id = uuid.uuid4()
    session_id = uuid.uuid4()
    pending = _approval_stub("tool_call_1", ApprovalStatus.PENDING)
    already_resolved = _approval_stub("tool_call_2", ApprovalStatus.APPROVED)
    session_stub = SimpleNamespace(id=session_id)

    fake_session_svc = SimpleNamespace(
        get_session=AsyncMock(return_value=session_stub),
        run_turn=AsyncMock(return_value=None),
    )
    fake_approval_svc = SimpleNamespace(
        list_approvals_for_session=AsyncMock(return_value=[pending, already_resolved]),
        delete_approval=AsyncMock(return_value=None),
    )

    with (
        patch(
            "tracecat_ee.agent.approvals.router.AgentSessionService",
            return_value=fake_session_svc,
        ),
        patch(
            "tracecat_ee.agent.approvals.router.ApprovalService",
            return_value=fake_approval_svc,
        ),
    ):
        raw = cast(Any, delete_approval).__wrapped__
        await raw(
            role=_execute_role(workspace_id),
            session_id=session_id,
            session=AsyncMock(),
        )

    # Only the pending approval goes into the deny decisions
    decisions = fake_session_svc.run_turn.call_args.args[1].decisions
    assert len(decisions) == 1
    assert decisions[0].tool_call_id == "tool_call_1"
    assert decisions[0].action == "deny"

    # Session is not deleted, approval records are not directly deleted
    fake_approval_svc.delete_approval.assert_not_awaited()


@pytest.mark.anyio
async def test_delete_approval_deletes_records_when_workflow_dead() -> None:
    """Workflow dead: run_turn fails, approval records are deleted directly."""
    workspace_id = uuid.uuid4()
    session_id = uuid.uuid4()
    pending = _approval_stub("tool_call_1", ApprovalStatus.PENDING)
    session_stub = SimpleNamespace(id=session_id)

    fake_session_svc = SimpleNamespace(
        get_session=AsyncMock(return_value=session_stub),
        run_turn=AsyncMock(side_effect=RuntimeError("Temporal workflow not found")),
    )
    fake_approval_svc = SimpleNamespace(
        list_approvals_for_session=AsyncMock(return_value=[pending]),
        delete_approval=AsyncMock(return_value=None),
    )

    with (
        patch(
            "tracecat_ee.agent.approvals.router.AgentSessionService",
            return_value=fake_session_svc,
        ),
        patch(
            "tracecat_ee.agent.approvals.router.ApprovalService",
            return_value=fake_approval_svc,
        ),
    ):
        raw = cast(Any, delete_approval).__wrapped__
        await raw(
            role=_execute_role(workspace_id),
            session_id=session_id,
            session=AsyncMock(),
        )

    fake_session_svc.run_turn.assert_awaited_once()
    # Falls back to direct approval record deletion
    fake_approval_svc.delete_approval.assert_awaited_once_with(pending)


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("lifecycle", "keeps_rows"),
    [(TurnLifecycle.RUNNING, True), (TurnLifecycle.COMPLETED, False)],
)
async def test_delete_approval_keeps_records_a_live_run_still_waits_on(
    lifecycle: TurnLifecycle, keeps_rows: bool
) -> None:
    """A live run refusing the denial holds decisions these rows have not caught up to."""
    session_id = uuid.uuid4()
    pending = _approval_stub("tool_call_1", ApprovalStatus.PENDING)
    fake_session_svc = SimpleNamespace(
        get_session=AsyncMock(return_value=SimpleNamespace(id=session_id)),
        run_turn=AsyncMock(side_effect=TracecatConflictError("refused")),
        get_turn_lifecycle=AsyncMock(
            return_value=TurnLifecycleResult(lifecycle, uuid.uuid4())
        ),
    )
    fake_approval_svc = SimpleNamespace(
        list_approvals_for_session=AsyncMock(return_value=[pending]),
        delete_approval=AsyncMock(return_value=None),
    )

    with (
        patch(
            "tracecat_ee.agent.approvals.router.AgentSessionService",
            return_value=fake_session_svc,
        ),
        patch(
            "tracecat_ee.agent.approvals.router.ApprovalService",
            return_value=fake_approval_svc,
        ),
    ):
        raw = cast(Any, delete_approval).__wrapped__
        call = raw(
            role=_execute_role(uuid.uuid4()), session_id=session_id, session=AsyncMock()
        )
        if keeps_rows:
            with pytest.raises(TracecatConflictError):
                await call
        else:
            await call

    if keeps_rows:
        fake_approval_svc.delete_approval.assert_not_awaited()
    else:
        fake_approval_svc.delete_approval.assert_awaited_once_with(pending)
