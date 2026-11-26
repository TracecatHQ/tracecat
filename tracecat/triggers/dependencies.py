"""Gate for the trigger routers: the ``object_triggers`` entitlement."""

from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.auth.dependencies import WorkspaceActorRouteRole
from tracecat.auth.types import Role
from tracecat.db.dependencies import AsyncDBSession
from tracecat.tiers.entitlements import check_entitlement
from tracecat.tiers.enums import Entitlement

# The one place that names the entitlement for triggers.
TRIGGERS_ENTITLEMENT = Entitlement.OBJECT_TRIGGERS


async def check_triggers_enabled(session: AsyncSession, role: Role) -> None:
    """Require the triggers entitlement.

    Raises:
        EntitlementRequired: If the organization lacks the entitlement (403).
    """
    await check_entitlement(session, role, TRIGGERS_ENTITLEMENT)


async def require_triggers_enabled(
    role: WorkspaceActorRouteRole, session: AsyncDBSession
) -> None:
    await check_triggers_enabled(session, role)
