from __future__ import annotations

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.agent.session.service import AgentSessionService
from tracecat.agent.session.types import AgentSessionEntity
from tracecat.auth.types import Role
from tracecat.chat.enums import MessageKind
from tracecat.db.models import AgentSession, AgentSessionHistory, User, Workspace
from tracecat.exceptions import TracecatNotFoundError


async def _add_user(
    session: AsyncSession, user_id: uuid.UUID | None = None
) -> uuid.UUID:
    user = User(
        id=user_id or uuid.uuid4(),
        email=f"recall-user-{uuid.uuid4().hex[:8]}@example.com",
        hashed_password="hashed",
        is_active=True,
        is_superuser=False,
        is_verified=True,
    )
    session.add(user)
    await session.flush()
    return user.id


@pytest.fixture(autouse=True)
async def seed_role_user(session: AsyncSession, svc_role: Role) -> None:
    assert svc_role.user_id is not None
    await _add_user(session, svc_role.user_id)


async def _add_session(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    created_by: uuid.UUID | None,
    entity_type: AgentSessionEntity = AgentSessionEntity.CASE,
    parent_session_id: uuid.UUID | None = None,
    title: str = "Prior session",
) -> AgentSession:
    agent_session = AgentSession(
        id=uuid.uuid4(),
        workspace_id=workspace_id,
        title=title,
        created_by=created_by,
        entity_type=entity_type.value,
        entity_id=uuid.uuid4(),
        parent_session_id=parent_session_id,
    )
    session.add(agent_session)
    await session.flush()
    return agent_session


async def _add_history(
    session: AsyncSession,
    *,
    agent_session: AgentSession,
    text: str,
    kind: str = MessageKind.CHAT_MESSAGE.value,
) -> AgentSessionHistory:
    entry = AgentSessionHistory(
        session_id=agent_session.id,
        workspace_id=agent_session.workspace_id,
        content={"type": "user", "message": {"role": "user", "content": text}},
        search_text=text,
        kind=kind,
    )
    session.add(entry)
    await session.flush()
    await session.refresh(entry)
    return entry


@pytest.mark.anyio
async def test_search_session_messages_returns_snippet(
    session: AsyncSession,
    svc_role: Role,
) -> None:
    assert svc_role.workspace_id is not None
    agent_session = await _add_session(
        session, workspace_id=svc_role.workspace_id, created_by=svc_role.user_id
    )
    entry = await _add_history(
        session,
        agent_session=agent_session,
        text="We decided to use Hermes style recall for agent session search.",
    )
    await session.commit()

    service = AgentSessionService(session=session, role=svc_role)
    results = await service.search_session_messages("Hermes recall")

    assert len(results) == 1
    assert results[0].session_id == agent_session.id
    assert results[0].surrogate_id == entry.surrogate_id
    assert ">>>" in results[0].snippet
    assert "<<<" in results[0].snippet


@pytest.mark.anyio
async def test_search_session_messages_is_workspace_scoped(
    session: AsyncSession,
    svc_role: Role,
) -> None:
    assert svc_role.workspace_id is not None
    assert svc_role.organization_id is not None
    other_workspace = Workspace(
        name="other-workspace",
        organization_id=svc_role.organization_id,
    )
    session.add(other_workspace)
    await session.flush()
    other_session = await _add_session(
        session, workspace_id=other_workspace.id, created_by=svc_role.user_id
    )
    await _add_history(
        session,
        agent_session=other_session,
        text="needle appears only in another workspace",
    )
    await session.commit()

    service = AgentSessionService(session=session, role=svc_role)
    results = await service.search_session_messages("needle")

    assert results == []


@pytest.mark.anyio
async def test_search_session_messages_excludes_other_users_sessions(
    session: AsyncSession,
    svc_role: Role,
) -> None:
    assert svc_role.workspace_id is not None
    other_user_id = await _add_user(session)
    other_user_session = await _add_session(
        session,
        workspace_id=svc_role.workspace_id,
        created_by=other_user_id,
        title="Another user's session",
    )
    await _add_history(
        session,
        agent_session=other_user_session,
        text="visibility needle in another user's session",
    )
    own_session = await _add_session(
        session,
        workspace_id=svc_role.workspace_id,
        created_by=svc_role.user_id,
        title="My session",
    )
    await _add_history(
        session,
        agent_session=own_session,
        text="visibility needle in my own session",
    )
    await session.commit()

    service = AgentSessionService(session=session, role=svc_role)
    results = await service.search_session_messages("visibility needle")

    assert [result.session_id for result in results] == [own_session.id]


@pytest.mark.anyio
async def test_get_session_window_hides_other_users_sessions(
    session: AsyncSession,
    svc_role: Role,
) -> None:
    assert svc_role.workspace_id is not None
    other_user_id = await _add_user(session)
    other_user_session = await _add_session(
        session,
        workspace_id=svc_role.workspace_id,
        created_by=other_user_id,
        title="Another user's session",
    )
    entry = await _add_history(
        session,
        agent_session=other_user_session,
        text="private message",
    )
    await session.commit()

    service = AgentSessionService(session=session, role=svc_role)
    with pytest.raises(TracecatNotFoundError):
        await service.get_session_window(
            other_user_session.id,
            anchor_surrogate_id=entry.surrogate_id,
        )


@pytest.mark.anyio
async def test_search_session_messages_excludes_current_lineage(
    session: AsyncSession,
    svc_role: Role,
) -> None:
    assert svc_role.workspace_id is not None
    parent = await _add_session(
        session,
        workspace_id=svc_role.workspace_id,
        created_by=svc_role.user_id,
        title="Parent session",
    )
    child = await _add_session(
        session,
        workspace_id=svc_role.workspace_id,
        created_by=svc_role.user_id,
        parent_session_id=parent.id,
        title="Child session",
    )
    sibling = await _add_session(
        session,
        workspace_id=svc_role.workspace_id,
        created_by=svc_role.user_id,
        parent_session_id=parent.id,
        title="Sibling fork",
    )
    unrelated = await _add_session(
        session,
        workspace_id=svc_role.workspace_id,
        created_by=svc_role.user_id,
        title="Unrelated session",
    )
    await _add_history(
        session,
        agent_session=parent,
        text="lineage exclusion should hide this recall needle",
    )
    await _add_history(
        session,
        agent_session=child,
        text="lineage exclusion should also hide this recall needle",
    )
    await _add_history(
        session,
        agent_session=sibling,
        text="sibling fork recall needle is part of the same tree",
    )
    await _add_history(
        session,
        agent_session=unrelated,
        text="unrelated recall needle remains searchable",
    )
    await session.commit()

    service = AgentSessionService(session=session, role=svc_role)
    results = await service.search_session_messages(
        "recall needle",
        exclude_session_id=child.id,
    )

    assert [result.session_id for result in results] == [unrelated.id]


@pytest.mark.anyio
async def test_recall_hides_internal_kind_rows(
    session: AsyncSession,
    svc_role: Role,
) -> None:
    # INTERNAL rows (continuation prompts, interrupt artifacts) are hidden from
    # the UI timeline and must be invisible to recall too (review P2).
    assert svc_role.workspace_id is not None
    agent_session = await _add_session(
        session, workspace_id=svc_role.workspace_id, created_by=svc_role.user_id
    )
    first = await _add_history(
        session, agent_session=agent_session, text="visible kind needle one"
    )
    await _add_history(
        session,
        agent_session=agent_session,
        text="hidden internal kind needle",
        kind=MessageKind.INTERNAL.value,
    )
    await _add_history(
        session, agent_session=agent_session, text="visible kind needle two"
    )
    await session.commit()

    service = AgentSessionService(session=session, role=svc_role)

    search_results = await service.search_session_messages("internal kind needle")
    assert search_results == []

    window = await service.get_session_window(
        agent_session.id,
        anchor_surrogate_id=first.surrogate_id,
        window=5,
    )
    assert [message.text for message in window.messages] == [
        "visible kind needle one",
        "visible kind needle two",
    ]


@pytest.mark.anyio
async def test_search_paginates_past_large_fork_lineage(
    session: AsyncSession,
    svc_role: Role,
) -> None:
    # A fork tree with many sibling sessions must not fill the scan window and
    # hide unrelated lineages (Codex review P2, second pass).
    assert svc_role.workspace_id is not None
    unrelated = await _add_session(
        session,
        workspace_id=svc_role.workspace_id,
        created_by=svc_role.user_id,
        title="Unrelated session",
    )
    await _add_history(
        session,
        agent_session=unrelated,
        text="fork pagination needle outside the tree",
    )
    root = await _add_session(
        session,
        workspace_id=svc_role.workspace_id,
        created_by=svc_role.user_id,
        title="Fork root",
    )
    await _add_history(
        session, agent_session=root, text="fork pagination needle root mention"
    )
    for index in range(30):
        fork = await _add_session(
            session,
            workspace_id=svc_role.workspace_id,
            created_by=svc_role.user_id,
            parent_session_id=root.id,
            title=f"Fork {index}",
        )
        await _add_history(
            session,
            agent_session=fork,
            text=f"fork pagination needle sibling mention {index}",
        )
    await session.commit()

    service = AgentSessionService(session=session, role=svc_role)
    # limit=2 -> page size 25 < 31 tree hits; the older unrelated session sorts
    # last and is only reachable by paginating past the fork tree.
    results = await service.search_session_messages("fork pagination needle", limit=2)

    root_ids = {result.session_id for result in results}
    assert unrelated.id in root_ids
    assert len(results) == 2


@pytest.mark.anyio
async def test_search_session_messages_chatty_session_does_not_starve_others(
    session: AsyncSession,
    svc_role: Role,
) -> None:
    # One session with many high-ranking hits must not fill the scan window
    # and hide other matching sessions (Codex review P2).
    assert svc_role.workspace_id is not None
    chatty = await _add_session(
        session,
        workspace_id=svc_role.workspace_id,
        created_by=svc_role.user_id,
        title="Chatty session",
    )
    for index in range(60):
        await _add_history(
            session,
            agent_session=chatty,
            text=f"starvation needle repeated mention {index}",
        )
    quiet = await _add_session(
        session,
        workspace_id=svc_role.workspace_id,
        created_by=svc_role.user_id,
        title="Quiet session",
    )
    await _add_history(
        session,
        agent_session=quiet,
        text="starvation needle single mention",
    )
    await session.commit()

    service = AgentSessionService(session=session, role=svc_role)
    results = await service.search_session_messages("starvation needle", limit=10)

    assert {result.session_id for result in results} == {chatty.id, quiet.id}


@pytest.mark.anyio
async def test_search_session_messages_clamps_limit(
    session: AsyncSession,
    svc_role: Role,
) -> None:
    assert svc_role.workspace_id is not None
    for index in range(30):
        agent_session = await _add_session(
            session,
            workspace_id=svc_role.workspace_id,
            created_by=svc_role.user_id,
            title=f"Session {index}",
        )
        await _add_history(
            session,
            agent_session=agent_session,
            text=f"shared clamp needle number {index}",
        )
    await session.commit()

    service = AgentSessionService(session=session, role=svc_role)
    results = await service.search_session_messages("shared clamp needle", limit=100)

    assert len(results) == 25


@pytest.mark.anyio
async def test_get_session_window_returns_boundary_counts(
    session: AsyncSession,
    svc_role: Role,
) -> None:
    assert svc_role.workspace_id is not None
    agent_session = await _add_session(
        session, workspace_id=svc_role.workspace_id, created_by=svc_role.user_id
    )
    entries = [
        await _add_history(
            session,
            agent_session=agent_session,
            text=f"message {index}",
        )
        for index in range(1, 7)
    ]
    await session.commit()

    service = AgentSessionService(session=session, role=svc_role)
    window = await service.get_session_window(
        agent_session.id,
        anchor_surrogate_id=entries[2].surrogate_id,
        window=2,
    )

    assert [message.text for message in window.messages] == [
        "message 2",
        "message 3",
        "message 4",
        "message 5",
    ]
    assert window.messages_before == 1
    assert window.messages_after == 1


@pytest.mark.anyio
async def test_get_session_window_clamps_window(
    session: AsyncSession,
    svc_role: Role,
) -> None:
    assert svc_role.workspace_id is not None
    agent_session = await _add_session(
        session, workspace_id=svc_role.workspace_id, created_by=svc_role.user_id
    )
    entries = [
        await _add_history(
            session,
            agent_session=agent_session,
            text=f"clamped message {index}",
        )
        for index in range(1, 5)
    ]
    await session.commit()

    service = AgentSessionService(session=session, role=svc_role)
    window = await service.get_session_window(
        agent_session.id,
        anchor_surrogate_id=entries[1].surrogate_id,
        window=0,
    )

    assert [message.text for message in window.messages] == [
        "clamped message 2",
        "clamped message 3",
    ]
    assert window.messages_before == 1
    assert window.messages_after == 1
