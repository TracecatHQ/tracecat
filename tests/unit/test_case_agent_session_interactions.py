"""Tests for durable case-to-agent-session interactions."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.auth.types import Role
from tracecat.cases.agent_sessions.service import (
    CaseAgentSessionInteractionService,
)
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
from tracecat.exceptions import TracecatNotFoundError, TracecatValidationError

pytestmark = [pytest.mark.anyio, pytest.mark.usefixtures("db")]


async def _create_case_and_session(
    session: AsyncSession,
    role: Role,
) -> tuple[Case, AgentSession]:
    assert role.workspace_id is not None
    case = Case(
        id=uuid.uuid4(),
        workspace_id=role.workspace_id,
        case_number=1,
        summary="Interaction test case",
        description="Case used to test agent-session interactions",
        priority=CasePriority.MEDIUM,
        severity=CaseSeverity.LOW,
        status=CaseStatus.NEW,
    )
    agent_session = AgentSession(
        id=uuid.uuid4(),
        workspace_id=role.workspace_id,
        title="Interaction test session",
        entity_type="workflow",
        entity_id=uuid.uuid4(),
    )
    session.add_all([case, agent_session])
    await session.commit()
    return case, agent_session


async def test_record_upserts_repeated_operation_and_preserves_first_seen(
    session: AsyncSession,
    svc_role: Role,
) -> None:
    case, agent_session = await _create_case_and_session(session, svc_role)
    service = CaseAgentSessionInteractionService(session, svc_role)

    first = await service.record(
        case_id=case.id,
        agent_session_id=agent_session.id,
        operation=CaseAgentSessionInteractionOperation.READ,
    )
    first_seen_at = first.created_at
    old_last_seen_at = datetime(2020, 1, 1, tzinfo=UTC)
    await session.execute(
        update(CaseAgentSessionInteraction)
        .where(CaseAgentSessionInteraction.id == first.id)
        .values(updated_at=old_last_seen_at)
    )

    repeated = await service.record(
        case_id=case.id,
        agent_session_id=agent_session.id,
        operation=CaseAgentSessionInteractionOperation.READ,
    )

    count = await session.scalar(
        select(func.count())
        .select_from(CaseAgentSessionInteraction)
        .where(CaseAgentSessionInteraction.case_id == case.id)
    )
    assert count == 1
    assert repeated.id == first.id
    assert repeated.created_at == first_seen_at
    assert repeated.updated_at > old_last_seen_at


async def test_record_preserves_distinct_operations(
    session: AsyncSession,
    svc_role: Role,
) -> None:
    case, agent_session = await _create_case_and_session(session, svc_role)
    service = CaseAgentSessionInteractionService(session, svc_role)

    for operation in (
        CaseAgentSessionInteractionOperation.READ,
        CaseAgentSessionInteractionOperation.CREATE,
        CaseAgentSessionInteractionOperation.UPDATE,
    ):
        await service.record(
            case_id=case.id,
            agent_session_id=agent_session.id,
            operation=operation,
        )

    operations = set(
        (
            await session.scalars(
                select(CaseAgentSessionInteraction.operation).where(
                    CaseAgentSessionInteraction.case_id == case.id
                )
            )
        ).all()
    )
    assert operations == {
        CaseAgentSessionInteractionOperation.READ,
        CaseAgentSessionInteractionOperation.CREATE,
        CaseAgentSessionInteractionOperation.UPDATE,
    }


async def test_record_normalizes_nested_fork_to_root_session(
    session: AsyncSession,
    svc_role: Role,
) -> None:
    case, root_session = await _create_case_and_session(session, svc_role)
    assert svc_role.workspace_id is not None
    child_session = AgentSession(
        id=uuid.uuid4(),
        workspace_id=svc_role.workspace_id,
        title="Child session",
        entity_type="approval",
        entity_id=uuid.uuid4(),
        parent_session_id=root_session.id,
    )
    grandchild_session = AgentSession(
        id=uuid.uuid4(),
        workspace_id=svc_role.workspace_id,
        title="Grandchild session",
        entity_type="approval",
        entity_id=uuid.uuid4(),
        parent_session_id=child_session.id,
    )
    session.add_all([child_session, grandchild_session])
    await session.commit()
    service = CaseAgentSessionInteractionService(session, svc_role)

    interaction = await service.record(
        case_id=case.id,
        agent_session_id=grandchild_session.id,
        operation=CaseAgentSessionInteractionOperation.UPDATE,
    )

    assert interaction.agent_session_id == root_session.id


async def test_record_does_not_commit_callers_transaction(
    session: AsyncSession,
    svc_role: Role,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case, agent_session = await _create_case_and_session(session, svc_role)
    service = CaseAgentSessionInteractionService(session, svc_role)
    commit = AsyncMock()
    monkeypatch.setattr(session, "commit", commit)

    await service.record(
        case_id=case.id,
        agent_session_id=agent_session.id,
        operation=CaseAgentSessionInteractionOperation.READ,
    )

    commit.assert_not_awaited()


async def test_record_rejects_cross_workspace_case_and_session(
    session: AsyncSession,
    svc_role: Role,
) -> None:
    own_case, own_session = await _create_case_and_session(session, svc_role)
    assert svc_role.organization_id is not None
    other_workspace = Workspace(
        id=uuid.uuid4(),
        name=f"interaction-test-{uuid.uuid4()}",
        organization_id=svc_role.organization_id,
    )
    session.add(other_workspace)
    await session.flush()
    other_case = Case(
        id=uuid.uuid4(),
        workspace_id=other_workspace.id,
        case_number=1,
        summary="Other workspace case",
        description="Case outside the service workspace",
        priority=CasePriority.MEDIUM,
        severity=CaseSeverity.LOW,
        status=CaseStatus.NEW,
    )
    other_session = AgentSession(
        id=uuid.uuid4(),
        workspace_id=other_workspace.id,
        title="Other workspace session",
        entity_type="workflow",
        entity_id=uuid.uuid4(),
    )
    session.add_all([other_case, other_session])
    await session.commit()
    service = CaseAgentSessionInteractionService(session, svc_role)

    with pytest.raises(TracecatNotFoundError):
        await service.record(
            case_id=other_case.id,
            agent_session_id=own_session.id,
            operation=CaseAgentSessionInteractionOperation.READ,
        )
    with pytest.raises(TracecatNotFoundError):
        await service.record(
            case_id=own_case.id,
            agent_session_id=other_session.id,
            operation=CaseAgentSessionInteractionOperation.READ,
        )

    count = await session.scalar(
        select(func.count()).select_from(CaseAgentSessionInteraction)
    )
    assert count == 0


async def test_root_resolution_rejects_session_lineage_cycle(
    session: AsyncSession,
    svc_role: Role,
) -> None:
    _, first_session = await _create_case_and_session(session, svc_role)
    assert svc_role.workspace_id is not None
    second_session = AgentSession(
        id=uuid.uuid4(),
        workspace_id=svc_role.workspace_id,
        title="Second cyclic session",
        entity_type="approval",
        entity_id=uuid.uuid4(),
        parent_session_id=first_session.id,
    )
    session.add(second_session)
    await session.commit()
    first_session.parent_session_id = second_session.id
    await session.commit()
    service = CaseAgentSessionInteractionService(session, svc_role)

    with pytest.raises(TracecatValidationError, match="lineage contains a cycle"):
        await service.resolve_root_session_id(first_session.id)
