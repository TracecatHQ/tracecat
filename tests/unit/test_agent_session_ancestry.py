"""Session ancestry and fork migration behavior without external services."""

from __future__ import annotations

import importlib.util
import uuid
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, Mock, patch

import pytest
from fastapi import HTTPException, status
from sqlalchemy import create_engine, text

from tracecat.agent.adapter.vercel import UIMessage
from tracecat.agent.session.router import get_session_vercel, send_message
from tracecat.agent.session.schemas import AgentSessionCreate
from tracecat.agent.session.service import AgentSessionService
from tracecat.agent.session.types import AgentSessionEntity
from tracecat.auth.types import Role
from tracecat.chat.enums import MessageKind
from tracecat.chat.schemas import (
    ApprovalDecision,
    BasicChatRequest,
    ContinueRunRequest,
    VercelChatRequest,
)
from tracecat.db.models import AgentSession
from tracecat.exceptions import TracecatNotFoundError

CHILD_TURN_REJECTION = (
    "Subagent sessions are read-only; message the parent session instead"
)


def _service() -> tuple[AgentSessionService, AsyncMock, Role]:
    role = Role(
        type="service",
        service_id="tracecat-api",
        workspace_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
    )
    db = AsyncMock(add=Mock())
    return AgentSessionService(db, role), db, role


@pytest.mark.anyio
async def test_fresh_child_has_only_spawning_parent() -> None:
    service, db, role = _service()
    assert role.workspace_id is not None
    parent_id = uuid.uuid4()
    with patch.object(service, "get_session", AsyncMock(return_value=object())):
        child = await service.create_session(
            AgentSessionCreate(
                title="Child",
                entity_type=AgentSessionEntity.WORKSPACE_CHAT,
                entity_id=role.workspace_id,
                parent_session_id=parent_id,
            )
        )
    assert child.parent_session_id == parent_id
    assert child.forked_from_session_id is None
    assert child.sdk_session_id is None
    db.commit.assert_awaited_once()


@pytest.mark.anyio
async def test_child_rejects_parent_outside_authorized_workspace() -> None:
    service, db, role = _service()
    assert role.workspace_id is not None
    with patch.object(service, "get_session", AsyncMock(return_value=None)):
        with pytest.raises(TracecatNotFoundError):
            await service.create_session(
                AgentSessionCreate(
                    entity_type=AgentSessionEntity.WORKSPACE_CHAT,
                    entity_id=role.workspace_id,
                    parent_session_id=uuid.uuid4(),
                )
            )
    db.add.assert_not_called()
    db.commit.assert_not_awaited()


@pytest.mark.anyio
async def test_fork_read_rejects_source_outside_authorized_workspace() -> None:
    service, db, role = _service()
    fork = AgentSession(
        id=uuid.uuid4(),
        workspace_id=role.workspace_id,
        title="Fork",
        entity_type=AgentSessionEntity.WORKSPACE_CHAT.value,
        entity_id=role.workspace_id,
        backend_id="oss",
        harness_type="claude_code",
        forked_from_session_id=uuid.uuid4(),
    )
    with patch.object(service, "get_session", AsyncMock(side_effect=[fork, None])):
        with pytest.raises(TracecatNotFoundError):
            await service.list_messages(fork.id)
    db.execute.assert_not_awaited()


@pytest.mark.anyio
@pytest.mark.parametrize("missing_reference", ["source", "parent"])
async def test_fork_creation_rejects_reference_outside_authorized_workspace(
    missing_reference: str,
) -> None:
    service, db, role = _service()
    source_id = uuid.uuid4()
    source = AgentSession(
        id=source_id,
        workspace_id=role.workspace_id,
        title="Source",
        entity_type=AgentSessionEntity.WORKSPACE_CHAT.value,
        entity_id=role.workspace_id,
        backend_id="oss",
        harness_type="claude_code",
    )
    references = [None] if missing_reference == "source" else [source, None]
    with patch.object(service, "get_session", AsyncMock(side_effect=references)):
        with pytest.raises(TracecatNotFoundError):
            await service.fork_session(source_id, parent_session_id=uuid.uuid4())
    db.add.assert_not_called()
    db.commit.assert_not_awaited()


@pytest.mark.anyio
async def test_first_fork_turn_uses_captured_sdk_identity_and_history_boundary() -> (
    None
):
    service, db, role = _service()
    source_id = uuid.uuid4()
    fork = AgentSession(
        id=uuid.uuid4(),
        workspace_id=role.workspace_id,
        title="Fork",
        entity_type=AgentSessionEntity.WORKSPACE_CHAT.value,
        entity_id=role.workspace_id,
        forked_from_session_id=source_id,
        forked_from_history_id=7,
        forked_from_sdk_session_id="sdk-at-fork",
    )
    source = AgentSession(
        id=source_id,
        workspace_id=role.workspace_id,
        title="Source",
        entity_type=AgentSessionEntity.WORKSPACE_CHAT.value,
        entity_id=role.workspace_id,
        sdk_session_id="sdk-after-fork",
    )
    entry = SimpleNamespace(
        id=uuid.uuid4(),
        kind="chat-message",
        raw_session_line=None,
        content={"type": "user", "uuid": "prompt", "message": {"content": "Hi"}},
    )
    result = Mock()
    result.scalars.return_value.all.return_value = [entry]
    db.execute.return_value = result
    with patch.object(service, "get_session", AsyncMock(side_effect=[fork, source])):
        history = await service.load_session_history(fork.id)

    assert history is not None
    assert history.is_fork is True
    assert history.sdk_session_id == "sdk-at-fork"
    query = db.execute.await_args.args[0]
    assert "agent_session_history.surrogate_id <=" in str(query)
    assert 7 in query.compile().params.values()


@pytest.mark.anyio
@pytest.mark.parametrize("spawned", [False, True])
async def test_fork_captures_source_and_optional_spawning_parent(
    spawned: bool,
) -> None:
    service, db, role = _service()
    source_id = uuid.uuid4()
    source = AgentSession(
        id=source_id,
        workspace_id=role.workspace_id,
        title="Source",
        entity_type=AgentSessionEntity.WORKSPACE_CHAT.value,
        entity_id=role.workspace_id,
        backend_id="oss",
        harness_type="claude_code",
        sdk_session_id="source-sdk-id",
    )
    parent_id = uuid.uuid4() if spawned else None
    db.scalar.return_value = 42
    with patch.object(service, "get_session", AsyncMock(return_value=source)):
        fork = await service.fork_session(source_id, parent_session_id=parent_id)
    assert fork.parent_session_id == parent_id
    assert fork.forked_from_session_id == source_id
    assert fork.forked_from_history_id == 42
    assert fork.forked_from_sdk_session_id == "source-sdk-id"
    db.commit.assert_awaited_once()


def test_legacy_forks_migrate_to_captured_history_ancestry() -> None:
    path = (
        Path(__file__).resolve().parents[2]
        / "alembic/versions/6d83f2a91c40_split_session_ancestry.py"
    )
    spec = importlib.util.spec_from_file_location("session_ancestry_migration", path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)

    engine = create_engine("sqlite+pysqlite:///:memory:")
    with engine.begin() as conn:
        conn.exec_driver_sql("""
            CREATE TABLE agent_session (
                id TEXT PRIMARY KEY, workspace_id TEXT, created_at TEXT,
                parent_session_id TEXT, forked_from_session_id TEXT,
                forked_from_history_id INTEGER, forked_from_sdk_session_id TEXT,
                sdk_session_id TEXT
            )
        """)
        conn.exec_driver_sql("""
            CREATE TABLE agent_session_history (
                session_id TEXT, surrogate_id INTEGER, created_at TEXT
            )
        """)
        conn.exec_driver_sql("""
            INSERT INTO agent_session VALUES
                ('source', 'workspace', '2026-01-02', NULL, NULL, NULL, NULL, 'sdk'),
                ('legacy-fork', 'workspace', '2026-01-04', 'source', NULL, NULL, NULL, NULL),
                ('other-workspace', 'other', '2026-01-04', 'source', NULL, NULL, NULL, NULL)
        """)
        conn.exec_driver_sql("""
            INSERT INTO agent_session_history VALUES
                ('source', 1, '2026-01-03'),
                ('source', 2, '2026-01-05')
        """)
        conn.execute(text(migration.BACKFILL_SQL))
        conn.exec_driver_sql(
            "UPDATE agent_session SET parent_session_id = NULL "
            "WHERE parent_session_id IS NOT NULL"
        )
        rows = conn.exec_driver_sql("""
            SELECT id, parent_session_id, forked_from_session_id,
                   forked_from_history_id, forked_from_sdk_session_id
            FROM agent_session ORDER BY id
        """).all()
    assert rows == [
        ("legacy-fork", None, "source", 1, "sdk"),
        ("other-workspace", None, None, None, None),
        ("source", None, None, None, None),
    ]


def _session_row(role: Role, **overrides: Any) -> AgentSession:
    now = datetime.now(UTC)
    values: dict[str, Any] = {
        "id": uuid.uuid4(),
        "workspace_id": role.workspace_id,
        "title": "Session",
        "entity_type": AgentSessionEntity.CASE.value,
        "entity_id": uuid.uuid4(),
        "backend_id": "oss",
        "harness_type": "claude_code",
        "artifacts": [],
        "created_at": now,
        "updated_at": now,
    }
    values.update(overrides)
    return AgentSession(**values)


def _user_message() -> VercelChatRequest:
    return VercelChatRequest(
        message=UIMessage(
            id="msg-1", role="user", parts=[{"type": "text", "text": "Hi"}]
        )
    )


@pytest.mark.anyio
@pytest.mark.parametrize(
    "turn_request",
    [
        BasicChatRequest(message="Hi"),
        _user_message(),
        ContinueRunRequest(
            decisions=[ApprovalDecision(tool_call_id="call-1", action="approve")]
        ),
    ],
    ids=["basic", "vercel", "continuation"],
)
async def test_child_sessions_reject_direct_turns(
    turn_request: BasicChatRequest | VercelChatRequest | ContinueRunRequest,
) -> None:
    service, db, role = _service()
    # An owned run would otherwise admit an approval continuation.
    child = _session_row(role, parent_session_id=uuid.uuid4(), curr_run_id=uuid.uuid4())
    with (
        patch.object(service, "get_session", AsyncMock(return_value=child)),
        patch("tracecat.agent.session.service.get_agent_backend") as resolve,
    ):
        with pytest.raises(ValueError, match=CHILD_TURN_REJECTION):
            await service.run_turn(child.id, turn_request)
    resolve.assert_not_called()
    db.add.assert_not_called()
    db.commit.assert_not_awaited()


@pytest.mark.anyio
async def test_child_sessions_reject_caller_owned_turns() -> None:
    service, db, role = _service()
    child = _session_row(role, parent_session_id=uuid.uuid4())
    with patch.object(service, "get_session", AsyncMock(return_value=child)):
        with pytest.raises(ValueError, match=CHILD_TURN_REJECTION):
            await service.prepare_new_turn(child.id, "Hi")
    db.add.assert_not_called()
    db.commit.assert_not_awaited()


@pytest.mark.anyio
async def test_send_message_to_child_session_is_bad_request() -> None:
    service, db, role = _service()
    child = _session_row(role, parent_session_id=uuid.uuid4())

    class _Context:
        async def __aenter__(self) -> AgentSessionService:
            return service

        async def __aexit__(self, *_: object) -> None:
            return None

    with (
        patch(
            "tracecat.agent.session.router.AgentSessionService.with_session",
            return_value=_Context(),
        ),
        patch.object(service, "is_legacy_session", AsyncMock(return_value=False)),
        patch.object(service, "get_session", AsyncMock(return_value=child)),
        patch("tracecat.agent.session.router.AgentStream.new") as open_stream,
        patch("tracecat.agent.session.service.get_agent_backend") as resolve,
    ):
        raw_send_message = cast(Any, send_message).__wrapped__
        with pytest.raises(HTTPException) as exc_info:
            await raw_send_message(
                session_id=child.id,
                request=_user_message(),
                role=role,
                http_request=Mock(),
            )
    assert exc_info.value.status_code == status.HTTP_400_BAD_REQUEST
    assert exc_info.value.detail == CHILD_TURN_REJECTION
    open_stream.assert_not_called()
    resolve.assert_not_called()
    db.commit.assert_not_awaited()


@pytest.mark.anyio
async def test_child_session_messages_remain_readable() -> None:
    _, db, role = _service()
    parent = _session_row(role)
    child = _session_row(role, parent_session_id=parent.id)
    sessions = {parent.id: parent, child.id: child}
    entry = SimpleNamespace(
        id=uuid.uuid4(),
        kind=MessageKind.CHAT_MESSAGE.value,
        content={
            "type": "user",
            "uuid": "task",
            "message": {"role": "user", "content": "Summarize the alerts"},
        },
    )

    def rows(items: list[Any]) -> Mock:
        result = Mock()
        result.scalars.return_value.all.return_value = items
        return result

    # Approvals for the child, then its visible history rows.
    db.execute.side_effect = [rows([]), rows([entry])]
    with (
        patch.object(
            AgentSessionService,
            "get_session",
            AsyncMock(side_effect=lambda session_id: sessions.get(session_id)),
        ),
        patch(
            "tracecat.agent.session.router.require_workspace_chat_entitlement_for_entity",
            AsyncMock(return_value=None),
        ),
    ):
        raw_get_session_vercel = cast(Any, get_session_vercel).__wrapped__
        response = await raw_get_session_vercel(
            session_id=child.id, role=role, session=db
        )

    payload = response.model_dump(mode="json")
    assert payload["parent_session_id"] == str(parent.id)
    assert [message["role"] for message in payload["messages"]] == ["user"]
    assert "Summarize the alerts" in str(payload["messages"])
