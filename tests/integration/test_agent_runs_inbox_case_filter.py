"""Integration coverage for case-filtered agent runs."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from tracecat_ee.inbox.providers.agent_runs import AgentRunsInboxProvider

from tracecat.agent.common.stream_types import HarnessType
from tracecat.auth.types import Role
from tracecat.cases.enums import (
    CaseAgentSessionInteractionOperation,
    CasePriority,
    CaseSeverity,
    CaseStatus,
)
from tracecat.db.models import (
    AgentSession,
    Case,
    CaseAgentSessionInteraction,
    Workspace,
)

pytestmark = [
    pytest.mark.anyio,
    pytest.mark.integration,
    pytest.mark.usefixtures("db"),
]


def _case(workspace_id: uuid.UUID, case_number: int) -> Case:
    return Case(
        workspace_id=workspace_id,
        case_number=case_number,
        summary=f"Case {case_number}",
        description="Inbox case-filter test",
        status=CaseStatus.NEW,
        priority=CasePriority.MEDIUM,
        severity=CaseSeverity.LOW,
    )


def _agent_session(
    workspace_id: uuid.UUID,
    title: str,
    created_at: datetime,
    *,
    entity_id: uuid.UUID,
) -> AgentSession:
    return AgentSession(
        workspace_id=workspace_id,
        title=title,
        entity_type="case",
        entity_id=entity_id,
        harness_type=HarnessType.CLAUDE_CODE,
        created_at=created_at,
        updated_at=created_at,
    )


async def test_case_filter_is_scoped_deduplicated_and_paginated(
    session: AsyncSession,
    svc_role: Role,
) -> None:
    """Exercise filtering, deduplication, and keyset paging in one DB scenario."""
    assert svc_role.workspace_id is not None
    assert svc_role.organization_id is not None
    workspace_id = svc_role.workspace_id
    target_case = _case(workspace_id, 1712)
    other_case = _case(workspace_id, 1713)
    session.add_all([target_case, other_case])
    await session.flush()

    base_time = datetime(2026, 1, 1, tzinfo=UTC)
    first, second, unrelated = [
        _agent_session(
            workspace_id,
            title,
            base_time + timedelta(minutes=index),
            entity_id=target_case.id if index < 2 else other_case.id,
        )
        for index, title in enumerate(["First", "Second", "Unrelated"])
    ]
    session.add_all([first, second, unrelated])
    await session.flush()

    for agent_session in [first, second]:
        session.add(
            CaseAgentSessionInteraction(
                workspace_id=workspace_id,
                case_id=target_case.id,
                agent_session_id=agent_session.id,
                operation=CaseAgentSessionInteractionOperation.UPDATE,
            )
        )
    # A second operation for one session must not duplicate the inbox row.
    session.add(
        CaseAgentSessionInteraction(
            workspace_id=workspace_id,
            case_id=target_case.id,
            agent_session_id=second.id,
            operation=CaseAgentSessionInteractionOperation.CREATE,
        )
    )
    session.add(
        CaseAgentSessionInteraction(
            workspace_id=workspace_id,
            case_id=other_case.id,
            agent_session_id=unrelated.id,
            operation=CaseAgentSessionInteractionOperation.UPDATE,
        )
    )

    foreign_workspace = Workspace(
        name="other-workspace",
        organization_id=svc_role.organization_id,
    )
    session.add(foreign_workspace)
    await session.flush()
    foreign_case = _case(foreign_workspace.id, 1712)
    session.add(foreign_case)
    await session.flush()
    foreign_session = _agent_session(
        foreign_workspace.id,
        "Foreign",
        base_time,
        entity_id=foreign_case.id,
    )
    session.add(foreign_session)
    await session.flush()
    session.add(
        CaseAgentSessionInteraction(
            workspace_id=foreign_workspace.id,
            case_id=foreign_case.id,
            agent_session_id=foreign_session.id,
            operation=CaseAgentSessionInteractionOperation.UPDATE,
        )
    )
    await session.commit()

    provider = AgentRunsInboxProvider(session, svc_role)

    first_page = await provider.list_items(
        case_id=target_case.id,
        limit=1,
        order_by="created_at",
        sort="asc",
    )
    assert [item.source_id for item in first_page.items] == [first.id]
    assert first_page.has_more is True
    assert first_page.next_cursor is not None

    second_page = await provider.list_items(
        case_id=target_case.id,
        limit=1,
        cursor=first_page.next_cursor,
        order_by="created_at",
        sort="asc",
    )
    assert [item.source_id for item in second_page.items] == [second.id]
    assert second_page.has_more is False

    unfiltered = await provider.list_items(limit=10)
    assert unrelated.id in {item.source_id for item in unfiltered.items}
    isolated = await provider.list_items(case_id=foreign_case.id)
    assert isolated.items == []
