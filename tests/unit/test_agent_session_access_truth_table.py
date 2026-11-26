"""Who may act on, and decide approvals in, an agent session.

A user may act on a session they own; with ``agent:execute`` they may also act
on a workflow session, an ownerless channel or preset session (one a service
started, such as a trigger), or one they sent a message in. Deciding approvals
is acting plus ``agent:execute``.

Pinned through the surfaces that decide access, not the predicates behind them.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.agent.session import router as session_router
from tracecat.agent.session.schemas import (
    AgentSessionPendingRead,
    AgentSessionRead,
)
from tracecat.agent.session.service import AgentSessionService
from tracecat.agent.session.types import AgentSessionEntity
from tracecat.auth.types import Role
from tracecat.chat.enums import MessageKind
from tracecat.db.models import AgentSession, AgentSessionHistory, User

pytestmark = pytest.mark.anyio

WF, CH, PRESET = (
    AgentSessionEntity.WORKFLOW,
    AgentSessionEntity.EXTERNAL_CHANNEL,
    AgentSessionEntity.AGENT_PRESET,
)

# entity, creator, participated, agent:execute -> act, decide.
# creator: "none" = no user creator, "me" = the actor, "other" = a teammate.
TRUTH_TABLE: list[tuple[AgentSessionEntity, str, bool, bool, bool, bool]] = [
    # Owner: always acts; decides only with agent:execute.
    *[
        (e, "me", p, False, True, False)
        for e in (WF, CH, PRESET)
        for p in (False, True)
    ],
    *[(e, "me", p, True, True, True) for e in (WF, CH, PRESET) for p in (False, True)],
    # Without agent:execute a non-owner never acts or decides.
    *[
        (e, c, p, False, False, False)
        for e in (WF, CH, PRESET)
        for c in ("none", "other")
        for p in (False, True)
    ],
    # Workflow sessions are shared with everyone who may run agents.
    *[(WF, c, p, True, True, True) for c in ("none", "other") for p in (False, True)],
    # Channel and preset sessions: shared when ownerless, else by participation.
    *[
        row
        for e in (CH, PRESET)
        for row in (
            (e, "none", False, True, True, True),
            (e, "none", True, True, True, True),
            (e, "other", False, True, False, False),
            (e, "other", True, True, True, True),
        )
    ],
]
assert len(TRUTH_TABLE) == 36


def _user(name: str) -> User:
    return User(
        id=uuid.uuid4(),
        email=f"{name}-{uuid.uuid4()}@example.com",
        first_name=name,
        hashed_password="unused",
        is_active=True,
        is_verified=True,
        is_superuser=False,
        last_login_at=None,
    )


@pytest.mark.parametrize(
    ("entity", "creator", "participated", "execute", "act", "decide"),
    TRUTH_TABLE,
    ids=lambda v: str(v),
)
async def test_session_access_truth_table(
    session: AsyncSession,
    svc_role: Role,
    monkeypatch: pytest.MonkeyPatch,
    entity: AgentSessionEntity,
    creator: str,
    participated: bool,
    execute: bool,
    act: bool,
    decide: bool,
) -> None:
    actor, teammate = _user("actor"), _user("teammate")
    session.add_all([actor, teammate])
    await session.flush()
    created_by = {"none": None, "me": actor.id, "other": teammate.id}[creator]
    agent_session = AgentSession(
        id=uuid.uuid4(),
        title="Access",
        workspace_id=svc_role.workspace_id,
        entity_type=entity.value,
        entity_id=uuid.uuid4(),
        created_by=created_by,
        backend_id="oss",
    )
    session.add(agent_session)
    await session.flush()
    if participated:
        session.add(
            AgentSessionHistory(
                session_id=agent_session.id,
                workspace_id=svc_role.workspace_id,
                kind=MessageKind.CHAT_MESSAGE.value,
                author_id=actor.id,
                content={"type": "user", "message": {"role": "user", "content": "x"}},
            )
        )
    await session.commit()

    scopes = {"agent:read", "agent:execute"} if execute else {"agent:read"}
    role = svc_role.model_copy(
        update={"type": "user", "user_id": actor.id, "scopes": frozenset(scopes)}
    )

    # Enforced, one session: the pending list refuses actors who may not act.
    async def list_pending(self: Any, session_id: uuid.UUID) -> AgentSessionPendingRead:
        return AgentSessionPendingRead(session_id=session_id, items=[])

    monkeypatch.setattr(AgentSessionService, "list_pending", list_pending)
    try:
        await session_router.list_pending_messages(
            session_id=agent_session.id, role=role, session=session
        )
        enforced_act = True
    except HTTPException as exc:
        assert exc.status_code == 403
        enforced_act = False
    assert enforced_act is act

    # Viewed, one session.
    read = await session_router.get_session(
        session_id=agent_session.id, role=role, session=session
    )
    assert isinstance(read, AgentSessionRead)
    assert (read.is_readonly, read.can_decide_approvals) == (not act, decide)

    # Viewed, a listed page.
    listed = await AgentSessionService(session, role).list_sessions(limit=100)
    row = next(item for item in listed if item.id == agent_session.id)
    assert isinstance(row, AgentSessionRead)
    assert (row.is_readonly, row.can_decide_approvals) == (not act, decide)
