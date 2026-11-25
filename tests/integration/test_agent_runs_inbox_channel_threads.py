"""Integration coverage for the channel an inbox session's linked thread is in."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
from pydantic import JsonValue
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.agent.channel_bots import channel_threads
from tracecat.agent.channel_bots.schemas import ChannelBotProvider, InboxChannelThread
from tracecat.agent.common.stream_types import HarnessType
from tracecat.agent.session.types import AgentSessionEntity
from tracecat.auth.types import Role
from tracecat.db.models import (
    AgentChannelBot,
    AgentPreset,
    AgentSession,
    AgentSessionHistory,
    AgentSessionThread,
    User,
)
from tracecat.inbox.providers.agent_runs import AgentRunsInboxProvider
from tracecat.inbox.schemas import InboxItemRead
from tracecat.inbox.types import InboxGroup
from tracecat.tiers.enums import Entitlement

pytestmark = [
    pytest.mark.anyio,
    pytest.mark.integration,
    pytest.mark.usefixtures("db"),
]

BASE_TIME = datetime(2026, 1, 1, tzinfo=UTC)


@pytest.fixture
def entitled(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    """Whether the organization has the channel bots preview; it does by default."""
    is_entitled = AsyncMock(return_value=True)
    monkeypatch.setattr(channel_threads, "is_org_entitled", is_entitled)
    return is_entitled


class FakeChannelBotAdmin:
    def thread_channel_id(
        self, provider: ChannelBotProvider, provider_thread_id: dict[str, JsonValue]
    ) -> str | None:
        channel_id = provider_thread_id.get("channel_id")
        return channel_id if isinstance(channel_id, str) else None


@pytest.fixture
def thread_queries(session: AsyncSession) -> Iterator[list[str]]:
    """The statements that read the thread link table."""
    statements: list[str] = []

    def record(conn, cursor, statement, parameters, context, executemany) -> None:
        if "agent_session_thread" in statement:
            statements.append(statement)

    engine = session.get_bind().engine
    event.listen(engine, "before_cursor_execute", record)
    yield statements
    event.remove(engine, "before_cursor_execute", record)


async def _linked_sessions(
    session: AsyncSession, workspace_id: uuid.UUID
) -> tuple[uuid.UUID, AgentSession, AgentSession, AgentSession]:
    """A bot, a session linked to a thread, an unlinked one, and one whose link
    the provider cannot read."""
    preset = AgentPreset(
        workspace_id=workspace_id,
        name="p",
        slug=f"p-{uuid.uuid4().hex}",
        model_name="m",
        model_provider="p",
    )
    session.add(preset)
    await session.flush()
    bot = AgentChannelBot(
        workspace_id=workspace_id,
        agent_preset_id=preset.id,
        provider="slack",
        manifest_version=1,
        encrypted_credentials=b"",
    )
    session.add(bot)
    rows = [
        AgentSession(
            workspace_id=workspace_id,
            title=title,
            entity_type=AgentSessionEntity.EXTERNAL_CHANNEL.value,
            entity_id=uuid.uuid4(),
            harness_type=HarnessType.CLAUDE_CODE,
            created_at=BASE_TIME,
            updated_at=BASE_TIME,
        )
        for title in ("linked", "unlinked", "unreadable")
    ]
    session.add_all(rows)
    await session.flush()
    linked, unlinked, unreadable = rows
    session.add_all(
        [
            AgentSessionThread(
                session_id=linked.id,
                workspace_id=workspace_id,
                bot_id=bot.id,
                provider_thread_id={
                    "provider": "slack",
                    "team_id": "T1",
                    "channel_id": "C1",
                    "thread_ts": "1.2",
                },
            ),
            AgentSessionThread(
                session_id=unreadable.id,
                workspace_id=workspace_id,
                bot_id=bot.id,
                provider_thread_id={"provider": "slack"},
            ),
        ]
    )
    await session.commit()
    session.expunge_all()
    return bot.id, linked, unlinked, unreadable


def _by_id(items: list[InboxItemRead]) -> dict[uuid.UUID, InboxItemRead]:
    return {item.source_id: item for item in items}


async def test_a_linked_session_names_its_thread_channel_in_one_query(
    session: AsyncSession,
    svc_role: Role,
    monkeypatch: pytest.MonkeyPatch,
    thread_queries: list[str],
    entitled: AsyncMock,
) -> None:
    assert svc_role.workspace_id is not None
    bot_id, linked, unlinked, unreadable = await _linked_sessions(
        session, svc_role.workspace_id
    )
    monkeypatch.setattr(
        channel_threads, "get_channel_bot_admin", lambda: FakeChannelBotAdmin()
    )
    provider = AgentRunsInboxProvider(session, svc_role)

    for group in (None, InboxGroup.COMPLETED):
        thread_queries.clear()
        items = _by_id((await provider.list_items(limit=20, group=group)).items)

        assert items[linked.id].channel_thread == InboxChannelThread(
            bot_id=bot_id,
            provider=ChannelBotProvider.SLACK,
            provider_channel_id="C1",
        )
        assert items[unlinked.id].channel_thread is None
        assert items[unreadable.id].channel_thread is None
        assert len(thread_queries) == 1


async def test_without_a_channel_bot_admin_no_session_names_a_channel(
    session: AsyncSession,
    svc_role: Role,
    monkeypatch: pytest.MonkeyPatch,
    thread_queries: list[str],
    entitled: AsyncMock,
) -> None:
    assert svc_role.workspace_id is not None
    _, linked, _, _ = await _linked_sessions(session, svc_role.workspace_id)
    monkeypatch.setattr(channel_threads, "get_channel_bot_admin", lambda: None)
    thread_queries.clear()

    items = _by_id(
        (await AgentRunsInboxProvider(session, svc_role).list_items(limit=20)).items
    )

    assert items[linked.id].channel_thread is None
    assert thread_queries == []


async def test_without_the_preview_entitlement_no_session_names_a_channel(
    session: AsyncSession,
    svc_role: Role,
    monkeypatch: pytest.MonkeyPatch,
    thread_queries: list[str],
    entitled: AsyncMock,
) -> None:
    assert svc_role.workspace_id is not None
    _, linked, _, _ = await _linked_sessions(session, svc_role.workspace_id)
    monkeypatch.setattr(
        channel_threads, "get_channel_bot_admin", lambda: FakeChannelBotAdmin()
    )
    entitled.return_value = False
    thread_queries.clear()

    items = _by_id(
        (await AgentRunsInboxProvider(session, svc_role).list_items(limit=20)).items
    )

    assert items[linked.id].channel_thread is None
    assert thread_queries == []
    entitled.assert_awaited_with(
        session, svc_role.organization_id, Entitlement.AGENT_CHANNELS_PREVIEW
    )


async def test_without_agent_read_no_session_names_a_channel(
    session: AsyncSession,
    svc_role: Role,
    monkeypatch: pytest.MonkeyPatch,
    thread_queries: list[str],
    entitled: AsyncMock,
) -> None:
    assert svc_role.workspace_id is not None
    _, linked, _, _ = await _linked_sessions(session, svc_role.workspace_id)
    monkeypatch.setattr(
        channel_threads, "get_channel_bot_admin", lambda: FakeChannelBotAdmin()
    )
    inbox_only = svc_role.model_copy(update={"scopes": frozenset({"inbox:read"})})
    thread_queries.clear()

    items = _by_id(
        (await AgentRunsInboxProvider(session, inbox_only).list_items(limit=20)).items
    )

    assert items[linked.id].channel_thread is None
    assert thread_queries == []


async def _user(session: AsyncSession) -> User:
    user = User(
        id=uuid.uuid4(),
        email=f"user-{uuid.uuid4().hex[:8]}@example.com",
        hashed_password="unused",
        is_active=True,
        is_verified=True,
        is_superuser=False,
        last_login_at=None,
    )
    session.add(user)
    await session.flush()
    return user


async def test_created_by_falls_back_to_the_sessions_starter(
    session: AsyncSession, svc_role: Role
) -> None:
    assert svc_role.workspace_id is not None
    workspace_id = svc_role.workspace_id
    starter, replier, creator = [await _user(session) for _ in range(3)]
    _, started, _, unstarted = await _linked_sessions(session, workspace_id)
    created = AgentSession(
        workspace_id=workspace_id,
        title="created",
        entity_type=AgentSessionEntity.EXTERNAL_CHANNEL.value,
        entity_id=uuid.uuid4(),
        harness_type=HarnessType.CLAUDE_CODE,
        created_by=creator.id,
        created_at=BASE_TIME,
        updated_at=BASE_TIME,
    )
    session.add(created)
    await session.flush()
    # Earliest by insertion: an unauthored row, then the starter, then a reply.
    for session_id, author_id in (
        (started.id, None),
        (started.id, starter.id),
        (started.id, replier.id),
        (created.id, starter.id),
    ):
        session.add(
            AgentSessionHistory(
                workspace_id=workspace_id,
                session_id=session_id,
                content={"type": "user"},
                kind="chat-message",
                author_id=author_id,
            )
        )
        await session.flush()
    await session.commit()

    items = _by_id(
        (await AgentRunsInboxProvider(session, svc_role).list_items(limit=20)).items
    )

    assert (created_by := items[started.id].created_by) is not None
    assert created_by.id == starter.id
    assert (created_by := items[created.id].created_by) is not None
    assert created_by.id == creator.id
    assert items[unstarted.id].created_by is None
    # Display only: the session stays ownerless.
    assert (
        await session.scalar(
            select(AgentSession.created_by).where(AgentSession.id == started.id)
        )
        is None
    )
