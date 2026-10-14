"""Integration coverage for how agent run outcomes show in the inbox."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.agent.approvals.enums import ApprovalStatus
from tracecat.agent.common.stream_types import HarnessType
from tracecat.auth.types import Role
from tracecat.chat.enums import MessageKind
from tracecat.db.models import AgentSession, AgentSessionHistory, Approval
from tracecat.inbox.providers.agent_runs import AgentRunsInboxProvider
from tracecat.inbox.schemas import InboxItemRead
from tracecat.inbox.types import InboxGroup, InboxItemStatus

pytestmark = [
    pytest.mark.anyio,
    pytest.mark.integration,
    pytest.mark.usefixtures("db"),
]

BASE_TIME = datetime(2026, 1, 1, tzinfo=UTC)


async def _run(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    title: str,
    *,
    turns: list[MessageKind],
    approval: tuple[ApprovalStatus, str] | None = None,
    last_error: str | None = None,
) -> AgentSession:
    """Add a finished run with its history rows and one approval decision."""
    row = AgentSession(
        workspace_id=workspace_id,
        title=title,
        entity_type="case",
        entity_id=uuid.uuid4(),
        harness_type=HarnessType.CLAUDE_CODE,
        last_error=last_error,
        created_at=BASE_TIME,
        updated_at=BASE_TIME,
    )
    session.add(row)
    await session.flush()
    for kind in turns:
        session.add(
            AgentSessionHistory(
                workspace_id=workspace_id,
                session_id=row.id,
                content={"type": "assistant"},
                kind=kind.value,
            )
        )
        # Rows are ordered by insertion, so flush each one.
        await session.flush()
    if approval is not None:
        status, reason = approval
        session.add(
            Approval(
                workspace_id=workspace_id,
                session_id=row.id,
                tool_call_id=f"{title}-call",
                tool_name="core.cases.list_cases",
                status=status,
                reason=reason,
            )
        )
    return row


def _by_id(items: list[InboxItemRead]) -> dict[uuid.UUID, InboxItemRead]:
    return {item.source_id: item for item in items}


async def test_run_outcomes_show_latest_turn_not_past_denials(
    session: AsyncSession,
    svc_role: Role,
) -> None:
    """Denials and cancels never read as failed; the latest turn sets the status."""
    assert svc_role.workspace_id is not None
    workspace_id = svc_role.workspace_id
    chat = MessageKind.CHAT_MESSAGE
    cancelled = MessageKind.CANCELLED
    denied = await _run(
        session,
        workspace_id,
        "denied",
        turns=[chat, chat],
        approval=(ApprovalStatus.REJECTED, "Not this case"),
    )
    stopped = await _run(
        session,
        workspace_id,
        "stopped",
        turns=[chat, cancelled, MessageKind.INTERNAL],
        approval=(ApprovalStatus.REJECTED, "Cancelled while waiting for approval"),
    )
    recovered = await _run(
        session,
        workspace_id,
        "recovered",
        turns=[chat, cancelled, chat],
        approval=(ApprovalStatus.REJECTED, "Cancelled while waiting for approval"),
    )
    errored = await _run(
        session,
        workspace_id,
        "errored",
        turns=[chat],
        last_error="Model request failed",
    )
    await session.commit()
    session.expunge_all()
    provider = AgentRunsInboxProvider(session, svc_role)

    items = _by_id((await provider.list_items(limit=20)).items)
    assert {
        title: (
            items[row.id].status,
            (items[row.id].metadata or {}).get("temporal_status"),
            items[row.id].preview,
        )
        for title, row in [
            ("denied", denied),
            ("stopped", stopped),
            ("recovered", recovered),
            ("errored", errored),
        ]
    } == {
        "denied": (InboxItemStatus.COMPLETED, None, "Execution completed"),
        "stopped": (InboxItemStatus.COMPLETED, "CANCELED", "Execution cancelled"),
        "recovered": (InboxItemStatus.COMPLETED, None, "Execution completed"),
        "errored": (InboxItemStatus.FAILED, "FAILED", "Model request failed"),
    }

    completed = await provider.list_items(limit=20, group=InboxGroup.COMPLETED)
    assert set(_by_id(completed.items)) == {denied.id, stopped.id, recovered.id}
    error = await provider.list_items(limit=20, group=InboxGroup.ERROR)
    assert set(_by_id(error.items)) == {errored.id}
