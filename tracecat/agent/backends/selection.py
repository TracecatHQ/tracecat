"""Choose the backend for new sessions whose caller does not pin one."""

from tracecat import config
from tracecat.agent.backends.access import can_use_backend
from tracecat.agent.backends.registry import DEFAULT_AGENT_BACKEND
from tracecat.agent.session.types import AgentSessionEntity
from tracecat.db.engine import SupportsExecute
from tracecat.identifiers import OrganizationID

# Surfaces that take the configured backend. The preset builder's tools and
# workflow agent actions exist only in the built-in backend's workflow.
SELECTABLE_ENTITIES = frozenset(
    {
        AgentSessionEntity.WORKSPACE_CHAT,
        AgentSessionEntity.AGENT_PRESET,
        AgentSessionEntity.CASE,
        AgentSessionEntity.EXTERNAL_CHANNEL,
    }
)


async def select_backend_id(
    session: SupportsExecute,
    organization_id: OrganizationID,
    entity_type: AgentSessionEntity,
) -> str:
    """Return the backend for a new session on this surface.

    The configured backend applies only to selectable surfaces and only for
    organizations entitled to it. Everything else gets the built-in backend.
    """
    backend_id = config.TRACECAT__AGENT_CHAT_BACKEND
    if entity_type not in SELECTABLE_ENTITIES:
        return DEFAULT_AGENT_BACKEND
    if not await can_use_backend(session, organization_id, backend_id):
        return DEFAULT_AGENT_BACKEND
    return backend_id
