"""Each web user prompt carries the Tracecat user who sent it."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.agent.adapter.vercel import convert_chat_messages_to_ui
from tracecat.agent.channel_bots.messages import ChannelMessage, ChannelMessageSender
from tracecat.agent.channel_bots.schemas import ChannelBotProvider
from tracecat.agent.session.activities import (
    LoadSessionMessagesInput,
    load_session_messages_activity,
)
from tracecat.agent.session.enums import UserMessageOrigin
from tracecat.agent.session.service import AgentSessionService
from tracecat.agent.session.types import AgentSessionEntity
from tracecat.auth.types import Role
from tracecat.authz.membership import ensure_member
from tracecat.authz.seeding import seed_system_roles_for_org, seed_system_scopes
from tracecat.chat.enums import MessageKind
from tracecat.db.models import (
    AgentChannelUser,
    AgentSession,
    AgentSessionHistory,
    User,
)

pytestmark = pytest.mark.anyio


async def test_list_messages_names_each_prompt_author(
    session: AsyncSession, svc_role: Role
) -> None:
    author = User(
        id=uuid.uuid4(),
        email=f"sam-{uuid.uuid4()}@example.com",
        first_name="Sam",
        hashed_password="unused",
        is_active=True,
        is_verified=True,
        is_superuser=False,
        last_login_at=None,
    )
    agent_session = AgentSession(
        id=uuid.uuid4(),
        title="Shared",
        workspace_id=svc_role.workspace_id,
        entity_type=AgentSessionEntity.WORKFLOW.value,
        entity_id=uuid.uuid4(),
    )
    session.add_all([author, agent_session])
    await session.flush()
    session.add_all(
        [
            AgentSessionHistory(
                session_id=agent_session.id,
                workspace_id=svc_role.workspace_id,
                kind=MessageKind.CHAT_MESSAGE.value,
                author_id=author.id,
                content={
                    "type": "user",
                    "uuid": "prompt",
                    "message": {"role": "user", "content": "triage this"},
                },
            ),
            AgentSessionHistory(
                session_id=agent_session.id,
                workspace_id=svc_role.workspace_id,
                kind=MessageKind.CHAT_MESSAGE.value,
                content={
                    "type": "user",
                    "uuid": "unattributed",
                    "message": {"role": "user", "content": "from a workflow"},
                },
            ),
        ]
    )
    await session.commit()

    service = AgentSessionService(session=session, role=svc_role)
    messages = await service.list_messages(agent_session.id, with_authors=True)

    assert [message.author and message.author.id for message in messages] == [
        author.id,
        None,
    ]
    # Only web reads ask for authors; agent and workflow reads never carry them.
    default_read = await service.list_messages(agent_session.id)
    assert [message.author for message in default_read] == [None, None]
    attributed, unattributed = convert_chat_messages_to_ui(messages)
    assert attributed.metadata is not None
    assert attributed.metadata["author"] == {
        "id": str(author.id),
        "email": author.email,
        "first_name": "Sam",
        "last_name": None,
    }
    assert unattributed.metadata is None or "author" not in unattributed.metadata


async def test_workflow_message_history_leaves_out_authors(
    session: AsyncSession, svc_role: Role, monkeypatch: pytest.MonkeyPatch
) -> None:
    @asynccontextmanager
    async def with_session(
        role: Role | None = None, **_: object
    ) -> AsyncIterator[AgentSessionService]:
        yield AgentSessionService(session=session, role=role)

    monkeypatch.setattr(AgentSessionService, "with_session", with_session)
    author = User(
        id=uuid.uuid4(),
        email=f"sam-{uuid.uuid4()}@example.com",
        hashed_password="unused",
        is_active=True,
        is_verified=True,
        is_superuser=False,
        last_login_at=None,
    )
    agent_session = AgentSession(
        id=uuid.uuid4(),
        title="Shared",
        workspace_id=svc_role.workspace_id,
        entity_type=AgentSessionEntity.WORKFLOW.value,
        entity_id=uuid.uuid4(),
    )
    session.add_all([author, agent_session])
    await session.flush()
    session.add(
        AgentSessionHistory(
            session_id=agent_session.id,
            workspace_id=svc_role.workspace_id,
            kind=MessageKind.CHAT_MESSAGE.value,
            author_id=author.id,
            content={
                "type": "user",
                "uuid": "prompt",
                "message": {"role": "user", "content": "triage this"},
            },
        )
    )
    await session.commit()

    result = await load_session_messages_activity(
        LoadSessionMessagesInput(role=svc_role, session_id=agent_session.id)
    )

    assert result.messages is not None
    assert [message.author for message in result.messages] == [None]


async def test_listing_marks_a_private_session_actable_once_the_user_sent_in_it(
    session: AsyncSession, svc_role: Role
) -> None:
    viewer = User(
        id=uuid.uuid4(),
        email=f"bo-{uuid.uuid4()}@example.com",
        hashed_password="unused",
        is_active=True,
        is_verified=True,
        is_superuser=False,
        last_login_at=None,
    )
    owner = uuid.uuid4()
    joined, untouched = (
        AgentSession(
            id=uuid.uuid4(),
            title=title,
            workspace_id=svc_role.workspace_id,
            entity_type=AgentSessionEntity.AGENT_PRESET.value,
            entity_id=uuid.uuid4(),
            created_by=owner,
        )
        for title in ("Joined", "Untouched")
    )
    session.add_all(
        [
            viewer,
            User(
                id=owner,
                email=f"al-{uuid.uuid4()}@example.com",
                hashed_password="unused",
                is_active=True,
                is_verified=True,
                is_superuser=False,
                last_login_at=None,
            ),
        ]
    )
    await session.flush()
    session.add_all([joined, untouched])
    await session.flush()
    session.add(
        AgentSessionHistory(
            session_id=joined.id,
            workspace_id=svc_role.workspace_id,
            kind=MessageKind.CHAT_MESSAGE.value,
            author_id=viewer.id,
            content={"type": "user", "message": {"role": "user", "content": "hi"}},
        )
    )
    await session.commit()
    role = svc_role.model_copy(
        update={
            "type": "user",
            "user_id": viewer.id,
            "service_id": "tracecat-api",
            "scopes": frozenset({"agent:execute", "agent:read"}),
        }
    )

    listed = await AgentSessionService(session=session, role=role).list_sessions()

    readonly = {item.title: item.is_readonly for item in listed}
    assert readonly["Joined"] is False
    assert readonly["Untouched"] is True


def _user(name: str) -> User:
    return User(
        id=uuid.uuid4(),
        email=f"{name}-{uuid.uuid4()}@example.com",
        hashed_password="unused",
        is_active=True,
        is_verified=True,
        is_superuser=False,
        last_login_at=None,
    )


async def test_only_a_participant_may_act_on_a_teammates_private_session(
    session: AsyncSession, svc_role: Role
) -> None:
    owner, participant, bystander = _user("al"), _user("bo"), _user("cy")
    session.add_all([owner, participant, bystander])
    await session.flush()
    private = AgentSession(
        id=uuid.uuid4(),
        title="Private",
        workspace_id=svc_role.workspace_id,
        entity_type=AgentSessionEntity.AGENT_PRESET.value,
        entity_id=uuid.uuid4(),
        created_by=owner.id,
    )
    session.add(private)
    await session.flush()
    session.add(
        AgentSessionHistory(
            session_id=private.id,
            workspace_id=svc_role.workspace_id,
            kind=MessageKind.CHAT_MESSAGE.value,
            author_id=participant.id,
            content={"type": "user", "message": {"role": "user", "content": "hi"}},
        )
    )
    await session.commit()

    def as_member(user: User, scopes: frozenset[str]) -> Role:
        return svc_role.model_copy(
            update={
                "type": "user",
                "user_id": user.id,
                "service_id": "tracecat-api",
                "scopes": scopes,
            }
        )

    async def access(user: User, scopes: frozenset[str]) -> tuple[bool, bool]:
        role = as_member(user, scopes)
        got = await AgentSessionService(session=session, role=role).session_access(
            private
        )
        return got.act, got.decide

    execute = frozenset({"agent:execute", "agent:read"})
    assert await access(participant, execute) == (True, True)
    assert await access(bystander, execute) == (False, False)
    # Participation shares the session only with members who may run agents.
    assert await access(participant, frozenset({"agent:read"})) == (False, False)


def _envelope(provider_user_id: str) -> str:
    return ChannelMessage(
        provider=ChannelBotProvider.SLACK,
        text="triage this",
        sender=ChannelMessageSender(
            provider_user_id=provider_user_id, provider_team_id="T1", name="Dana"
        ),
    ).render()


async def test_list_messages_names_a_linked_channel_sender_at_read_time(
    session: AsyncSession, svc_role: Role
) -> None:
    assert svc_role.organization_id is not None
    await seed_system_scopes(session)
    await seed_system_roles_for_org(session, svc_role.organization_id)
    linked = _user("dana")
    session.add(linked)
    await session.flush()
    await ensure_member(session, svc_role.organization_id, linked.id)
    session.add(
        AgentChannelUser(
            organization_id=svc_role.organization_id,
            provider="slack",
            provider_tenant_id="T1",
            provider_user_id="U1",
            user_id=linked.id,
        )
    )
    channel = AgentSession(
        id=uuid.uuid4(),
        title="Slack thread",
        workspace_id=svc_role.workspace_id,
        entity_type=AgentSessionEntity.EXTERNAL_CHANNEL.value,
        entity_id=uuid.uuid4(),
    )
    session.add(channel)
    await session.flush()
    session.add_all(
        AgentSessionHistory(
            session_id=channel.id,
            workspace_id=svc_role.workspace_id,
            kind=MessageKind.CHAT_MESSAGE.value,
            origin=UserMessageOrigin.CHANNEL.value,
            content={
                "type": "user",
                "message": {"role": "user", "content": _envelope(sender)},
            },
        )
        for sender in ("U1", "U2")
    )
    await session.commit()

    service = AgentSessionService(session=session, role=svc_role)
    messages = await service.list_messages(channel.id, with_authors=True)

    # Linked before or after the message: named now. Unlinked: no author.
    assert [m.author and m.author.id for m in messages] == [linked.id, None]
    # Display only: participation still reads the stored author.
    stored = await session.scalars(
        select(AgentSessionHistory.author_id).where(
            AgentSessionHistory.session_id == channel.id
        )
    )
    assert set(stored) == {None}
    # A Retry copies the stored author, never the read-time name.
    held = messages[0].channel_message
    assert held is not None
    received = await service._received_channel_message(channel, held)
    assert received is not None and received.author is None


async def test_participating_lists_my_chats_and_channel_sessions_i_sent_in(
    session: AsyncSession, svc_role: Role
) -> None:
    me, teammate = _user("me"), _user("al")
    session.add_all([me, teammate])
    await session.flush()

    def _session(title: str, entity: AgentSessionEntity, creator: User | None):
        return AgentSession(
            id=uuid.uuid4(),
            title=title,
            workspace_id=svc_role.workspace_id,
            entity_type=entity.value,
            entity_id=uuid.uuid4(),
            created_by=creator.id if creator else None,
        )

    copilot, channel = (
        AgentSessionEntity.WORKSPACE_CHAT,
        AgentSessionEntity.EXTERNAL_CHANNEL,
    )
    sessions = [
        _session("Mine", copilot, me),
        _session("Joined chat", copilot, teammate),
        _session("Joined thread", channel, None),
        _session("Teammate's chat", copilot, teammate),
        _session("Silent thread", channel, None),
        _session("Joined workflow", AgentSessionEntity.WORKFLOW, None),
    ]
    session.add_all(sessions)
    await session.flush()
    session.add_all(
        AgentSessionHistory(
            session_id=sent_in.id,
            workspace_id=svc_role.workspace_id,
            kind=MessageKind.CHAT_MESSAGE.value,
            author_id=me.id,
            content={"type": "user", "message": {"role": "user", "content": "hi"}},
        )
        for sent_in in (sessions[1], sessions[2], sessions[5])
    )
    await session.commit()
    role = svc_role.model_copy(update={"user_id": me.id})

    listed = await AgentSessionService(session=session, role=role).list_sessions(
        participating=True
    )

    assert {item.title for item in listed} == {
        "Mine",
        "Joined chat",
        "Joined thread",
    }
