"""Gate for the event source routes: the ``object_triggers`` entitlement.

Event sources feed object storage triggers only, so the whole router needs
that kind's entitlement. The trigger routes are open to every plan; the
service checks each trigger kind's own entitlement
(``ENTITLEMENT_BY_TRIGGER_KIND``).
"""

from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.auth.dependencies import WorkspaceActorRouteRole
from tracecat.auth.types import Role
from tracecat.db.dependencies import AsyncDBSession
from tracecat.tiers.entitlements import check_entitlement
from tracecat.tiers.enums import Entitlement

# The entitlement of object storage triggers and the event sources that feed
# them. Ingest checks it too.
TRIGGERS_ENTITLEMENT = Entitlement.OBJECT_TRIGGERS


async def check_event_sources_enabled(session: AsyncSession, role: Role) -> None:
    """Require the event sources entitlement.

    Raises:
        EntitlementRequired: If the organization lacks the entitlement (403).
    """
    await check_entitlement(session, role, TRIGGERS_ENTITLEMENT)


async def require_event_sources_enabled(
    role: WorkspaceActorRouteRole, session: AsyncDBSession
) -> None:
    await check_event_sources_enabled(session, role)
