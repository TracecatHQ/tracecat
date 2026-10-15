"""Choose the agent backend for sessions that a surface creates itself."""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.agent.backends.registry import DEFAULT_AGENT_BACKEND
from tracecat.auth.types import Role


async def select_session_backend(
    session: AsyncSession,
    role: Role,
    *,
    agent_preset_id: uuid.UUID | None,
) -> str:
    """Return the backend for a new session that its surface does not pin.

    Call this only when creating a session; existing sessions keep the backend
    they were created with. The returned identifier is not validated here:
    session creation rejects an unavailable backend instead of rerouting.

    Args:
        session: Database session for reading the selection setting.
        role: The role creating the session.
        agent_preset_id: The preset the new session runs, if any.

    Returns:
        The backend identifier for the new session.
    """
    del session, role, agent_preset_id
    return DEFAULT_AGENT_BACKEND
