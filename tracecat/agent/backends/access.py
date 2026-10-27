"""Entitlement gate for starting sessions on non-default agent backends."""

from tracecat.agent.backends.registry import DEFAULT_AGENT_BACKEND
from tracecat.db.engine import SupportsExecute
from tracecat.exceptions import EntitlementRequired
from tracecat.identifiers import OrganizationID
from tracecat.tiers.access import is_org_entitled
from tracecat.tiers.enums import Entitlement


async def can_use_backend(
    session: SupportsExecute,
    organization_id: OrganizationID,
    backend_id: str | None,
) -> bool:
    """Whether an organization may start new sessions on a backend.

    The built-in backend is always allowed. Every other backend needs the
    ``agent_runtime_preview`` entitlement. Only new sessions are gated, so
    existing sessions keep running after the entitlement is revoked.
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
