"""Each web user prompt carries the Tracecat user who sent it."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.agent.adapter.vercel import convert_chat_messages_to_ui
from tracecat.agent.session.activities import (
    LoadSessionMessagesInput,
    load_session_messages_activity,
)
from tracecat.agent.session.service import AgentSessionService, may_act_on_session
from tracecat.agent.session.types import AgentSessionEntity, can_decide_on_session
from tracecat.auth.types import Role
from tracecat.chat.enums import MessageKind
from tracecat.db.models import AgentSession, AgentSessionHistory, User

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
    messages = await service.list_messages(agent_session.id)

    assert [message.author and message.author.id for message in messages] == [
        author.id,
        None,
    ]
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

    async def may_act(user: User, scopes: frozenset[str], **kwargs: Any) -> bool:
        role = as_member(user, scopes)
        sent_in = AgentSessionService(
            session=session, role=role
        ).participated_session_ids
        return await may_act_on_session(role, private, sent_in=sent_in, **kwargs)

    execute = frozenset({"agent:execute", "agent:read"})
    assert await may_act(participant, execute)
    assert await may_act(participant, execute, check=can_decide_on_session)
    assert not await may_act(bystander, execute)
    # Participation shares the session only with members who may run agents.
    assert not await may_act(participant, frozenset({"agent:read"}))
