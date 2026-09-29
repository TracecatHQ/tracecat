"""Database-backed session ancestry access tests."""

import uuid
from typing import Any, cast

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat import config
from tracecat.agent.session.router import (
    create_session,
    fork_session,
    get_session,
    get_session_vercel,
)
from tracecat.agent.session.schemas import AgentSessionCreate, AgentSessionForkRequest
from tracecat.agent.session.service import AgentSessionService
from tracecat.agent.session.types import AgentSessionEntity
from tracecat.auth.types import Role
from tracecat.db.models import AgentSession
from tracecat.exceptions import EntitlementRequired
from tracecat.tiers import defaults

pytestmark = [pytest.mark.anyio, pytest.mark.integration, pytest.mark.usefixtures("db")]


@pytest.mark.parametrize(
    "relationship", ["spawned_by_session_id", "forked_from_session_id"]
)
async def test_entitlement_loss_keeps_ancestry_readable_but_blocks_execution(
    session: AsyncSession,
    svc_role: Role,
    monkeypatch: pytest.MonkeyPatch,
    relationship: str,
) -> None:
    svc_role = svc_role.model_copy(update={"type": "service", "user_id": None})
    monkeypatch.setattr(config, "TRACECAT__EE_MULTI_TENANT", False)
    monkeypatch.setattr(
        defaults,
        "DEFAULT_ENTITLEMENTS",
        defaults.DEFAULT_ENTITLEMENTS.model_copy(update={"workspace_chat": False}),
    )
    root = AgentSession(
        id=uuid.uuid4(),
        workspace_id=svc_role.workspace_id,
        title="Original chat",
        entity_type="copilot",
        entity_id=svc_role.workspace_id,
    )
    child = AgentSession(
        id=uuid.uuid4(),
        workspace_id=svc_role.workspace_id,
        title="Continuation",
        entity_type="approval",
        entity_id=svc_role.workspace_id,
    )
    if relationship == "spawned_by_session_id":
        child.spawned_by_session_id = root.id
    else:
        child.forked_from_session_id = root.id
    nested = AgentSession(
        id=uuid.uuid4(),
        workspace_id=svc_role.workspace_id,
        title="Nested child",
        entity_type="approval",
        entity_id=svc_role.workspace_id,
        spawned_by_session_id=child.id,
    )
    unrelated = AgentSession(
        id=uuid.uuid4(),
        workspace_id=svc_role.workspace_id,
        title="Workflow run",
        entity_type="workflow",
        entity_id=uuid.uuid4(),
    )
    session.add_all([root, child, nested, unrelated])
    await session.commit()
    service = AgentSessionService(session, svc_role)

    results = await service.list_sessions(include_children=True)
    states = {result.id: result.is_readonly for result in results}
    assert states == {
        root.id: True,
        child.id: True,
        nested.id: True,
        unrelated.id: False,
    }
    # Explicit Workspace Chat filters remain readable too.
    chats = await service.list_sessions(entity_type=AgentSessionEntity.WORKSPACE_CHAT)
    assert [chat.id for chat in chats] == [root.id]
    assert chats[0].is_readonly is True

    for endpoint in (get_session, get_session_vercel):
        for agent_session in (root, child, nested):
            response = await cast(Any, endpoint).__wrapped__(
                session_id=agent_session.id,
                role=svc_role,
                session=session,
            )
            assert response.is_readonly is True
            assert response.messages == []

    with pytest.raises(EntitlementRequired):
        await cast(Any, fork_session).__wrapped__(
            session_id=nested.id,
            role=svc_role,
            session=session,
            request=AgentSessionForkRequest(entity_type=AgentSessionEntity.APPROVAL),
        )
    assert svc_role.workspace_id is not None
    with pytest.raises(EntitlementRequired):
        await cast(Any, create_session).__wrapped__(
            request=AgentSessionCreate(
                entity_type=AgentSessionEntity.APPROVAL,
                entity_id=svc_role.workspace_id,
                spawned_by_session_id=nested.id,
            ),
            role=svc_role,
            session=session,
        )

    # The recursive query terminates even if existing rows contain a cycle.
    root.spawned_by_session_id = nested.id
    await session.commit()
    assert await service.get_workspace_chat_session_ids([nested.id, unrelated.id]) == {
        nested.id
    }

    monkeypatch.setattr(
        defaults,
        "DEFAULT_ENTITLEMENTS",
        defaults.DEFAULT_ENTITLEMENTS.model_copy(update={"workspace_chat": True}),
    )
    results = await service.list_sessions(include_children=True)
    assert all(not result.is_readonly for result in results)
