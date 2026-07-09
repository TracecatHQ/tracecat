from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, Mock

import orjson
import pytest

from tracecat.agent.approvals.enums import ApprovalStatus
from tracecat.agent.session.service import AgentSessionService
from tracecat.auth.types import Role
from tracecat.chat.enums import MessageKind
from tracecat.db.models import AgentSession, Approval


def _mock_scalar_result(items: list[Any]) -> Mock:
    scalars = MagicMock()
    scalars.all.return_value = items
    scalars.__iter__.return_value = iter(items)
    result = Mock()
    result.scalars.return_value = scalars
    result.scalar_one_or_none.return_value = items[0] if items else None
    return result


def _build_service() -> tuple[AgentSessionService, AgentSession]:
    workspace_id = uuid.uuid4()
    role = Role(
        type="service",
        service_id="tracecat-api",
        workspace_id=workspace_id,
        organization_id=uuid.uuid4(),
        scopes=frozenset({"agent:execute"}),
    )
    session = SimpleNamespace()
    service = AgentSessionService(cast(Any, session), role)
    agent_session = AgentSession(
        workspace_id=workspace_id,
        title="Chat",
        created_by=None,
        entity_type="case",
        entity_id=uuid.uuid4(),
    )
    agent_session.id = uuid.uuid4()
    return service, agent_session


@pytest.mark.anyio
async def test_list_messages_preserves_compaction_metadata() -> None:
    service, agent_session = _build_service()
    compaction_entry = SimpleNamespace(
        id=uuid.uuid4(),
        kind=MessageKind.COMPACTION.value,
        content={
            "type": "system",
            "subtype": "compact_boundary",
            "compactMetadata": {
                "preTokens": 128000,
                "trigger": "auto",
            },
        },
    )

    service.get_session = AsyncMock(return_value=agent_session)
    service.session.execute = AsyncMock(
        side_effect=[
            _mock_scalar_result([]),
            _mock_scalar_result([compaction_entry]),
        ]
    )

    messages = await service.list_messages(agent_session.id)

    assert len(messages) == 1
    assert messages[0].kind == MessageKind.COMPACTION
    assert messages[0].compaction == {
        "phase": "completed",
        "pre_tokens": 128000,
    }


@pytest.mark.anyio
async def test_list_messages_maps_cancelled_marker() -> None:
    service, agent_session = _build_service()
    cancelled_entry = SimpleNamespace(
        id=uuid.uuid4(),
        kind=MessageKind.CANCELLED.value,
        content={
            "type": "cancelled",
            "reason": "user_cancel",
            "timestamp": "2026-07-02T00:00:00Z",
        },
    )

    service.get_session = AsyncMock(return_value=agent_session)
    service.session.execute = AsyncMock(
        side_effect=[
            _mock_scalar_result([]),
            _mock_scalar_result([cancelled_entry]),
        ]
    )

    messages = await service.list_messages(agent_session.id)

    assert len(messages) == 1
    assert messages[0].kind == MessageKind.CANCELLED
    assert messages[0].cancelled == {"reason": "user_cancel"}


@pytest.mark.anyio
async def test_load_session_history_omits_cancelled_marker_rows() -> None:
    service, _ = _build_service()
    session_id = uuid.uuid4()
    sdk_session = SimpleNamespace(
        id=session_id,
        parent_session_id=None,
        sdk_session_id="sdk-session-123",
        curr_run_id=None,
    )
    entries = [
        SimpleNamespace(
            kind=MessageKind.CHAT_MESSAGE.value,
            content={
                "type": "user",
                "uuid": "prompt-uuid",
                "message": {"role": "user", "content": "List cases."},
            },
        ),
        SimpleNamespace(
            kind=MessageKind.CANCELLED.value,
            content={
                "type": "cancelled",
                "reason": "user_cancel",
                "timestamp": "2026-07-02T00:00:00Z",
            },
        ),
        SimpleNamespace(
            kind=MessageKind.CHAT_MESSAGE.value,
            content={
                "type": "assistant",
                "uuid": "answer-uuid",
                "parentUuid": "prompt-uuid",
                "message": {"content": [{"type": "text", "text": "Stopped early."}]},
            },
        ),
    ]

    service.get_session = AsyncMock(return_value=sdk_session)
    service.session.execute = AsyncMock(return_value=_mock_scalar_result(entries))

    history = await service.load_session_history(session_id)

    assert history is not None
    lines = [orjson.loads(line) for line in history.sdk_session_data.splitlines()]
    assert [line["uuid"] for line in lines] == ["prompt-uuid", "answer-uuid"]
    assert "cancelled" not in history.sdk_session_data


@pytest.mark.anyio
async def test_load_session_history_omits_internal_rows_and_repairs_parent_chain() -> (
    None
):
    service, _ = _build_service()
    session_id = uuid.uuid4()
    sdk_session = SimpleNamespace(
        id=session_id,
        parent_session_id=None,
        sdk_session_id="sdk-session-123",
        curr_run_id=None,
    )
    tool_result_uuid = "tool-result-uuid"
    thinking_uuid = "thinking-uuid"
    answer_uuid = "answer-uuid"
    entries = [
        SimpleNamespace(
            kind=MessageKind.CHAT_MESSAGE.value,
            content={
                "type": "user",
                "uuid": tool_result_uuid,
                "message": {
                    "role": "user",
                    "content": [{"type": "tool_result", "tool_use_id": "call_123"}],
                },
            },
        ),
        SimpleNamespace(
            kind=MessageKind.INTERNAL.value,
            content={
                "type": "user",
                "uuid": "meta-uuid",
                "isMeta": True,
                "parentUuid": tool_result_uuid,
                "message": {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": "Continue from where you left off.",
                        }
                    ],
                },
            },
        ),
        SimpleNamespace(
            kind=MessageKind.INTERNAL.value,
            content={
                "type": "assistant",
                "uuid": "synthetic-uuid",
                "parentUuid": "meta-uuid",
                "message": {"model": "<synthetic>", "content": []},
            },
        ),
        SimpleNamespace(
            kind=MessageKind.INTERNAL.value,
            content={
                "type": "user",
                "uuid": "prompt-uuid",
                "parentUuid": "synthetic-uuid",
                "message": {"role": "user", "content": "Continue."},
            },
        ),
        SimpleNamespace(
            kind=MessageKind.INTERNAL.value,
            content={
                "type": "assistant",
                "uuid": thinking_uuid,
                "parentUuid": "prompt-uuid",
                "message": {
                    "content": [
                        {
                            "type": "thinking",
                            "thinking": "Saw hidden continuation prompts.",
                        }
                    ]
                },
            },
        ),
        SimpleNamespace(
            kind=MessageKind.CHAT_MESSAGE.value,
            content={
                "type": "assistant",
                "uuid": answer_uuid,
                "parentUuid": thinking_uuid,
                "message": {
                    "content": [{"type": "text", "text": "There are no cases."}]
                },
            },
        ),
    ]

    service.get_session = AsyncMock(return_value=sdk_session)
    service.session.execute = AsyncMock(return_value=_mock_scalar_result(entries))

    history = await service.load_session_history(session_id)

    assert history is not None
    assert history.sdk_session_id == "sdk-session-123"
    lines = [orjson.loads(line) for line in history.sdk_session_data.splitlines()]
    assert [line["uuid"] for line in lines] == [tool_result_uuid, answer_uuid]
    assert lines[1]["parentUuid"] == tool_result_uuid
    assert "Continue" not in history.sdk_session_data


@pytest.mark.anyio
async def test_list_messages_skips_misclassified_continuation_artifacts() -> None:
    service, _ = _build_service()
    session_id = uuid.uuid4()
    agent_session = SimpleNamespace(
        id=session_id,
        parent_session_id=None,
        curr_run_id=None,
    )
    prompt_uuid = "prompt-uuid"
    thinking_uuid = "thinking-uuid"
    entries = [
        SimpleNamespace(
            id=uuid.uuid4(),
            kind=MessageKind.INTERNAL.value,
            content={
                "type": "assistant",
                "uuid": "synthetic-uuid",
                "message": {"model": "<synthetic>", "content": []},
            },
        ),
        SimpleNamespace(
            id=uuid.uuid4(),
            kind=MessageKind.CHAT_MESSAGE.value,
            content={
                "type": "user",
                "uuid": prompt_uuid,
                "parentUuid": "synthetic-uuid",
                "message": {"role": "user", "content": "Continue."},
            },
        ),
        SimpleNamespace(
            id=uuid.uuid4(),
            kind=MessageKind.CHAT_MESSAGE.value,
            content={
                "type": "assistant",
                "uuid": thinking_uuid,
                "parentUuid": prompt_uuid,
                "message": {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "thinking",
                            "thinking": "Saw hidden continuation prompts.",
                        }
                    ],
                },
            },
        ),
        SimpleNamespace(
            id=uuid.uuid4(),
            kind=MessageKind.CHAT_MESSAGE.value,
            content={
                "type": "assistant",
                "uuid": "answer-uuid",
                "parentUuid": thinking_uuid,
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "There are no cases."}],
                },
            },
        ),
    ]

    service.get_session = AsyncMock(return_value=agent_session)
    service.session.execute = AsyncMock(
        side_effect=[
            _mock_scalar_result([]),
            _mock_scalar_result(entries),
        ]
    )

    messages = await service.list_messages(session_id)

    assert len(messages) == 1
    assert messages[0].message is not None


def _approval(
    *,
    session_id: uuid.UUID,
    workspace_id: uuid.UUID,
    tool_call_id: str,
    status: ApprovalStatus,
) -> Approval:
    now = datetime.now(UTC)
    approval = Approval(
        workspace_id=workspace_id,
        session_id=session_id,
        tool_call_id=tool_call_id,
        tool_name="core.http_request",
        tool_call_args={"url": "https://example.com"},
        status=status,
    )
    approval.id = uuid.uuid4()
    approval.decision = True if status is not ApprovalStatus.PENDING else None
    approval.reason = None
    approval.approved_by = None
    approval.approved_at = None
    approval.created_at = now
    approval.updated_at = now
    return approval


def _assistant_tool_use_entry(
    *,
    curr_run_id: uuid.UUID | None,
    tool_call_id: str,
    surrogate_id: int = 1,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        surrogate_id=surrogate_id,
        kind=MessageKind.CHAT_MESSAGE.value,
        curr_run_id=curr_run_id,
        content={
            "type": "assistant",
            "uuid": f"assistant-{tool_call_id}",
            "message": {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": tool_call_id,
                        "name": "core.http_request",
                        "input": {"url": "https://example.com"},
                    }
                ],
            },
        },
    )


def _assistant_text_entry(
    *, curr_run_id: uuid.UUID | None, text: str, surrogate_id: int
) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        surrogate_id=surrogate_id,
        kind=MessageKind.CHAT_MESSAGE.value,
        curr_run_id=curr_run_id,
        content={
            "type": "assistant",
            "uuid": f"assistant-{surrogate_id}",
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": text}],
            },
        },
    )


@pytest.mark.anyio
async def test_list_messages_shows_active_rows_when_run_has_pending_approval() -> None:
    """A turn paused on a tool-approval interrupt renders from DB.

    The active run's partial rows are normally hidden mid-turn, but when the run
    has an approval (correlated by tool_call_id) they become visible so the
    paused turn is not a blank pane.
    """
    service, _ = _build_service()
    workspace_id = service.workspace_id
    assert workspace_id is not None
    session_id = uuid.uuid4()
    run_id = uuid.uuid4()
    agent_session = SimpleNamespace(
        id=session_id, parent_session_id=None, curr_run_id=run_id
    )
    entry = _assistant_tool_use_entry(curr_run_id=run_id, tool_call_id="tool_call_1")
    approval = _approval(
        session_id=session_id,
        workspace_id=workspace_id,
        tool_call_id="tool_call_1",
        status=ApprovalStatus.PENDING,
    )

    service.get_session = AsyncMock(return_value=agent_session)
    service.session.execute = AsyncMock(
        side_effect=[
            _mock_scalar_result([approval]),
            _mock_scalar_result([entry]),
            _mock_scalar_result([entry]),
        ]
    )

    messages = await service.list_messages(session_id)

    kinds = [m.kind for m in messages]
    # Assistant tool_use row is visible, plus the interleaved approval bubble.
    assert any(m.message is not None for m in messages)
    assert MessageKind.APPROVAL_REQUEST in kinds


@pytest.mark.anyio
async def test_list_messages_shows_active_rows_when_run_approval_resolved() -> None:
    """After continuation resolves the approval, active rows stay visible.

    A cold mount mid-continuation must show the DB prefix (this row) plus a
    suffix-only fresh stream, so resolved approvals keep the rows unhidden.
    """
    service, _ = _build_service()
    workspace_id = service.workspace_id
    assert workspace_id is not None
    session_id = uuid.uuid4()
    run_id = uuid.uuid4()
    agent_session = SimpleNamespace(
        id=session_id, parent_session_id=None, curr_run_id=run_id
    )
    entry = _assistant_tool_use_entry(curr_run_id=run_id, tool_call_id="tool_call_1")
    approval = _approval(
        session_id=session_id,
        workspace_id=workspace_id,
        tool_call_id="tool_call_1",
        status=ApprovalStatus.APPROVED,
    )

    service.get_session = AsyncMock(return_value=agent_session)
    service.session.execute = AsyncMock(
        side_effect=[
            _mock_scalar_result([approval]),
            _mock_scalar_result([entry]),
            _mock_scalar_result([entry]),
        ]
    )

    messages = await service.list_messages(session_id)

    kinds = [m.kind for m in messages]
    assert any(m.message is not None for m in messages)
    # Resolved approval renders both the request and the decision bubble.
    assert MessageKind.APPROVAL_REQUEST in kinds
    assert MessageKind.APPROVAL_DECISION in kinds


@pytest.mark.anyio
async def test_list_messages_hides_active_continuation_rows_after_approval() -> None:
    """The DB supplies only the prefix while Redis replays the live suffix."""
    service, _ = _build_service()
    workspace_id = service.workspace_id
    assert workspace_id is not None
    session_id = uuid.uuid4()
    run_id = uuid.uuid4()
    agent_session = SimpleNamespace(
        id=session_id, parent_session_id=None, curr_run_id=run_id
    )
    approval_entry = _assistant_tool_use_entry(
        curr_run_id=run_id,
        tool_call_id="tool_call_1",
        surrogate_id=1,
    )
    continuation_entry = _assistant_text_entry(
        curr_run_id=run_id,
        text="This suffix is already in Redis.",
        surrogate_id=2,
    )
    approval = _approval(
        session_id=session_id,
        workspace_id=workspace_id,
        tool_call_id="tool_call_1",
        status=ApprovalStatus.APPROVED,
    )

    service.get_session = AsyncMock(return_value=agent_session)
    service.session.execute = AsyncMock(
        side_effect=[
            _mock_scalar_result([approval]),
            _mock_scalar_result([approval_entry, continuation_entry]),
            _mock_scalar_result([approval_entry]),
        ]
    )

    messages = await service.list_messages(session_id)

    rendered_message_ids = {m.id for m in messages if m.message is not None}
    assert str(approval_entry.id) in rendered_message_ids
    assert str(continuation_entry.id) not in rendered_message_ids


@pytest.mark.anyio
async def test_list_messages_hides_active_rows_without_approvals() -> None:
    """A normal live turn (no approvals) keeps active-run rows hidden.

    Mid-turn the live assistant streams from Redis, so the active run's partial
    rows must stay hidden to avoid a duplicate source.
    """
    service, _ = _build_service()
    session_id = uuid.uuid4()
    run_id = uuid.uuid4()
    agent_session = SimpleNamespace(
        id=session_id, parent_session_id=None, curr_run_id=run_id
    )
    entry = _assistant_tool_use_entry(curr_run_id=run_id, tool_call_id="tool_call_1")

    service.get_session = AsyncMock(return_value=agent_session)
    service.session.execute = AsyncMock(
        side_effect=[
            _mock_scalar_result([]),
            _mock_scalar_result([entry]),
        ]
    )

    messages = await service.list_messages(session_id)

    assert messages == []


@pytest.mark.anyio
async def test_list_messages_does_not_unhide_active_rows_for_prior_run_approval() -> (
    None
):
    """A prior turn's approval must not unhide a fresh clean run's rows.

    The Approval model has no run_id, so we run-scope by tool_call_id: an
    approval only unhides the current run when one of the current run's rows
    issued that tool_use. A resolved approval from an earlier run (whose
    tool_call_id belongs to a different, non-current run's rows) leaves the fresh
    current-run rows hidden.
    """
    service, _ = _build_service()
    workspace_id = service.workspace_id
    assert workspace_id is not None
    session_id = uuid.uuid4()
    prior_run_id = uuid.uuid4()
    current_run_id = uuid.uuid4()
    agent_session = SimpleNamespace(
        id=session_id, parent_session_id=None, curr_run_id=current_run_id
    )
    # Prior run's resolved approval + its tool_use row (a completed earlier turn).
    prior_entry = _assistant_tool_use_entry(
        curr_run_id=prior_run_id, tool_call_id="prior_tool_call"
    )
    prior_approval = _approval(
        session_id=session_id,
        workspace_id=workspace_id,
        tool_call_id="prior_tool_call",
        status=ApprovalStatus.APPROVED,
    )
    # Current run's fresh partial row issues a DIFFERENT tool_call with no approval.
    current_entry = _assistant_tool_use_entry(
        curr_run_id=current_run_id, tool_call_id="current_tool_call"
    )

    service.get_session = AsyncMock(return_value=agent_session)
    service.session.execute = AsyncMock(
        side_effect=[
            _mock_scalar_result([prior_approval]),
            _mock_scalar_result([current_entry]),
            _mock_scalar_result([prior_entry, current_entry]),
        ]
    )

    messages = await service.list_messages(session_id)

    # Prior run's row (and its approval bubbles) are visible; the current run's
    # fresh row stays hidden because its tool_call has no approval.
    rendered_message_ids = {m.id for m in messages if m.message is not None}
    assert str(prior_entry.id) in rendered_message_ids
    assert str(current_entry.id) not in rendered_message_ids
