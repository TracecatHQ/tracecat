"""Entitlement gate for agent backends other than the built-in one.

The gate applies to new sessions and to every new turn of an existing
session, so revoking the entitlement stops further turns on that backend.
A turn that already started finishes, including approval decisions for a
turn parked on approvals.

Workflow agent actions need a second entitlement on top of the preview one,
so an organization can move them back to the built-in backend without
stopping its chats on the configured one.
"""

from tracecat.agent.backends.registry import DEFAULT_AGENT_BACKEND
from tracecat.agent.session.types import AgentSessionEntity
from tracecat.db.engine import SupportsExecute
from tracecat.exceptions import EntitlementRequired
from tracecat.identifiers import OrganizationID
from tracecat.tiers.access import is_org_entitled
from tracecat.tiers.enums import Entitlement

SESSION_BACKEND_NOT_ENTITLED_MESSAGE = (
    "This chat uses a feature your workspace no longer has. Start a new chat."
)


def required_backend_entitlements(
    backend_id: str | None,
    entity_type: AgentSessionEntity | None,
) -> tuple[Entitlement, ...]:
    """Entitlements an organization needs to run turns on a backend.

    Args:
        backend_id: The session's backend. None means the built-in one.
        entity_type: The session's surface, or None when no surface applies,
            such as when listing the backends an organization may use.
    """
    if backend_id is None or backend_id == DEFAULT_AGENT_BACKEND:
        return ()
    if entity_type is AgentSessionEntity.WORKFLOW:
        return (
            Entitlement.AGENT_RUNTIME_PREVIEW,
            Entitlement.AGENT_RUNTIME_WORKFLOWS,
        )
    return (Entitlement.AGENT_RUNTIME_PREVIEW,)


async def missing_backend_entitlement(
    session: SupportsExecute,
    organization_id: OrganizationID,
    backend_id: str | None,
    *,
    entity_type: AgentSessionEntity | None,
) -> Entitlement | None:
    """Return the first entitlement the organization lacks for a backend."""
    for entitlement in required_backend_entitlements(backend_id, entity_type):
        if not await is_org_entitled(session, organization_id, entitlement):
            return entitlement
    return None


async def can_use_backend(
    session: SupportsExecute,
    organization_id: OrganizationID,
    backend_id: str | None,
    *,
    entity_type: AgentSessionEntity | None,
) -> bool:
    """Whether an organization may run agent turns on a backend.

    The built-in backend is always allowed. Every other backend needs the
    ``agent_runtime_preview`` entitlement, and workflow sessions also need
    ``agent_runtime_workflows``.
    """
    missing = await missing_backend_entitlement(
        session, organization_id, backend_id, entity_type=entity_type
    )
    return missing is None


async def require_backend_entitlement(
    session: SupportsExecute,
    organization_id: OrganizationID,
    backend_id: str | None,
    *,
    entity_type: AgentSessionEntity | None,
) -> None:
    """Require that an organization may start new sessions on a backend.

    Raises:
        EntitlementRequired: If the organization lacks an entitlement the
            backend needs on this surface (403).
    """
    if missing := await missing_backend_entitlement(
        session, organization_id, backend_id, entity_type=entity_type
    ):
        raise EntitlementRequired(missing.value)


async def require_session_backend_entitlement(
    session: SupportsExecute,
    organization_id: OrganizationID,
    backend_id: str | None,
    *,
    entity_type: AgentSessionEntity | None,
) -> None:
    """Require that an existing session may start a new turn on its backend.

    Call before any turn state is written, so a refused turn leaves nothing
    behind. The session keeps its backend; the user starts a new chat, which
    gets a backend the organization may use.

    Raises:
        EntitlementRequired: If the organization lost an entitlement the
            session's backend needs (403), with a message telling the user to
            start a new chat.
    """
    if missing := await missing_backend_entitlement(
        session, organization_id, backend_id, entity_type=entity_type
    ):
        raise EntitlementRequired(
            missing.value,
            message=SESSION_BACKEND_NOT_ENTITLED_MESSAGE,
        )
