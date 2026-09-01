"""Integration coverage for historical case-agent interaction backfills."""

from __future__ import annotations

import uuid
from typing import Any

import orjson
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.agent.session.history import prepare_session_history
from tracecat.agent.subagents import (
    ResolvedAgentsConfig,
    ResolvedAttachedSubagentRef,
)
from tracecat.auth.types import Role
from tracecat.cases.agent_sessions.backfill import CaseAgentSessionBackfill
from tracecat.cases.agent_sessions.types import CaseAgentSessionBackfillSkipReason
from tracecat.cases.enums import (
    CaseAgentSessionInteractionOperation,
    CasePriority,
    CaseSeverity,
    CaseStatus,
)
from tracecat.db.models import (
    AgentSession,
    AgentSessionHistory,
    Case,
    CaseAgentSessionInteraction,
    CaseComment,
)

pytestmark = [
    pytest.mark.anyio,
    pytest.mark.integration,
    pytest.mark.usefixtures("db"),
]


def _case(workspace_id: uuid.UUID, case_number: int, summary: str) -> Case:
    return Case(
        workspace_id=workspace_id,
        case_number=case_number,
        summary=summary,
        description=f"Description for {summary}",
        status=CaseStatus.NEW,
        priority=CasePriority.MEDIUM,
        severity=CaseSeverity.LOW,
    )


def _use(tool_call_id: str, name: str, **arguments: object) -> dict[str, object]:
    return {
        "type": "tool_use",
        "id": tool_call_id,
        "name": name,
        "input": arguments,
    }


def _result(
    tool_call_id: str,
    content: object = '{"success":true}',
) -> dict[str, object]:
    return {
        "type": "tool_result",
        "tool_use_id": tool_call_id,
        "content": content,
    }


def _history(
    agent_session: AgentSession,
    message_type: str,
    blocks: list[dict[str, object]],
) -> AgentSessionHistory:
    content: dict[str, Any] = {
        "type": message_type,
        "message": {"content": blocks},
    }
    prepared = prepare_session_history(content, raw_session_line=orjson.dumps(content))
    return AgentSessionHistory(
        workspace_id=agent_session.workspace_id,
        session_id=agent_session.id,
        content=prepared.content,
        raw_session_line=prepared.raw_session_line,
        kind="internal",
    )


def _turn(
    agent_session: AgentSession,
    uses: list[dict[str, object]],
    results: list[dict[str, object]],
) -> list[AgentSessionHistory]:
    return [
        _history(agent_session, "assistant", uses),
        _history(agent_session, "user", results),
    ]


async def _interactions(
    session: AsyncSession,
    workspace_id: uuid.UUID,
) -> set[tuple[uuid.UUID, CaseAgentSessionInteractionOperation, uuid.UUID]]:
    rows = (
        await session.execute(
            select(
                CaseAgentSessionInteraction.case_id,
                CaseAgentSessionInteraction.operation,
                CaseAgentSessionInteraction.agent_session_id,
            ).where(CaseAgentSessionInteraction.workspace_id == workspace_id)
        )
    ).tuples()
    return set(rows)


async def test_backfill_reconstructs_mutations_safely_and_idempotently(
    session: AsyncSession,
    svc_role: Role,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exercise the backfill as one bounded, database-backed workflow."""
    monkeypatch.setattr(
        "tracecat.cases.agent_sessions.backfill._HISTORY_FETCH_SIZE",
        1,
    )
    monkeypatch.setattr(
        "tracecat.cases.agent_sessions.backfill._INTERACTION_INSERT_BATCH_SIZE",
        1,
    )
    monkeypatch.setattr(
        "tracecat.cases.agent_sessions.backfill._TARGET_LOOKUP_BATCH_SIZE",
        1,
    )
    assert svc_role.workspace_id is not None
    workspace_id = svc_role.workspace_id
    created, updated, commented, edited, associated = [
        _case(workspace_id, 101 + index, summary)
        for index, summary in enumerate(
            ["Created", "Updated", "Commented", "Edited", "Entity only"]
        )
    ]
    session.add_all([created, updated, commented, edited, associated])
    await session.flush()

    edited_comment = CaseComment(
        workspace_id=workspace_id,
        case_id=edited.id,
        content="Original comment",
    )
    root = AgentSession(
        workspace_id=workspace_id,
        title="Root",
        entity_type="workflow",
        entity_id=uuid.uuid4(),
    )
    session.add_all([edited_comment, root])
    await session.flush()
    child = AgentSession(
        workspace_id=workspace_id,
        title="Continuation",
        entity_type="approval",
        entity_id=uuid.uuid4(),
        parent_session=root,
        agents_binding=ResolvedAgentsConfig(
            enabled=True,
            subagents=[
                ResolvedAttachedSubagentRef(
                    preset="analyst",
                    name="analyst",
                    preset_id=uuid.uuid4(),
                    preset_version_id=uuid.uuid4(),
                    preset_version=1,
                )
            ],
        ).model_dump(mode="json"),
    )
    entity_only = AgentSession(
        workspace_id=workspace_id,
        title="Case entity without mutations",
        entity_type="case",
        entity_id=associated.id,
    )
    session.add_all([child, entity_only])
    await session.flush()

    session.add(
        CaseAgentSessionInteraction(
            workspace_id=workspace_id,
            case_id=updated.id,
            agent_session_id=root.id,
            operation=CaseAgentSessionInteractionOperation.UPDATE,
        )
    )
    session.add_all(
        _turn(
            root,
            [
                _use(
                    "root-update",
                    "core.cases.update_case",
                    case_id=str(updated.id),
                )
            ],
            [_result("root-update")],
        )
    )

    uses = [
        _use(
            "create",
            "mcp__tracecat-registry__core__cases__create_case",
            summary="Created",
        ),
        _use(
            "legacy-update",
            "mcp__tracecat_registry__execute_tool",
            tool_name="core.cases.update_case",
            case_id=str(updated.id),
        ),
        _use(
            "comment",
            "mcp.tracecat-registry.core.cases.create_comment",
            case_id=str(commented.id),
            content="Contains\x00NUL",
        ),
        _use(
            "edit-comment",
            "core.cases.update_comment",
            comment_id=str(edited_comment.id),
        ),
        _use(
            "failed",
            "core.cases.update_case",
            case_id=str(updated.id),
        ),
        _use(
            "external-update",
            "mcp__tracecat-registry-fake__core__cases__update_case",
            case_id=str(updated.id),
        ),
        _use(
            "subagent-update",
            "mcp__tracecat-registry-analyst__core__cases__update_case",
            case_id=str(updated.id),
        ),
        _use(
            "child-mutation",
            "core.cases.add_case_tag",
            case_id=str(associated.id),
            tag="agent-mutated",
        ),
        _use(
            "incomplete",
            "core.cases.create_comment",
            case_id=str(commented.id),
        ),
        _use(
            "bad-result",
            "core.cases.create_case",
            summary="Unparseable result",
        ),
        _use("read", "core.cases.list_cases"),
    ]
    results = [
        _result("bad-result", "not JSON"),
        _result(
            "failed",
            [{"type": "text", "text": '{"success":false,"error":"failure"}'}],
        ),
        _result("edit-comment"),
        _result("comment"),
        _result("legacy-update"),
        _result("external-update"),
        _result("subagent-update"),
        _result("child-mutation"),
        _result(
            "create",
            [{"type": "text", "text": f'{{"id":"{created.id}"}}'}],
        ),
    ]
    child_history = _turn(child, uses, results)
    assert child_history[0].raw_session_line is not None
    session.add_all(child_history)
    session.add(
        _history(
            entity_only,
            "user",
            [{"type": "text", "text": "Summarize this case"}],
        )
    )
    await session.commit()

    backfill = CaseAgentSessionBackfill(session)
    assert await _interactions(session, workspace_id) == {
        (updated.id, CaseAgentSessionInteractionOperation.UPDATE, root.id)
    }
    progress_calls = 0

    def on_progress() -> None:
        nonlocal progress_calls
        progress_calls += 1

    applied = await backfill.run(batch_size=1, on_progress=on_progress)
    rerun = await backfill.run(batch_size=1)

    assert applied.batches_processed == 3
    assert applied.sessions_scanned == 3
    assert applied.history_rows_scanned == 5
    assert progress_calls == applied.history_rows_scanned + applied.batches_processed
    assert applied.mutation_candidates == 7
    assert (applied.inserted, applied.existing) == (4, 2)
    assert (rerun.inserted, rerun.existing) == (0, 6)
    expected_skips = {
        CaseAgentSessionBackfillSkipReason.FAILED_TOOL_CALL: 1,
        CaseAgentSessionBackfillSkipReason.INCOMPLETE_TOOL_CALL: 1,
        CaseAgentSessionBackfillSkipReason.UNPARSEABLE_TOOL_CALL: 1,
    }
    assert applied.skipped == rerun.skipped == expected_skips
    assert await _interactions(session, workspace_id) == {
        (created.id, CaseAgentSessionInteractionOperation.CREATE, root.id),
        (updated.id, CaseAgentSessionInteractionOperation.UPDATE, root.id),
        (commented.id, CaseAgentSessionInteractionOperation.UPDATE, root.id),
        (edited.id, CaseAgentSessionInteractionOperation.UPDATE, root.id),
        (associated.id, CaseAgentSessionInteractionOperation.UPDATE, root.id),
    }
