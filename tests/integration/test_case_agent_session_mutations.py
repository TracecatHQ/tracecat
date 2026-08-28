"""End-to-end persistence tests for agent case mutation interactions."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import event, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.auth.types import Role
from tracecat.cases.enums import (
    CaseAgentSessionInteractionOperation,
    CasePriority,
    CaseSeverity,
    CaseStatus,
)
from tracecat.cases.schemas import (
    CaseCommentCreate,
    CaseCommentUpdate,
    CaseCreate,
    CaseUpdate,
)
from tracecat.cases.service import CaseCommentsService, CasesService
from tracecat.contexts import ctx_agent_session_id
from tracecat.db.models import (
    AgentSession,
    Case,
    CaseAgentSessionInteraction,
    CaseComment,
    Workspace,
)

pytestmark = [
    pytest.mark.anyio,
    pytest.mark.integration,
    pytest.mark.usefixtures("db"),
]


@pytest.fixture(autouse=True)
def isolate_case_side_effects() -> Iterator[None]:
    """Keep the integration focused on case and interaction persistence."""
    with (
        patch.object(
            CasesService,
            "has_entitlement",
            new=AsyncMock(return_value=True),
        ),
        patch(
            "tracecat.cases.events.enqueue_case_duration_sync_after_commit",
            return_value=None,
        ),
        patch(
            "tracecat.cases.events.publish_case_event_payload",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "tracecat.cases.service.publish_case_event_payload",
            new=AsyncMock(return_value=None),
        ),
    ):
        yield


def _case_create(summary: str) -> CaseCreate:
    return CaseCreate(
        summary=summary,
        description=f"Description for {summary}",
        status=CaseStatus.NEW,
        priority=CasePriority.MEDIUM,
        severity=CaseSeverity.LOW,
    )


async def _create_session_lineage(
    session: AsyncSession,
    role: Role,
) -> tuple[AgentSession, AgentSession]:
    assert role.workspace_id is not None
    root = AgentSession(
        workspace_id=role.workspace_id,
        title="Case interaction root",
        entity_type="workflow",
        entity_id=uuid.uuid4(),
    )
    child = AgentSession(
        workspace_id=role.workspace_id,
        title="Case interaction continuation",
        entity_type="approval",
        entity_id=uuid.uuid4(),
        parent_session=root,
    )
    session.add_all([root, child])
    await session.commit()
    return root, child


async def _list_interactions(
    session: AsyncSession,
    workspace_id: uuid.UUID,
) -> list[CaseAgentSessionInteraction]:
    result = await session.scalars(
        select(CaseAgentSessionInteraction)
        .where(CaseAgentSessionInteraction.workspace_id == workspace_id)
        .order_by(
            CaseAgentSessionInteraction.case_id,
            CaseAgentSessionInteraction.operation,
        )
    )
    return list(result.all())


async def test_agent_case_and_comment_mutations_record_root_interactions(
    session: AsyncSession,
    svc_role: Role,
) -> None:
    """Case and comment mutations upsert case-level root-session interactions."""
    assert svc_role.workspace_id is not None
    root, child = await _create_session_lineage(session, svc_role)
    cases = CasesService(session=session, role=svc_role)
    comments = CaseCommentsService(session=session, role=svc_role)
    read_then_no_op_case = await cases.create_case(_case_create("Read then no-op case"))

    context_token = ctx_agent_session_id.set(child.id)
    try:
        assert await cases.get_case(read_then_no_op_case.id) is not None
        assert await _list_interactions(session, svc_role.workspace_id) == []
        await cases.update_case(read_then_no_op_case, CaseUpdate())

        first_case = await cases.create_case(_case_create("First case"))
        await cases.update_case(
            first_case,
            CaseUpdate(summary="Updated first case"),
        )
        await cases.update_case(
            first_case,
            CaseUpdate(summary="Updated first case"),
        )
        comment = await comments.create_comment(
            first_case,
            CaseCommentCreate(content="Agent comment"),
        )
        await comments.update_comment(
            comment,
            CaseCommentUpdate(content="Edited agent comment"),
        )

        second_case = await cases.create_case(_case_create("Second case"))
        await comments.create_comment(
            second_case,
            CaseCommentCreate(content="Second case comment"),
        )

        third_case = await cases.create_case(_case_create("Third case"))
        batch_response = await cases.batch_update_cases(
            [third_case.id],
            CaseUpdate(status=CaseStatus.IN_PROGRESS),
        )
        assert batch_response.succeeded == 1
    finally:
        ctx_agent_session_id.reset(context_token)

    interactions = await _list_interactions(session, svc_role.workspace_id)
    assert {
        (interaction.case_id, interaction.operation, interaction.agent_session_id)
        for interaction in interactions
    } == {
        (
            read_then_no_op_case.id,
            CaseAgentSessionInteractionOperation.UPDATE,
            root.id,
        ),
        (
            first_case.id,
            CaseAgentSessionInteractionOperation.CREATE,
            root.id,
        ),
        (
            first_case.id,
            CaseAgentSessionInteractionOperation.UPDATE,
            root.id,
        ),
        (
            second_case.id,
            CaseAgentSessionInteractionOperation.CREATE,
            root.id,
        ),
        (
            second_case.id,
            CaseAgentSessionInteractionOperation.UPDATE,
            root.id,
        ),
        (
            third_case.id,
            CaseAgentSessionInteractionOperation.CREATE,
            root.id,
        ),
        (
            third_case.id,
            CaseAgentSessionInteractionOperation.UPDATE,
            root.id,
        ),
    }


async def test_non_agent_case_and_comment_mutations_do_not_record(
    session: AsyncSession,
    svc_role: Role,
) -> None:
    """Normal calls preserve case behavior without creating interactions."""
    assert svc_role.workspace_id is not None
    cases = CasesService(session=session, role=svc_role)
    comments = CaseCommentsService(session=session, role=svc_role)

    context_token = ctx_agent_session_id.set(None)
    try:
        case = await cases.create_case(_case_create("Non-agent case"))
        await cases.update_case(case, CaseUpdate(status=CaseStatus.IN_PROGRESS))
        comment = await comments.create_comment(
            case,
            CaseCommentCreate(content="Normal comment"),
        )
        await comments.update_comment(
            comment,
            CaseCommentUpdate(content="Edited normal comment"),
        )
    finally:
        ctx_agent_session_id.reset(context_token)

    assert await _list_interactions(session, svc_role.workspace_id) == []


async def test_missing_agent_session_does_not_block_case_creation(
    session: AsyncSession,
    svc_role: Role,
) -> None:
    """A stale provenance ID does not veto the requested case mutation."""
    assert svc_role.workspace_id is not None
    cases = CasesService(session=session, role=svc_role)

    context_token = ctx_agent_session_id.set(uuid.uuid4())
    try:
        await cases.create_case(_case_create("Stale agent session"))
    finally:
        ctx_agent_session_id.reset(context_token)

    assert await _list_interactions(session, svc_role.workspace_id) == []


async def test_failed_case_and_comment_transactions_leave_no_interactions(
    session: AsyncSession,
    svc_role: Role,
    svc_workspace: Workspace,
) -> None:
    """Commit failures leave neither mutations nor interaction upserts."""
    assert svc_role.workspace_id is not None
    _, child = await _create_session_lineage(session, svc_role)
    child_id = child.id
    cases = CasesService(session=session, role=svc_role)
    comments = CaseCommentsService(session=session, role=svc_role)
    target_case = await cases.create_case(_case_create("Rollback target"))
    target_case_id = target_case.id
    original_summary = target_case.summary

    class ExpectedCommitFailure(RuntimeError):
        pass

    def fail_commit(*_: object) -> None:
        raise ExpectedCommitFailure

    event.listen(session.sync_session, "before_commit", fail_commit)

    context_token = ctx_agent_session_id.set(child_id)
    try:
        with pytest.raises(ExpectedCommitFailure):
            await cases.update_case(
                target_case,
                CaseUpdate(summary="Must roll back"),
            )
    finally:
        ctx_agent_session_id.reset(context_token)
        event.remove(session.sync_session, "before_commit", fail_commit)

    refreshed_case = await session.scalar(select(Case).where(Case.id == target_case_id))
    assert refreshed_case is not None
    assert refreshed_case.summary == original_summary
    assert await _list_interactions(session, svc_role.workspace_id) == []

    event.listen(session.sync_session, "before_commit", fail_commit)

    context_token = ctx_agent_session_id.set(child_id)
    try:
        with pytest.raises(ExpectedCommitFailure):
            await comments.create_comment(
                refreshed_case,
                CaseCommentCreate(content="Must also roll back"),
            )
    finally:
        ctx_agent_session_id.reset(context_token)
        event.remove(session.sync_session, "before_commit", fail_commit)
        await session.rollback()

    interaction_count = await session.scalar(
        select(func.count())
        .select_from(CaseAgentSessionInteraction)
        .where(CaseAgentSessionInteraction.workspace_id == svc_role.workspace_id)
    )
    comment_count = await session.scalar(
        select(func.count())
        .select_from(CaseComment)
        .where(
            CaseComment.workspace_id == svc_role.workspace_id,
            CaseComment.content == "Must also roll back",
        )
    )
    assert interaction_count == 0
    assert comment_count == 0
    await session.refresh(svc_workspace)
