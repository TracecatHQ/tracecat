"""Database-backed session ancestry access tests."""

import uuid
from typing import Any, cast

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from tests.database import TEST_DB_CONFIG
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
from tracecat.db.models import (
    AgentSession,
    AgentSessionHistory,
    Organization,
    Workspace,
)
from tracecat.exceptions import EntitlementRequired, TracecatNotFoundError
from tracecat.tiers import defaults

pytestmark = [pytest.mark.anyio, pytest.mark.integration, pytest.mark.usefixtures("db")]


async def test_fork_captures_committed_history_and_sdk_identity_together() -> None:
    # Use independent transactions at production isolation; the session fixture
    # uses SERIALIZABLE isolation and an outer transaction that never commits.
    engine = create_async_engine(
        TEST_DB_CONFIG.test_url, isolation_level="READ COMMITTED"
    )
    organization_id, workspace_id, source_id = (uuid.uuid4() for _ in range(3))
    role = Role(
        type="service",
        service_id="tracecat-api",
        organization_id=organization_id,
        workspace_id=workspace_id,
    )
    try:
        async with AsyncSession(engine, expire_on_commit=False) as writer:
            writer.add(
                Organization(
                    id=organization_id,
                    name="Fork snapshot test",
                    slug=f"fork-snapshot-{organization_id.hex}",
                )
            )
            await writer.flush()
            writer.add(
                Workspace(
                    id=workspace_id,
                    name="Fork snapshot workspace",
                    organization_id=organization_id,
                )
            )
            await writer.flush()
            source = AgentSession(
                id=source_id,
                workspace_id=workspace_id,
                title="Source awaiting its first message",
                entity_type="workflow",
                entity_id=uuid.uuid4(),
            )
            writer.add(source)
            await writer.commit()

            async with AsyncSession(engine, expire_on_commit=False) as reader:
                service = AgentSessionService(reader, role)
                # The route's authorization checks may already have loaded A.
                cached_source = await service.get_session(source_id)
                assert cached_source is not None
                assert cached_source.sdk_session_id is None

                # The executor publishes the SDK ID and first message together
                # while the fork request still holds its earlier source object.
                source.sdk_session_id = "sdk-first-message"
                first_message = AgentSessionHistory(
                    session_id=source_id,
                    workspace_id=workspace_id,
                    kind="chat-message",
                    content={"type": "user", "message": {"content": "Hello"}},
                )
                writer.add(first_message)
                await writer.commit()
                assert cached_source.sdk_session_id is None

                fork = await service.fork_session(source_id)
                assert fork.forked_from_history_id == first_message.surrogate_id
                assert fork.forked_from_sdk_session_id == "sdk-first-message"
    finally:
        async with AsyncSession(engine) as cleanup:
            await cleanup.execute(delete(Workspace).where(Workspace.id == workspace_id))
            await cleanup.execute(
                delete(Organization).where(Organization.id == organization_id)
            )
            await cleanup.commit()
        await engine.dispose()


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


async def test_ancestry_rejects_references_to_existing_sessions_in_another_workspace(
    session: AsyncSession,
    svc_role: Role,
) -> None:
    assert svc_role.organization_id is not None
    assert svc_role.workspace_id is not None
    other_workspace = Workspace(
        id=uuid.uuid4(),
        name="Other ancestry test workspace",
        organization_id=svc_role.organization_id,
    )
    session.add(other_workspace)
    await session.flush()
    own_source = AgentSession(
        id=uuid.uuid4(),
        workspace_id=svc_role.workspace_id,
        title="Local source",
        entity_type="workflow",
        entity_id=uuid.uuid4(),
    )
    other_source = AgentSession(
        id=uuid.uuid4(),
        workspace_id=other_workspace.id,
        title="Other workspace source",
        entity_type="copilot",
        entity_id=other_workspace.id,
    )
    session.add_all([own_source, other_source])
    await session.commit()
    service = AgentSessionService(session, svc_role)
    assert await service.get_session(other_source.id) is None

    with pytest.raises(TracecatNotFoundError, match="Parent session not found"):
        await service.create_session(
            AgentSessionCreate(
                entity_type=AgentSessionEntity.WORKSPACE_CHAT,
                entity_id=svc_role.workspace_id,
                spawned_by_session_id=other_source.id,
            )
        )
    with pytest.raises(TracecatNotFoundError, match="Source session"):
        await service.fork_session(other_source.id)
    with pytest.raises(TracecatNotFoundError, match="Parent session not found"):
        await service.fork_session(own_source.id, spawned_by_session_id=other_source.id)
    # Rejected creates and forks must not leave any child rows behind.
    assert set((await session.scalars(select(AgentSession.id))).all()) == {
        own_source.id,
        other_source.id,
    }

    # Existing malformed links must not expose another workspace's history.
    malformed_fork = AgentSession(
        id=uuid.uuid4(),
        workspace_id=svc_role.workspace_id,
        title="Fork with an invalid source",
        entity_type="workflow",
        entity_id=uuid.uuid4(),
        forked_from_session_id=other_source.id,
    )
    session.add(malformed_fork)
    await session.commit()
    with pytest.raises(TracecatNotFoundError, match="Fork source session not found"):
        await service.list_messages(malformed_fork.id)
    assert await service.get_workspace_chat_session_ids([malformed_fork.id]) == set()
