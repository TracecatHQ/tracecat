"""Whether a user took part in a session, and the access rule that follows."""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable, Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.agent.session.types import SessionAccessCheck, can_act_on_session
from tracecat.auth.types import Role
from tracecat.db.models import AgentSession, AgentSessionHistory
from tracecat.identifiers import UserID


async def sessions_with_messages_from(
    session: AsyncSession, user_id: UserID | None, session_ids: Sequence[uuid.UUID]
) -> set[uuid.UUID]:
    """The sessions among ``session_ids`` where ``user_id`` authored a message."""
    if user_id is None or not session_ids:
        return set()
    result = await session.scalars(
        select(AgentSessionHistory.session_id)
        .where(
            AgentSessionHistory.author_id == user_id,
            AgentSessionHistory.session_id.in_(session_ids),
        )
        .distinct()
    )
    return set(result.all())


async def may_act_on_session(
    role: Role,
    agent_session: AgentSession,
    *,
    sent_in: Callable[[Sequence[uuid.UUID]], Awaitable[set[uuid.UUID]]],
    check: SessionAccessCheck = can_act_on_session,
) -> bool:
    """Whether ``role`` passes ``check`` as owner, on a shared session, or as a participant.

    ``sent_in`` returns the sessions the role's user sent a message in; it runs only
    when the cheaper rule refuses.
    """
    entity_type, created_by = agent_session.entity_type, agent_session.created_by
    if check(role, entity_type, created_by, participated=False):
        return True
    participated = agent_session.id in await sent_in([agent_session.id])
    return participated and check(role, entity_type, created_by, participated=True)
