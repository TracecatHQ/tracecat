"""Each web user prompt carries the Tracecat user who sent it."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.agent.adapter.vercel import convert_chat_messages_to_ui
from tracecat.agent.session.service import AgentSessionService
from tracecat.agent.session.types import AgentSessionEntity
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
