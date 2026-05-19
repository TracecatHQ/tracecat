"""Tests for the approvals inbox provider."""

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from tracecat_ee.inbox.providers.approvals import ApprovalsInboxProvider

from tracecat.agent.approvals.enums import ApprovalStatus
from tracecat.auth.types import Role
from tracecat.db.models import AgentSession, Approval

pytestmark = pytest.mark.usefixtures("db")


def _agent_session(
    workspace_id: uuid.UUID,
    *,
    entity_type: str = "workflow",
    parent_session_id: uuid.UUID | None = None,
) -> AgentSession:
    return AgentSession(
        id=uuid.uuid4(),
        title="Approval review session",
        workspace_id=workspace_id,
        entity_type=entity_type,
        entity_id=uuid.uuid4(),
        parent_session_id=parent_session_id,
    )


def _approval(
    workspace_id: uuid.UUID,
    session_id: uuid.UUID,
    tool_call_id: str,
    *,
    status: ApprovalStatus = ApprovalStatus.PENDING,
) -> Approval:
    return Approval(
        workspace_id=workspace_id,
        session_id=session_id,
        tool_call_id=tool_call_id,
        tool_name="dangerous_tool",
        status=status,
    )


@pytest.mark.anyio
async def test_count_pending_items_counts_sessions_not_approval_rows(
    session: AsyncSession,
    svc_role: Role,
) -> None:
    """The sidebar badge should match pending inbox items, not tool approvals."""
    first_parent = _agent_session(svc_role.workspace_id)
    second_parent = _agent_session(svc_role.workspace_id)
    completed_parent = _agent_session(svc_role.workspace_id)
    case_session = _agent_session(svc_role.workspace_id, entity_type="case")
    session.add_all([first_parent, second_parent, completed_parent, case_session])
    await session.flush()

    child_session = _agent_session(
        svc_role.workspace_id,
        entity_type="workflow",
        parent_session_id=first_parent.id,
    )
    session.add(child_session)
    await session.flush()

    approvals = [
        # Eight pending approval rows across two parent sessions should produce a
        # badge count of two, because the inbox renders one review item per session.
        *[
            _approval(svc_role.workspace_id, first_parent.id, f"first-{idx}")
            for idx in range(4)
        ],
        *[
            _approval(svc_role.workspace_id, second_parent.id, f"second-{idx}")
            for idx in range(4)
        ],
        # Non-pending approvals remain visible in the inbox history but should not
        # increment the attention badge.
        _approval(
            svc_role.workspace_id,
            completed_parent.id,
            "completed",
            status=ApprovalStatus.APPROVED,
        ),
        # Forked approval continuation sessions are hidden from the inbox.
        _approval(svc_role.workspace_id, child_session.id, "child"),
        # Non-inbox entity types should not be counted.
        _approval(svc_role.workspace_id, case_session.id, "case"),
    ]
    session.add_all(approvals)
    await session.commit()

    provider = ApprovalsInboxProvider(session, svc_role)

    assert await provider.count_pending_items() == 2
