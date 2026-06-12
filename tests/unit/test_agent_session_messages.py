from __future__ import annotations

import contextlib
import uuid
from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, Mock

import orjson
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.agent.executor.loopback import (
    LoopbackHandler,
    LoopbackInput,
    _session_line_db_content,
    _session_line_from_json,
    _session_line_jsonb_safe_content,
)
from tracecat.agent.session.service import AgentSessionService
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


def test_session_line_db_content_sanitizes_nul_only_for_retry() -> None:
    session_line = (
        r'{"type":"user","uuid":"line-uuid","bad\u0000key":"v",'
        r'"message":{"role":"user",'
        r'"content":"hello\u0000world"},"toolUseResult":{"stdout":"a\u0000b"}}'
    )

    line = _session_line_from_json(session_line)

    content = _session_line_db_content(line)
    assert content["bad\x00key"] == "v"
    assert content["message"]["content"] == "hello\x00world"
    assert content["toolUseResult"]["stdout"] == "a\x00b"

    safe_content = _session_line_jsonb_safe_content(line)
    assert safe_content[r"bad\u0000key"] == "v"
    assert safe_content["message"]["content"] == r"hello\u0000world"
    assert safe_content["toolUseResult"]["stdout"] == r"a\u0000b"
    assert "\x00" not in orjson.dumps(safe_content).decode("utf-8")


@pytest.mark.anyio
async def test_persist_session_line_lazily_sanitizes_jsonb_nul_failure(
    session: AsyncSession,
    svc_role: Role,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_id = svc_role.workspace_id
    assert workspace_id is not None

    agent_session = AgentSession(
        id=uuid.uuid4(),
        workspace_id=workspace_id,
        title="Chat",
        created_by=None,
        entity_type="case",
        entity_id=uuid.uuid4(),
    )
    session.add(agent_session)
    await session.commit()

    @contextlib.asynccontextmanager
    async def patched_bypass_session() -> AsyncIterator[AsyncSession]:
        yield session

    monkeypatch.setattr(
        "tracecat.agent.executor.loopback.get_async_session_bypass_rls_context_manager",
        lambda: patched_bypass_session(),
    )

    raw_line = (
        r'{"type":"user","uuid":"line-uuid","message":{"role":"user",'
        r'"content":[{"type":"text","text":"hello\u0000world"},'
        r'{"type":"text","text":"literal\\u0000text"}]},'
        r'"toolUseResult":{"stdout":"a\u0000b"}}'
    )

    handler = LoopbackHandler(
        input=LoopbackInput(
            session_id=agent_session.id,
            workspace_id=workspace_id,
        )
    )
    await handler._persist_session_line("sdk-session-123", raw_line)

    result = await session.execute(
        select(AgentSessionHistory).where(
            AgentSessionHistory.session_id == agent_session.id
        )
    )
    persisted = result.scalar_one()
    message_content = persisted.content["message"]["content"]

    assert isinstance(message_content, list)
    assert message_content[0]["text"] == r"hello\u0000world"
    assert message_content[1]["text"] == r"literal\u0000text"
    assert persisted.content["toolUseResult"]["stdout"] == r"a\u0000b"
    assert persisted.kind == MessageKind.CHAT_MESSAGE.value
    assert handler._sdk_session_id == "sdk-session-123"
    assert "line-uuid" in handler._persisted_line_uuids

    session_result = await session.execute(
        select(AgentSession).where(AgentSession.id == agent_session.id)
    )
    persisted_session = session_result.scalar_one()
    assert persisted_session.sdk_session_id == "sdk-session-123"


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
