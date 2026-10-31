"""Entitlement gate for agent backends other than the built-in one.

The gate applies to new sessions and to every new turn of an existing
session, so revoking the entitlement stops further turns on that backend.
A turn that already started finishes, including approval decisions for a
turn parked on approvals.
"""

from tracecat.agent.backends.registry import DEFAULT_AGENT_BACKEND
from tracecat.db.engine import SupportsExecute
from tracecat.exceptions import EntitlementRequired
from tracecat.identifiers import OrganizationID
from tracecat.tiers.access import is_org_entitled
from tracecat.tiers.enums import Entitlement

SESSION_BACKEND_NOT_ENTITLED_MESSAGE = (
    "This chat uses a feature your workspace no longer has. Start a new chat."
)


async def can_use_backend(
    session: SupportsExecute,
    organization_id: OrganizationID,
    backend_id: str | None,
) -> bool:
    """Whether an organization may run agent turns on a backend.

    The built-in backend is always allowed. Every other backend needs the
    ``agent_runtime_preview`` entitlement.
    """
    if backend_id is None or backend_id == DEFAULT_AGENT_BACKEND:
        return True
    return await is_org_entitled(
        session, organization_id, Entitlement.AGENT_RUNTIME_PREVIEW
    )


async def require_backend_entitlement(
    session: SupportsExecute,
    organization_id: OrganizationID,
    backend_id: str | None,
) -> None:
    """Require that an organization may start new sessions on a backend.

    Raises:
        EntitlementRequired: If the backend is not the built-in one and the
            organization lacks ``agent_runtime_preview`` (403).
    """
    if not await can_use_backend(session, organization_id, backend_id):
        raise EntitlementRequired(Entitlement.AGENT_RUNTIME_PREVIEW.value)


async def require_session_backend_entitlement(
    session: SupportsExecute,
    organization_id: OrganizationID,
    backend_id: str | None,
) -> None:
    """Require that an existing session may start a new turn on its backend.

    Call before any turn state is written, so a refused turn leaves nothing
    behind. The session keeps its backend; the user starts a new chat, which
    gets a backend the organization may use.

    Raises:
        EntitlementRequired: If the organization lost the entitlement for the
            session's backend (403), with a message telling the user to
            start a new chat.
    """
    if not await can_use_backend(session, organization_id, backend_id):
        raise EntitlementRequired(
            Entitlement.AGENT_RUNTIME_PREVIEW.value,
            message=SESSION_BACKEND_NOT_ENTITLED_MESSAGE,
        )
