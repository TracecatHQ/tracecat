from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, Mock

import orjson
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.agent.approvals.enums import ApprovalStatus
from tracecat.agent.session.service import AgentSessionService
from tracecat.agent.session.types import AgentSessionStatus
from tracecat.auth.types import Role
from tracecat.chat.enums import MessageKind
from tracecat.db.models import AgentSession, AgentSessionHistory


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
            _mock_scalar_result([compaction_entry]),
            _mock_scalar_result([]),
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
async def test_list_messages_adds_interrupt_notice_for_stopped_session() -> None:
    service, agent_session = _build_service()
    agent_session.status = AgentSessionStatus.STOPPED.value
    interrupt_entry = SimpleNamespace(
        id=uuid.uuid4(),
        session_id=agent_session.id,
        kind=MessageKind.INTERNAL.value,
        content={
            "type": "user",
            "uuid": "interrupt-uuid",
            "message": {
                "role": "user",
                "content": [{"type": "text", "text": "[Request interrupted by user]"}],
            },
        },
    )
    duplicate_interrupt_entry = SimpleNamespace(
        id=uuid.uuid4(),
        session_id=agent_session.id,
        kind=MessageKind.INTERNAL.value,
        content={
            "type": "user",
            "uuid": "interrupt-tool-result-uuid",
            "message": {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "call_123",
                        "is_error": True,
                        "content": "The user doesn't want to take this action right now.",
                    }
                ],
            },
        },
    )

    service.get_session = AsyncMock(return_value=agent_session)
    service.session.execute = AsyncMock(
        side_effect=[
            _mock_scalar_result([interrupt_entry, duplicate_interrupt_entry]),
            _mock_scalar_result([]),
        ]
    )

    messages = await service.list_messages(agent_session.id)

    assert len(messages) == 1
    assert messages[0].kind == MessageKind.INTERRUPT
    assert messages[0].interrupt == {"reason": "user_cancel"}


@pytest.mark.anyio
async def test_list_messages_adds_interrupt_notice_after_session_resumes() -> None:
    service, agent_session = _build_service()
    agent_session.status = AgentSessionStatus.IDLE.value
    interrupt_entry = SimpleNamespace(
        id=uuid.uuid4(),
        session_id=agent_session.id,
        kind=MessageKind.INTERNAL.value,
        content={
            "type": "user",
            "uuid": "interrupt-uuid",
            "message": {
                "role": "user",
                "content": [{"type": "text", "text": "[Request interrupted by user]"}],
            },
        },
    )

    service.get_session = AsyncMock(return_value=agent_session)
    service.session.execute = AsyncMock(
        side_effect=[
            _mock_scalar_result([interrupt_entry]),
            _mock_scalar_result([]),
        ]
    )

    messages = await service.list_messages(agent_session.id)

    assert len(messages) == 1
    assert messages[0].kind == MessageKind.INTERRUPT
    assert messages[0].interrupt == {"reason": "user_cancel"}


@pytest.mark.anyio
async def test_list_messages_keeps_pending_approval_interrupt_artifacts_hidden() -> (
    None
):
    service, agent_session = _build_service()
    agent_session.status = AgentSessionStatus.WAITING_FOR_APPROVAL.value
    assistant_entry = SimpleNamespace(
        id=uuid.uuid4(),
        session_id=agent_session.id,
        kind=MessageKind.CHAT_MESSAGE.value,
        content={
            "type": "assistant",
            "uuid": "assistant-uuid",
            "message": {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "call_123",
                        "name": "core__http_request",
                        "input": {"url": "https://example.com"},
                    }
                ],
            },
        },
    )
    interrupt_entry = SimpleNamespace(
        id=uuid.uuid4(),
        session_id=agent_session.id,
        kind=MessageKind.INTERNAL.value,
        content={
            "type": "user",
            "uuid": "interrupt-uuid",
            "message": {
                "role": "user",
                "content": [{"type": "text", "text": "[Request interrupted by user]"}],
            },
        },
    )
    pending_approval = SimpleNamespace(
        tool_call_id="call_123",
        status=ApprovalStatus.PENDING,
    )

    service.get_session = AsyncMock(return_value=agent_session)
    service.session.execute = AsyncMock(
        side_effect=[
            _mock_scalar_result([assistant_entry, interrupt_entry]),
            _mock_scalar_result([pending_approval]),
        ]
    )

    messages = await service.list_messages(
        agent_session.id,
        kinds=[MessageKind.INTERRUPT],
    )

    assert messages == []


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
            _mock_scalar_result(entries),
            _mock_scalar_result([]),
        ]
    )

    messages = await service.list_messages(session_id)

    assert len(messages) == 1
    assert messages[0].message is not None


def _chat_message_content(text: str) -> dict[str, Any]:
    return {
        "type": "assistant",
        "uuid": str(uuid.uuid4()),
        "message": {"role": "assistant", "content": [{"type": "text", "text": text}]},
    }


@pytest.mark.anyio
@pytest.mark.usefixtures("db")
async def test_list_messages_hides_active_run_rows_while_running(
    session: AsyncSession,
    svc_role: Role,
) -> None:
    """While RUNNING, the active run's rows are hidden (Redis owns them); rows
    from prior runs or without a run id stay visible."""
    active_run = uuid.uuid4()
    prior_run = uuid.uuid4()
    agent_session = AgentSession(
        id=uuid.uuid4(),
        workspace_id=svc_role.workspace_id,
        title="Chat",
        entity_type="case",
        entity_id=uuid.uuid4(),
        status=AgentSessionStatus.RUNNING.value,
        curr_run_id=active_run,
    )
    session.add(agent_session)
    for text, run_id in (
        ("prior turn", prior_run),
        ("legacy row", None),
        ("active partial", active_run),
    ):
        session.add(
            AgentSessionHistory(
                session_id=agent_session.id,
                workspace_id=svc_role.workspace_id,
                content=_chat_message_content(text),
                kind=MessageKind.CHAT_MESSAGE.value,
                curr_run_id=run_id,
            )
        )
    await session.commit()

    service = AgentSessionService(session=session, role=svc_role)
    texts = [
        getattr(part, "text", None)
        for m in await service.list_messages(agent_session.id)
        if m.message is not None
        for part in cast(Any, m.message).content
    ]

    assert "prior turn" in texts
    assert "legacy row" in texts
    assert "active partial" not in texts


@pytest.mark.anyio
@pytest.mark.usefixtures("db")
async def test_list_messages_keeps_waiting_for_approval_rows(
    session: AsyncSession,
    svc_role: Role,
) -> None:
    """WAITING_FOR_APPROVAL is DB-backed; fresh observers should load history."""
    active_run = uuid.uuid4()
    agent_session = AgentSession(
        id=uuid.uuid4(),
        workspace_id=svc_role.workspace_id,
        title="Chat",
        entity_type="case",
        entity_id=uuid.uuid4(),
        status=AgentSessionStatus.WAITING_FOR_APPROVAL.value,
        curr_run_id=active_run,
    )
    session.add(agent_session)
    session.add(
        AgentSessionHistory(
            session_id=agent_session.id,
            workspace_id=svc_role.workspace_id,
            content=_chat_message_content("approval is visible"),
            kind=MessageKind.CHAT_MESSAGE.value,
            curr_run_id=active_run,
        )
    )
    await session.commit()

    service = AgentSessionService(session=session, role=svc_role)
    texts = [
        getattr(part, "text", None)
        for m in await service.list_messages(agent_session.id)
        if m.message is not None
        for part in cast(Any, m.message).content
    ]

    assert "approval is visible" in texts
