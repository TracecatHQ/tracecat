"""A session's channel sync: the flag on its thread link, and the notifier hooks."""

from __future__ import annotations

import asyncio
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import get_args
from unittest.mock import AsyncMock, Mock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.agent import channel_notifier
from tracecat.agent.backends.default import DefaultBackend
from tracecat.agent.backends.types import SessionWorkflowContext
from tracecat.agent.channel_bots import channel_threads
from tracecat.agent.channel_bots.messages import ChannelMessage, ChannelMessageSender
from tracecat.agent.channel_bots.schemas import ChannelBotProvider, InboxChannelThread
from tracecat.agent.channel_notifier import (
    ChannelDestination,
    ChannelDestinationNotRoutable,
    ChannelDestinationRef,
    ChannelRecipientNotLinked,
    ChannelsNotEntitled,
    MessageSent,
    NoChannelBot,
    RunUpdate,
    TurnFinished,
    TurnStarted,
    can_open_channel_thread,
    check_channel_reply_thread,
    list_channel_destinations,
    notify_sync_stopped,
    open_channel_reply_thread,
    open_channel_thread,
    turn_started_updates,
)
from tracecat.agent.common.stream_types import HarnessType
from tracecat.agent.session import activities as session_activities
from tracecat.agent.session import channel_sync
from tracecat.agent.session import router as session_router
from tracecat.agent.session.channel_sync import (
    ChannelThreadUnavailable,
    check_turn_destination,
    get_channel_sync,
    has_channel_bot,
    push_late_turn_finished,
    set_channel_sync,
)
from tracecat.agent.session.router import router
from tracecat.agent.session.schemas import AgentSessionChannelSyncRead
from tracecat.agent.session.service import AgentSessionService
from tracecat.agent.session.types import AgentSessionEntity
from tracecat.agent.types import AgentConfig
from tracecat.auth.dependencies import WorkspaceActorRouteRole
from tracecat.auth.types import Role
from tracecat.chat.enums import MessageKind
from tracecat.chat.schemas import VercelChatRequest
from tracecat.db.engine import get_async_session
from tracecat.db.models import (
    AgentChannelBot,
    AgentPreset,
    AgentSession,
    AgentSessionHistory,
    AgentSessionThread,
    User,
)

SESSION_ID = uuid.UUID("00000000-0000-4000-8000-00000000005e")
RUN_ID = uuid.UUID("00000000-0000-4000-8000-0000000000a1")
USER_ID = uuid.UUID("00000000-0000-4000-8000-0000000000c1")
THREAD = {"provider": "slack", "team_id": "T1", "channel_id": "C1", "thread_ts": "1.2"}
TEST_CHANNEL = ChannelDestination(kind="channel", id="C1", name="test", recent=True)


def _user_role(workspace_id: uuid.UUID | None = None) -> Role:
    return Role(
        type="user",
        service_id="tracecat-api",
        user_id=USER_ID,
        workspace_id=workspace_id or uuid.uuid4(),
        organization_id=uuid.uuid4(),
        scopes=frozenset({"agent:read", "agent:execute"}),
    )


CHANNEL_ROLE = Role(
    type="service", service_id="tracecat-api", workspace_id=uuid.uuid4()
)


class FakeNotifier:
    """Records the sync hooks; `open_thread` links a thread through `link`."""

    def __init__(self, *, opens: bool = True, link=None) -> None:
        self.opens = opens
        self.link = link
        self.opened: list[uuid.UUID] = []
        self.destinations: list[ChannelDestinationRef | None] = []
        self.stopped: list[tuple[uuid.UUID | None, uuid.UUID]] = []

    async def is_linked(self, role: Role, session_id: uuid.UUID) -> bool:
        del role, session_id
        return True

    async def push(self, role: Role, update: RunUpdate) -> str | None:
        del role, update
        return None

    async def can_open_thread(self, role: Role, session_id: uuid.UUID) -> bool:
        del role, session_id
        return self.opens

    async def list_destinations(
        self, role: Role, session_id: uuid.UUID
    ) -> list[ChannelDestination]:
        del role, session_id
        return [TEST_CHANNEL] if self.opens else []

    async def open_thread(
        self,
        role: Role,
        session_id: uuid.UUID,
        destination: ChannelDestinationRef | None = None,
    ) -> bool:
        del role
        if not self.opens:
            return False
        self.opened.append(session_id)
        self.destinations.append(destination)
        if self.link is not None:
            await self.link(session_id)
        return True

    async def sync_stopped(self, role: Role, session_id: uuid.UUID) -> None:
        self.stopped.append((role.user_id, session_id))


class BrokenNotifier(FakeNotifier):
    async def can_open_thread(self, role: Role, session_id: uuid.UUID) -> bool:
        raise RuntimeError("provider down")

    async def list_destinations(
        self, role: Role, session_id: uuid.UUID
    ) -> list[ChannelDestination]:
        raise RuntimeError("provider down")

    async def open_thread(
        self,
        role: Role,
        session_id: uuid.UUID,
        destination: ChannelDestinationRef | None = None,
    ) -> bool:
        raise RuntimeError("provider down")

    async def sync_stopped(self, role: Role, session_id: uuid.UUID) -> None:
        raise RuntimeError("provider down")


def _install(monkeypatch: pytest.MonkeyPatch, notifier: FakeNotifier | None) -> None:
    monkeypatch.setattr(channel_notifier, "get_channel_notifier", lambda: notifier)
    monkeypatch.setattr(channel_sync, "get_channel_notifier", lambda: notifier)


# What a turn's start pushes.


def test_a_tracecat_users_message_is_relayed_before_the_status() -> None:
    updates = turn_started_updates(_user_role(), SESSION_ID, RUN_ID, "Check the alert")

    assert updates == [
        MessageSent(
            session_id=SESSION_ID,
            run_id=RUN_ID,
            user_id=USER_ID,
            text="Check the alert",
        ),
        TurnStarted(session_id=SESSION_ID, run_id=RUN_ID),
    ]


def test_a_retried_message_is_relayed_under_the_run_that_first_sent_it() -> None:
    first_run = uuid.uuid4()

    updates = turn_started_updates(
        _user_role(), SESSION_ID, RUN_ID, "Check the alert", prompt_run_id=first_run
    )

    assert updates == [
        MessageSent(
            session_id=SESSION_ID,
            run_id=first_run,
            user_id=USER_ID,
            text="Check the alert",
        ),
        TurnStarted(session_id=SESSION_ID, run_id=RUN_ID),
    ]


@pytest.mark.parametrize(
    ("role", "prompt"),
    [
        pytest.param(CHANNEL_ROLE, "From the channel", id="channel-message"),
        pytest.param(
            _user_role().model_copy(update={"type": "service"}),
            "From a workflow",
            id="service-role-with-user",
        ),
        pytest.param(_user_role(), None, id="resumed-run"),
        pytest.param(_user_role(), "  ", id="blank-prompt"),
    ],
)
def test_only_the_status_is_pushed_otherwise(role: Role, prompt: str | None) -> None:
    updates = turn_started_updates(role, SESSION_ID, RUN_ID, prompt)

    assert updates == [TurnStarted(session_id=SESSION_ID, run_id=RUN_ID)]


# The notifier hooks never fail the caller.


@pytest.mark.anyio
async def test_hooks_fail_closed_without_a_notifier(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install(monkeypatch, None)
    role = _user_role()

    assert await can_open_channel_thread(role, SESSION_ID) is False
    assert await list_channel_destinations(role, SESSION_ID) == []
    assert await open_channel_thread(role, SESSION_ID) is False
    await notify_sync_stopped(role, SESSION_ID)


@pytest.mark.anyio
async def test_hooks_fail_closed_on_a_broken_notifier(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install(monkeypatch, BrokenNotifier())
    role = _user_role()

    assert await can_open_channel_thread(role, SESSION_ID) is False
    assert await list_channel_destinations(role, SESSION_ID) == []
    assert await open_channel_thread(role, SESSION_ID) is False
    await notify_sync_stopped(role, SESSION_ID)


# The flag, on the database.


async def _bot(session: AsyncSession, workspace_id: uuid.UUID) -> AgentChannelBot:
    preset = AgentPreset(
        workspace_id=workspace_id,
        name="Triage",
        slug=f"triage-{uuid.uuid4().hex[:8]}",
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
    await session.flush()
    return bot


async def _session(
    session: AsyncSession, role: Role, *, entity_type: AgentSessionEntity
) -> AgentSession:
    assert role.workspace_id is not None
    row = AgentSession(
        workspace_id=role.workspace_id,
        title="t",
        entity_type=entity_type.value,
        entity_id=uuid.uuid4(),
        harness_type=HarnessType.CLAUDE_CODE,
    )
    session.add(row)
    await session.flush()
    return row


async def _link(
    session: AsyncSession, bot: AgentChannelBot, session_id: uuid.UUID, *, synced: bool
) -> None:
    session.add(
        AgentSessionThread(
            session_id=session_id,
            workspace_id=bot.workspace_id,
            bot_id=bot.id,
            provider_thread_id=THREAD,
            sync_enabled=synced,
        )
    )
    await session.commit()


async def _flag(session: AsyncSession, session_id: uuid.UUID) -> bool | None:
    return await session.scalar(
        select(AgentSessionThread.sync_enabled).where(
            AgentSessionThread.session_id == session_id
        )
    )


@pytest.mark.anyio
@pytest.mark.usefixtures("redis_server")
async def test_every_retry_of_a_web_message_names_the_run_that_first_sent_it(
    session: AsyncSession, svc_role: Role
) -> None:
    web = await _session(session, svc_role, entity_type=AgentSessionEntity.AGENT_PRESET)
    sender, teammate = _person("sender"), _person("teammate")
    session.add_all([sender, teammate])
    await session.flush()
    svc = AgentSessionService(
        session, svc_role.model_copy(update={"type": "user", "user_id": sender.id})
    )
    client_id = f"msg-{uuid.uuid4().hex[:12]}"
    first_run, same_tab_retry, reload_retry, second_reload_retry = (
        uuid.uuid4() for _ in range(4)
    )

    # A new message names no earlier run; Retry before a reload resends its client id.
    assert await svc._prompt_origin_run_id(web.id, client_id, first_run) is None
    assert (
        await svc._prompt_origin_run_id(web.id, client_id, same_tab_retry) == first_run
    )

    # After a reload, Retry names the prompt row the retried run wrote.
    retried_row = AgentSessionHistory(
        session_id=web.id,
        workspace_id=web.workspace_id,
        kind=MessageKind.CHAT_MESSAGE.value,
        curr_run_id=same_tab_retry,
        author_id=sender.id,
        content={"type": "user", "message": {"role": "user", "content": "hi"}},
    )
    session.add(retried_row)
    await session.flush()
    assert (
        await svc._prompt_origin_run_id(web.id, str(retried_row.id), reload_retry)
        == first_run
    )

    # A row with no recorded origin is its own run's prompt.
    unrecorded_run = uuid.uuid4()
    unrecorded_row = AgentSessionHistory(
        session_id=web.id,
        workspace_id=web.workspace_id,
        kind=MessageKind.CHAT_MESSAGE.value,
        curr_run_id=unrecorded_run,
        author_id=sender.id,
        content={"type": "user", "message": {"role": "user", "content": "hi"}},
    )
    session.add(unrecorded_row)
    await session.flush()
    assert (
        await svc._prompt_origin_run_id(
            web.id, str(unrecorded_row.id), second_reload_retry
        )
        == unrecorded_run
    )

    # A teammate's prompt row is not the sender's message: it names no earlier run.
    teammate_row = AgentSessionHistory(
        session_id=web.id,
        workspace_id=web.workspace_id,
        kind=MessageKind.CHAT_MESSAGE.value,
        curr_run_id=first_run,
        author_id=teammate.id,
        content={"type": "user", "message": {"role": "user", "content": "hi"}},
    )
    session.add(teammate_row)
    await session.flush()
    assert (
        await svc._prompt_origin_run_id(web.id, str(teammate_row.id), uuid.uuid4())
        is None
    )

    # A service role carrying the sender's id authors nothing, as history stamps it.
    as_service = AgentSessionService(
        session, svc_role.model_copy(update={"type": "service", "user_id": sender.id})
    )
    assert (
        await as_service._prompt_origin_run_id(
            web.id, str(retried_row.id), uuid.uuid4()
        )
        is None
    )


def _person(name: str) -> User:
    return User(
        id=uuid.uuid4(),
        email=f"{name}-{uuid.uuid4()}@example.com",
        hashed_password="unused",
        is_active=True,
        is_verified=True,
        is_superuser=False,
        last_login_at=None,
    )


def test_a_retried_channel_envelope_is_not_relayed_again() -> None:
    message = ChannelMessage(
        provider=ChannelBotProvider.SLACK,
        sender=ChannelMessageSender(provider_user_id="U1", name="Sam"),
        text="look at this",
    )

    updates = turn_started_updates(_user_role(), SESSION_ID, RUN_ID, message.render())

    assert updates == [TurnStarted(session_id=SESSION_ID, run_id=RUN_ID)]


def test_the_retried_run_reaches_the_turn_workflow() -> None:
    first_run = uuid.uuid4()
    context = SessionWorkflowContext(
        session_id=SESSION_ID,
        backend_id="default",
        harness_type=HarnessType.CLAUDE_CODE.value,
        title="t",
        entity_type=AgentSessionEntity.AGENT_PRESET,
        entity_id=uuid.uuid4(),
        tools=None,
        agent_preset_id=None,
        agent_preset_version_id=None,
        role=_user_role(),
        config=AgentConfig(model_name="m", model_provider="p"),
        prompt="hi",
        run_id=RUN_ID,
        stream_id=uuid.uuid4(),
        prompt_run_id=first_run,
    )

    args = asyncio.run(DefaultBackend().build_workflow_args(context))

    assert args.prompt_run_id == first_run


@pytest.mark.anyio
async def test_a_web_session_without_a_thread_asks_whether_one_can_open(
    session: AsyncSession, svc_role: Role, monkeypatch: pytest.MonkeyPatch
) -> None:
    web = await _session(session, svc_role, entity_type=AgentSessionEntity.AGENT_PRESET)
    await session.commit()
    svc = AgentSessionService(session, svc_role)

    _install(monkeypatch, FakeNotifier(opens=True))
    assert await get_channel_sync(svc, web.id) == AgentSessionChannelSyncRead(
        available=True, enabled=False
    )
    _install(monkeypatch, FakeNotifier(opens=False))
    assert await get_channel_sync(svc, web.id) == AgentSessionChannelSyncRead(
        available=False, enabled=False
    )


@pytest.mark.anyio
async def test_opting_a_web_session_in_opens_and_links_a_thread(
    session: AsyncSession, svc_role: Role, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert svc_role.workspace_id is not None
    bot = await _bot(session, svc_role.workspace_id)
    web = await _session(session, svc_role, entity_type=AgentSessionEntity.AGENT_PRESET)
    await session.commit()

    async def link(session_id: uuid.UUID) -> None:
        await _link(session, bot, session_id, synced=True)

    notifier = FakeNotifier(link=link)
    _install(monkeypatch, notifier)
    svc = AgentSessionService(session, svc_role)

    picked = ChannelDestinationRef(kind="channel", id="C1")
    result = await set_channel_sync(svc, web.id, enabled=True, destination=picked)

    assert result == AgentSessionChannelSyncRead(
        available=True, enabled=True, linked=True
    )
    assert notifier.opened == [web.id]
    assert notifier.destinations == [picked]
    assert await _flag(session, web.id) is True
    assert await svc.channel_linked_session_ids([web.id, uuid.uuid4()]) == {web.id}


@pytest.mark.anyio
async def test_opting_in_fails_when_no_thread_can_open(
    session: AsyncSession, svc_role: Role, monkeypatch: pytest.MonkeyPatch
) -> None:
    web = await _session(session, svc_role, entity_type=AgentSessionEntity.AGENT_PRESET)
    await session.commit()
    _install(monkeypatch, FakeNotifier(opens=False))
    svc = AgentSessionService(session, svc_role)

    with pytest.raises(ChannelThreadUnavailable):
        await set_channel_sync(svc, web.id, enabled=True)
    assert await _flag(session, web.id) is None


@pytest.mark.anyio
async def test_turning_sync_off_tells_the_thread_once(
    session: AsyncSession, svc_role: Role, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert svc_role.workspace_id is not None
    bot = await _bot(session, svc_role.workspace_id)
    started = await _session(
        session, svc_role, entity_type=AgentSessionEntity.EXTERNAL_CHANNEL
    )
    await _link(session, bot, started.id, synced=True)
    notifier = FakeNotifier()
    _install(monkeypatch, notifier)
    svc = AgentSessionService(session, svc_role)

    first = await set_channel_sync(svc, started.id, enabled=False)
    again = await set_channel_sync(svc, started.id, enabled=False)

    assert (
        first
        == again
        == AgentSessionChannelSyncRead(available=True, enabled=False, linked=True)
    )
    assert notifier.stopped == [(svc_role.user_id, started.id)]
    assert await _flag(session, started.id) is False


@pytest.mark.anyio
async def test_turning_sync_back_on_reuses_the_thread(
    session: AsyncSession, svc_role: Role, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert svc_role.workspace_id is not None
    bot = await _bot(session, svc_role.workspace_id)
    started = await _session(
        session, svc_role, entity_type=AgentSessionEntity.EXTERNAL_CHANNEL
    )
    await _link(session, bot, started.id, synced=False)
    notifier = FakeNotifier()
    _install(monkeypatch, notifier)
    svc = AgentSessionService(session, svc_role)

    result = await set_channel_sync(svc, started.id, enabled=True)

    assert result == AgentSessionChannelSyncRead(
        available=True, enabled=True, linked=True
    )
    assert notifier.opened == [] and notifier.stopped == []
    assert await _flag(session, started.id) is True


@pytest.mark.anyio
async def test_a_linked_session_names_its_thread_channel(
    session: AsyncSession, svc_role: Role, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert svc_role.workspace_id is not None
    bot = await _bot(session, svc_role.workspace_id)
    bot.provider_bot_user_id = "UBOT"
    web = await _session(session, svc_role, entity_type=AgentSessionEntity.AGENT_PRESET)
    await _link(session, bot, web.id, synced=True)
    admin = Mock(thread_channel_id=Mock(return_value="C1"))
    monkeypatch.setattr(channel_threads, "get_channel_bot_admin", lambda: admin)
    monkeypatch.setattr(
        channel_threads, "is_org_entitled", AsyncMock(return_value=True)
    )
    svc = AgentSessionService(session, svc_role)

    result = await get_channel_sync(svc, web.id)

    assert result.thread == InboxChannelThread(
        bot_id=bot.id,
        provider=ChannelBotProvider.SLACK,
        provider_channel_id="C1",
        provider_bot_user_id="UBOT",
    )
    admin.thread_channel_id.assert_called_once_with(ChannelBotProvider.SLACK, THREAD)


# Who may toggle: whoever may talk to the agent.


def _agent_session(
    workspace_id: uuid.UUID, *, created_by: uuid.UUID | None, entity_type: str
) -> AgentSession:
    return AgentSession(
        id=uuid.uuid4(),
        workspace_id=workspace_id,
        created_by=created_by,
        title="t",
        entity_type=entity_type,
        entity_id=uuid.uuid4(),
        harness_type="claude_code",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )


def _client(
    role: Role, agent_session: AgentSession, authored: list[uuid.UUID] | None = None
):
    app = FastAPI()
    app.include_router(router)
    role_dependency = get_args(WorkspaceActorRouteRole)[1].dependency
    app.dependency_overrides[role_dependency] = lambda: role
    # The access rule's participation read finds ``authored`` sessions.
    db = AsyncMock()
    db.scalars.return_value = Mock(all=Mock(return_value=authored or []))
    app.dependency_overrides[get_async_session] = lambda: db
    return app, patch.object(
        AgentSessionService, "get_session", AsyncMock(return_value=agent_session)
    )


@pytest.mark.parametrize(
    ("created_by", "entity_type", "participated", "allowed"),
    [
        pytest.param(USER_ID, "agent_preset", False, True, id="own-session"),
        pytest.param(None, "external_channel", False, True, id="channel-started"),
        pytest.param(uuid.uuid4(), "agent_preset", False, False, id="teammate-session"),
        pytest.param(uuid.uuid4(), "agent_preset", True, True, id="teammate-who-sent"),
    ],
)
def test_toggling_needs_the_right_to_talk_to_the_agent(
    created_by: uuid.UUID | None, entity_type: str, participated: bool, allowed: bool
) -> None:
    role = _user_role()
    assert role.workspace_id is not None
    agent_session = _agent_session(
        role.workspace_id, created_by=created_by, entity_type=entity_type
    )
    app, get_session = _client(
        role, agent_session, [agent_session.id] if participated else None
    )
    toggled = AgentSessionChannelSyncRead(available=True, enabled=False)
    with (
        get_session,
        patch.object(
            session_router,
            "_require_workspace_chat_entitlement_for_session_tree",
            AsyncMock(),
        ),
        patch.object(
            session_router, "set_channel_sync", AsyncMock(return_value=toggled)
        ) as set_sync,
        TestClient(app) as client,
    ):
        response = client.put(
            f"/agent/sessions/{agent_session.id}/channel-sync",
            json={"enabled": False},
        )

    assert response.status_code == (200 if allowed else 403)
    assert set_sync.await_count == (1 if allowed else 0)


def test_reading_a_session_the_actor_cannot_toggle_hides_the_toggle() -> None:
    role = _user_role()
    assert role.workspace_id is not None
    agent_session = _agent_session(
        role.workspace_id, created_by=uuid.uuid4(), entity_type="agent_preset"
    )
    app, get_session = _client(role, agent_session)
    with (
        get_session,
        patch.object(
            session_router, "get_channel_sync_enabled", AsyncMock(return_value=True)
        ),
        TestClient(app) as client,
    ):
        response = client.get(f"/agent/sessions/{agent_session.id}/channel-sync")

    assert response.status_code == 200
    assert response.json() == {
        "available": False,
        "enabled": True,
        "linked": True,
        "thread": None,
    }


def test_opting_in_without_a_channel_is_a_conflict() -> None:
    role = _user_role()
    assert role.workspace_id is not None
    agent_session = _agent_session(
        role.workspace_id, created_by=USER_ID, entity_type="agent_preset"
    )
    app, get_session = _client(role, agent_session)
    with (
        get_session,
        patch.object(
            session_router,
            "_require_workspace_chat_entitlement_for_session_tree",
            AsyncMock(),
        ),
        patch.object(
            session_router,
            "set_channel_sync",
            AsyncMock(side_effect=ChannelThreadUnavailable("none")),
        ),
        TestClient(app) as client,
    ):
        response = client.put(
            f"/agent/sessions/{agent_session.id}/channel-sync",
            json={"enabled": True},
        )

    assert response.status_code == 409


@pytest.mark.parametrize(
    ("created_by", "linked", "status_code", "items"),
    [
        pytest.param(USER_ID, False, 200, [TEST_CHANNEL], id="own-unlinked"),
        pytest.param(USER_ID, True, 200, [], id="own-linked"),
        pytest.param(uuid.uuid4(), False, 403, None, id="teammate-session"),
    ],
)
def test_listing_destinations_follows_the_toggle_rule(
    monkeypatch: pytest.MonkeyPatch,
    created_by: uuid.UUID,
    linked: bool,
    status_code: int,
    items: list[ChannelDestination] | None,
) -> None:
    _install(monkeypatch, FakeNotifier())
    role = _user_role()
    assert role.workspace_id is not None
    agent_session = _agent_session(
        role.workspace_id, created_by=created_by, entity_type="agent_preset"
    )
    app, get_session = _client(role, agent_session)
    with (
        get_session,
        patch.object(
            session_router,
            "get_channel_sync_enabled",
            AsyncMock(return_value=True if linked else None),
        ),
        TestClient(app) as client,
    ):
        response = client.get(
            f"/agent/sessions/{agent_session.id}/channel-sync/destinations"
        )

    assert response.status_code == status_code
    if items is not None:
        assert response.json() == {
            "items": [item.model_dump(mode="json") for item in items]
        }


def test_opting_in_passes_the_picked_destination() -> None:
    role = _user_role()
    assert role.workspace_id is not None
    agent_session = _agent_session(
        role.workspace_id, created_by=USER_ID, entity_type="agent_preset"
    )
    app, get_session = _client(role, agent_session)
    toggled = AgentSessionChannelSyncRead(available=True, enabled=True, linked=True)
    with (
        get_session,
        patch.object(
            session_router,
            "_require_workspace_chat_entitlement_for_session_tree",
            AsyncMock(),
        ),
        patch.object(
            session_router, "set_channel_sync", AsyncMock(return_value=toggled)
        ) as set_sync,
        TestClient(app) as client,
    ):
        response = client.put(
            f"/agent/sessions/{agent_session.id}/channel-sync",
            json={"enabled": True, "destination": {"kind": "direct_message"}},
        )

    assert response.status_code == 200
    assert set_sync.await_args is not None
    assert set_sync.await_args.kwargs["destination"] == ChannelDestinationRef(
        kind="direct_message"
    )


# A workflow's preset action posting its reply: every refusal is raised, typed.


class UnroutableNotifier(FakeNotifier):
    async def check_reply_thread(
        self, role: Role, preset_id: uuid.UUID, destination: ChannelDestinationRef
    ) -> None:
        raise ChannelDestinationNotRoutable(f"Not in {destination.id}")

    async def open_reply_thread(
        self, role: Role, session_id: uuid.UUID, destination: ChannelDestinationRef
    ) -> None:
        raise ChannelDestinationNotRoutable(f"Not in {destination.id}")


def _entitled(monkeypatch: pytest.MonkeyPatch, entitled: bool) -> None:
    async def channels_entitled(role: Role) -> bool:
        del role
        return entitled

    monkeypatch.setattr(channel_notifier, "_channels_entitled", channels_entitled)


REPLY_TO = ChannelDestinationRef.from_input("C1")


def test_an_email_names_a_dm_and_anything_else_a_channel() -> None:
    assert ChannelDestinationRef.from_input(" alice@example.com ") == (
        ChannelDestinationRef(kind="direct_message", id="alice@example.com")
    )
    assert REPLY_TO == ChannelDestinationRef(kind="channel", id="C1")


@pytest.mark.anyio
async def test_a_reply_thread_needs_the_channels_entitlement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install(monkeypatch, FakeNotifier())
    _entitled(monkeypatch, False)

    with pytest.raises(ChannelsNotEntitled):
        await check_channel_reply_thread(CHANNEL_ROLE, uuid.uuid4(), REPLY_TO)
    with pytest.raises(ChannelsNotEntitled):
        await open_channel_reply_thread(CHANNEL_ROLE, SESSION_ID, REPLY_TO)


@pytest.mark.anyio
async def test_a_reply_thread_needs_an_installed_bot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install(monkeypatch, None)
    _entitled(monkeypatch, True)

    with pytest.raises(NoChannelBot):
        await check_channel_reply_thread(CHANNEL_ROLE, uuid.uuid4(), REPLY_TO)


@pytest.mark.anyio
async def test_an_unroutable_reply_thread_is_raised_not_swallowed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install(monkeypatch, UnroutableNotifier())
    _entitled(monkeypatch, True)

    with pytest.raises(ChannelDestinationNotRoutable, match="Not in C1"):
        await check_channel_reply_thread(CHANNEL_ROLE, uuid.uuid4(), REPLY_TO)
    with pytest.raises(ChannelDestinationNotRoutable, match="Not in C1"):
        await open_channel_reply_thread(CHANNEL_ROLE, SESSION_ID, REPLY_TO)


# A turn that carries a destination, or started before sync was turned on.


class LateLinkNotifier(FakeNotifier):
    """Reads the link from the database; `open_reply_thread` links, or raises ``error``."""

    def __init__(
        self,
        session: AsyncSession,
        bot: AgentChannelBot,
        error: Exception | None = None,
    ) -> None:
        super().__init__()
        self.session = session
        self.bot = bot
        self.error = error
        self.pushed: list[RunUpdate] = []

    async def is_linked(self, role: Role, session_id: uuid.UUID) -> bool:
        return await _flag(self.session, session_id) is not None

    async def push(self, role: Role, update: RunUpdate) -> str | None:
        self.pushed.append(update)
        return None

    async def open_reply_thread(
        self, role: Role, session_id: uuid.UUID, destination: ChannelDestinationRef
    ) -> None:
        self.destinations.append(destination)
        if self.error is not None:
            raise self.error
        await _link(self.session, self.bot, session_id, synced=True)
        self.opened.append(session_id)


def _use_test_session(monkeypatch: pytest.MonkeyPatch, session: AsyncSession) -> None:
    """The late-link helpers open their own session; give them the test's."""

    @asynccontextmanager
    async def with_session(role: Role):
        yield AgentSessionService(session, role)

    monkeypatch.setattr(
        channel_sync.AgentSessionService, "with_session", staticmethod(with_session)
    )


PICKED = ChannelDestinationRef(kind="channel", id="C1")


@pytest.mark.anyio
async def test_a_destination_on_the_first_send_opens_the_thread_at_turn_end(
    session: AsyncSession, svc_role: Role, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert svc_role.workspace_id is not None
    bot = await _bot(session, svc_role.workspace_id)
    web = await _session(session, svc_role, entity_type=AgentSessionEntity.AGENT_PRESET)
    await session.commit()
    notifier = LateLinkNotifier(session, bot)
    _install(monkeypatch, notifier)
    _entitled(monkeypatch, True)

    await push_late_turn_finished(svc_role, web.id, RUN_ID, PICKED)

    # The notifier roots the thread at the message; the turn's reply posts under it.
    assert notifier.destinations == [PICKED]
    assert notifier.opened == [web.id]
    assert notifier.pushed == [TurnFinished(session_id=web.id, run_id=RUN_ID)]
    assert await _flag(session, web.id) is True


@pytest.mark.anyio
async def test_an_opened_thread_gets_the_reply_without_a_link_check(
    session: AsyncSession, svc_role: Role, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert svc_role.workspace_id is not None
    bot = await _bot(session, svc_role.workspace_id)
    web = await _session(session, svc_role, entity_type=AgentSessionEntity.AGENT_PRESET)
    await session.commit()
    notifier = LateLinkNotifier(session, bot)
    _install(monkeypatch, notifier)
    _entitled(monkeypatch, True)
    # The link check fails closed; a thread just opened must not depend on it.
    monkeypatch.setattr(
        channel_sync, "is_channel_linked", AsyncMock(return_value=False)
    )

    await push_late_turn_finished(svc_role, web.id, RUN_ID, PICKED)

    assert notifier.pushed == [TurnFinished(session_id=web.id, run_id=RUN_ID)]


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("error", "reason"),
    [
        pytest.param(NoChannelBot("bot paused"), "bot paused", id="bot-gone"),
        pytest.param(
            ChannelRecipientNotLinked("You hasn't linked Slack"),
            "You hasn't linked Slack",
            id="dm-unlinked",
        ),
        pytest.param(ChannelDestinationNotRoutable("gone"), "gone", id="not-routable"),
        pytest.param(
            TimeoutError(),
            "Slack didn't respond. Turn sync on to try again.",
            id="transient",
        ),
    ],
)
async def test_a_turns_thread_that_cannot_open_is_told_to_the_session(
    session: AsyncSession,
    svc_role: Role,
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
    reason: str,
) -> None:
    assert svc_role.workspace_id is not None
    bot = await _bot(session, svc_role.workspace_id)
    web = await _session(session, svc_role, entity_type=AgentSessionEntity.AGENT_PRESET)
    await session.commit()
    notifier = LateLinkNotifier(session, bot, error)
    _install(monkeypatch, notifier)
    _entitled(monkeypatch, True)
    told = AsyncMock()
    monkeypatch.setattr(channel_sync, "publish_channel_sync_failed", told)

    await push_late_turn_finished(svc_role, web.id, RUN_ID, PICKED)

    assert notifier.pushed == []
    assert await _flag(session, web.id) is None
    told.assert_awaited_once_with(
        workspace_id=svc_role.workspace_id,
        session_id=web.id,
        reason=f"Couldn't open Slack thread: {reason}",
    )


@pytest.mark.parametrize(
    "error",
    [
        pytest.param(ChannelsNotEntitled("Agent Channels is off"), id="not-entitled"),
        pytest.param(NoChannelBot("No bot"), id="no-bot"),
        pytest.param(ChannelDestinationNotRoutable("Not in C1"), id="not-routable"),
        pytest.param(ChannelRecipientNotLinked("You hasn't linked Slack"), id="dm"),
    ],
)
def test_a_web_turns_destination_is_refused_before_the_turn_starts(
    error: Exception,
) -> None:
    role = _user_role()
    assert role.workspace_id is not None
    agent_session = _agent_session(
        role.workspace_id, created_by=USER_ID, entity_type="agent_preset"
    )
    app, _ = _client(role, agent_session)

    @asynccontextmanager
    async def with_session(role: Role):
        yield AgentSessionService(AsyncMock(), role)

    run_turn = AsyncMock()
    check = AsyncMock(side_effect=error)
    with (
        patch.object(
            session_router.AgentSessionService,
            "with_session",
            staticmethod(with_session),
        ),
        patch.object(
            AgentSessionService, "is_legacy_session", AsyncMock(return_value=False)
        ),
        patch.object(
            AgentSessionService,
            "validate_turn_request",
            AsyncMock(return_value=agent_session),
        ),
        patch.object(AgentSessionService, "session_access", AsyncMock()),
        patch.object(session_router, "_require_session_access", Mock()),
        patch.object(
            session_router,
            "_require_workspace_chat_entitlement_for_session_tree",
            AsyncMock(),
        ),
        patch.object(session_router, "check_turn_destination", check),
        patch.object(AgentSessionService, "run_turn", run_turn),
        TestClient(app) as client,
    ):
        response = client.post(
            f"/agent/sessions/{agent_session.id}/messages",
            json={
                "kind": "vercel",
                "message": {
                    "id": "m1",
                    "role": "user",
                    "parts": [{"type": "text", "text": "hi"}],
                },
                "channel_destination": {"kind": "channel", "id": "C1"},
            },
        )

    assert response.status_code == 422
    assert response.json() == {"detail": f"Couldn't open Slack thread: {error}"}
    assert check.await_args is not None
    assert check.await_args.args[2] == PICKED
    run_turn.assert_not_awaited()


@pytest.mark.anyio
async def test_a_session_without_a_preset_has_no_bot_to_open_a_thread() -> None:
    role = _user_role()
    assert role.workspace_id is not None
    agent_session = _agent_session(
        role.workspace_id, created_by=USER_ID, entity_type="copilot"
    )

    with pytest.raises(NoChannelBot):
        await check_turn_destination(role, agent_session, PICKED)


def test_a_web_turn_dms_only_its_sender() -> None:
    message = {"id": "m1", "role": "user", "parts": [{"type": "text", "text": "hi"}]}
    request = VercelChatRequest.model_validate(
        {"message": message, "channel_destination": {"kind": "direct_message"}}
    )
    assert request.channel_destination == ChannelDestinationRef(kind="direct_message")

    with pytest.raises(ValidationError, match="takes no id"):
        VercelChatRequest.model_validate(
            {
                "message": message,
                "channel_destination": {
                    "kind": "direct_message",
                    "id": "someone@example.com",
                },
            }
        )
    with pytest.raises(ValidationError, match="needs the channel's id"):
        VercelChatRequest.model_validate(
            {"message": message, "channel_destination": {"kind": "channel"}}
        )


def test_the_turns_destination_reaches_the_turn_workflow() -> None:
    context = SessionWorkflowContext(
        session_id=SESSION_ID,
        backend_id="default",
        harness_type=HarnessType.CLAUDE_CODE.value,
        title="t",
        entity_type=AgentSessionEntity.AGENT_PRESET,
        entity_id=uuid.uuid4(),
        tools=None,
        agent_preset_id=None,
        agent_preset_version_id=None,
        role=_user_role(),
        config=AgentConfig(model_name="m", model_provider="p"),
        prompt="hi",
        run_id=RUN_ID,
        stream_id=uuid.uuid4(),
        channel_destination=PICKED,
    )

    args = asyncio.run(DefaultBackend().build_workflow_args(context))

    assert args.channel_destination == PICKED


@pytest.mark.anyio
async def test_sync_turned_on_mid_turn_gets_that_turns_reply(
    session: AsyncSession, svc_role: Role, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert svc_role.workspace_id is not None
    bot = await _bot(session, svc_role.workspace_id)
    web = await _session(session, svc_role, entity_type=AgentSessionEntity.AGENT_PRESET)
    await session.commit()
    notifier = LateLinkNotifier(session, bot)
    _install(monkeypatch, notifier)
    _use_test_session(monkeypatch, session)
    # Unlinked when the turn started; the thread opened while it ran.
    assert await has_channel_bot(svc_role) is True
    await _link(session, bot, web.id, synced=True)

    await push_late_turn_finished(svc_role, web.id, RUN_ID)

    assert notifier.opened == []
    assert notifier.pushed == [TurnFinished(session_id=web.id, run_id=RUN_ID)]


@pytest.mark.anyio
async def test_a_workspace_without_a_bot_reads_no_link_at_a_turns_end(
    session: AsyncSession, svc_role: Role, monkeypatch: pytest.MonkeyPatch
) -> None:
    notifier = LateLinkNotifier(session, Mock())
    _install(monkeypatch, notifier)
    _use_test_session(monkeypatch, session)

    assert await has_channel_bot(svc_role) is False

    # Without a notifier the gate reads nothing at all.
    _install(monkeypatch, None)
    with patch.object(channel_sync.AgentSessionService, "with_session") as opened:
        assert await has_channel_bot(svc_role) is False
    opened.assert_not_called()


@pytest.mark.anyio
@pytest.mark.parametrize("bot", [True, False], ids=["bot", "no-bot"])
async def test_the_turns_end_reads_the_link_only_when_the_start_saw_a_bot(
    monkeypatch: pytest.MonkeyPatch, bot: bool
) -> None:
    late = AsyncMock()
    monkeypatch.setattr(session_activities, "push_late_turn_finished", late)
    monkeypatch.setattr(session_activities, "push_updates", AsyncMock())
    service = AsyncMock()

    @asynccontextmanager
    async def with_session(role: Role):
        yield service

    monkeypatch.setattr(
        session_activities.AgentSessionService,
        "with_session",
        staticmethod(with_session),
    )
    role = _user_role()

    await session_activities.finalize_turn_activity(
        session_activities.FinalizeTurnInput(
            role=role,
            session_id=SESSION_ID,
            run_id=RUN_ID,
            resolve_channel_link=bot,
            channel_destination=PICKED if bot else None,
        )
    )

    if bot:
        late.assert_awaited_once_with(role, SESSION_ID, RUN_ID, PICKED)
    else:
        late.assert_not_awaited()


@pytest.mark.anyio
@pytest.mark.parametrize("status", ["paused", "disconnected", "needs_reconnect"])
async def test_only_an_active_bot_turns_on_the_end_of_turn_lookup(
    session: AsyncSession,
    svc_role: Role,
    monkeypatch: pytest.MonkeyPatch,
    status: str,
) -> None:
    assert svc_role.workspace_id is not None
    bot = await _bot(session, svc_role.workspace_id)
    await session.commit()
    _install(monkeypatch, LateLinkNotifier(session, bot))
    _use_test_session(monkeypatch, session)
    assert await has_channel_bot(svc_role) is True

    bot.status = status  # pyright: ignore[reportAttributeAccessIssue]
    await session.commit()

    assert await has_channel_bot(svc_role) is False
