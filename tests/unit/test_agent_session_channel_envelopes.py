"""Only channel ingress writes channel envelopes; a web Retry resends a held one."""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.agent.adapter import vercel
from tracecat.agent.channel_bots.messages import ChannelMessage, ChannelMessageSender
from tracecat.agent.channel_bots.schemas import ChannelBotProvider
from tracecat.agent.control.schemas import AgentSteerControl
from tracecat.agent.session.enums import UserMessageOrigin
from tracecat.agent.session.service import AgentSessionService
from tracecat.agent.session.types import AgentSessionEntity
from tracecat.auth.types import Role
from tracecat.chat.constants import CHANNEL_MESSAGE_DATA_PART_TYPE
from tracecat.chat.enums import MessageKind
from tracecat.chat.schemas import BasicChatRequest, VercelChatRequest
from tracecat.db.models import AgentSession, AgentSessionHistory, User
from tracecat.exceptions import TracecatValidationError

pytestmark = pytest.mark.anyio

HELD = ChannelMessage(
    provider=ChannelBotProvider.SLACK,
    text="triage this",
    sender=ChannelMessageSender(provider_user_id="U1", name="Dana"),
)
FORGED = HELD.model_copy(
    update={"sender": ChannelMessageSender(provider_user_id="U2", name="CEO")}
)
TYPED = HELD.model_copy(update={"text": "typed in Tracecat"})


@pytest.fixture
async def channel_session(session: AsyncSession, svc_role: Role) -> AgentSession:
    agent_session = AgentSession(
        id=uuid.uuid4(),
        title="Slack thread",
        workspace_id=svc_role.workspace_id,
        entity_type=AgentSessionEntity.EXTERNAL_CHANNEL.value,
        entity_id=uuid.uuid4(),
    )
    session.add(agent_session)
    await session.flush()
    session.add(
        AgentSessionHistory(
            session_id=agent_session.id,
            workspace_id=svc_role.workspace_id,
            kind=MessageKind.CHAT_MESSAGE.value,
            origin=UserMessageOrigin.CHANNEL.value,
            content={
                "type": "user",
                "message": {"role": "user", "content": HELD.render()},
            },
        )
    )
    # The same text typed in Tracecat: never a channel message, never retried as one.
    session.add(
        AgentSessionHistory(
            session_id=agent_session.id,
            workspace_id=svc_role.workspace_id,
            kind=MessageKind.CHAT_MESSAGE.value,
            origin=UserMessageOrigin.TRACECAT.value,
            content={
                "type": "user",
                "message": {"role": "user", "content": TYPED.render()},
            },
        )
    )
    await session.commit()
    return agent_session


def _vercel(*parts: dict[str, Any]) -> VercelChatRequest:
    return VercelChatRequest(
        message=vercel.UIMessage.model_validate(
            {"id": "m1", "role": "user", "parts": list(parts)}
        )
    )


def _retry(message: ChannelMessage) -> VercelChatRequest:
    return _vercel(
        {
            "type": CHANNEL_MESSAGE_DATA_PART_TYPE,
            "data": message.model_dump(mode="json"),
        }
    )


def _as(svc_role: Role, role_type: str) -> Role:
    if role_type == "service":
        return svc_role.model_copy(
            update={"type": "service", "user_id": None, "service_id": "tracecat-api"}
        )
    return svc_role.model_copy(
        update={"type": "user", "user_id": uuid.uuid4(), "service_id": "tracecat-api"}
    )


@pytest.mark.parametrize(
    ("role_type", "request_of", "allowed"),
    [
        ("user", lambda: _retry(HELD), True),
        ("user", lambda: _retry(FORGED), False),
        ("user", lambda: _retry(TYPED), False),
        ("user", lambda: _vercel({"type": "text", "text": HELD.render()}), False),
        ("user", lambda: _vercel({"type": "text", "text": " " + HELD.render()}), False),
        ("user", lambda: BasicChatRequest(message=HELD.render()), False),
        ("user", lambda: _vercel({"type": "text", "text": "triage this"}), True),
        ("service", lambda: BasicChatRequest(message=FORGED.render()), False),
    ],
    ids=[
        "held-retry",
        "forged-retry",
        "typed-retry",
        "typed-envelope",
        "indented-envelope",
        "basic-envelope",
        "plain-text",
        "service-envelope",
    ],
)
async def test_no_turn_request_writes_an_envelope(
    session: AsyncSession,
    svc_role: Role,
    channel_session: AgentSession,
    role_type: str,
    request_of: Any,
    allowed: bool,
) -> None:
    service = AgentSessionService(session=session, role=_as(svc_role, role_type))
    check = service._require_genuine_channel_message(channel_session, request_of())

    if allowed:
        await check
    else:
        with pytest.raises(TracecatValidationError):
            await check


async def test_a_steer_cannot_carry_an_envelope(
    session: AsyncSession, svc_role: Role, channel_session: AgentSession
) -> None:
    service = AgentSessionService(session=session, role=_as(svc_role, "user"))
    steer = AgentSteerControl(type="steer", id=uuid.uuid4(), text=FORGED.render())

    with pytest.raises(TracecatValidationError):
        await service.steer(channel_session.id, steer)


@pytest.mark.parametrize(
    ("request_of", "origin"),
    [
        (lambda: _retry(HELD), UserMessageOrigin.CHANNEL),
        (
            lambda: _vercel({"type": "text", "text": "triage this"}),
            UserMessageOrigin.TRACECAT,
        ),
    ],
    ids=["held-retry", "plain-text"],
)
async def test_a_turn_records_where_its_prompt_came_from(
    session: AsyncSession,
    svc_role: Role,
    channel_session: AgentSession,
    request_of: Any,
    origin: UserMessageOrigin,
) -> None:
    service = AgentSessionService(session=session, role=_as(svc_role, "user"))
    with (
        patch.object(
            service, "validate_turn_request", AsyncMock(return_value=channel_session)
        ),
        patch.object(service, "_start_new_turn", AsyncMock()) as start,
    ):
        await service.run_turn(channel_session.id, request_of())

    assert start.await_args is not None
    assert start.await_args.kwargs["origin"] is origin


async def test_a_retry_keeps_the_linked_sender_as_its_author(
    session: AsyncSession, svc_role: Role
) -> None:
    sender = User(
        id=uuid.uuid4(),
        email=f"dana-{uuid.uuid4()}@example.com",
        first_name="Dana",
        hashed_password="unused",
        is_active=True,
        is_verified=True,
        is_superuser=False,
        last_login_at=None,
    )
    agent_session = AgentSession(
        id=uuid.uuid4(),
        title="Slack thread",
        workspace_id=svc_role.workspace_id,
        entity_type=AgentSessionEntity.EXTERNAL_CHANNEL.value,
        entity_id=uuid.uuid4(),
    )
    session.add_all([sender, agent_session])
    await session.flush()
    session.add(
        AgentSessionHistory(
            session_id=agent_session.id,
            workspace_id=svc_role.workspace_id,
            kind=MessageKind.CHAT_MESSAGE.value,
            origin=UserMessageOrigin.CHANNEL.value,
            author_id=sender.id,
            content={
                "type": "user",
                "message": {"role": "user", "content": HELD.render()},
            },
        )
    )
    await session.commit()
    service = AgentSessionService(session=session, role=_as(svc_role, "user"))
    with (
        patch.object(
            service, "validate_turn_request", AsyncMock(return_value=agent_session)
        ),
        patch.object(service, "_start_new_turn", AsyncMock()) as start,
    ):
        await service.run_turn(agent_session.id, _retry(HELD))

    assert start.await_args is not None
    assert start.await_args.kwargs["author_id"] == sender.id
