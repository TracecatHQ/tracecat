"""Gates for the forms routers: the ``forms`` entitlement."""

from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.auth.dependencies import (
    ExecutorWorkspaceRole,
    OrgUserRole,
    WorkspaceActorRouteRole,
)
from tracecat.auth.types import Role
from tracecat.db.dependencies import AsyncDBSession
from tracecat.tiers.entitlements import check_entitlement
from tracecat.tiers.enums import Entitlement


async def check_forms_enabled(session: AsyncSession, role: Role) -> None:
    """Require the ``forms`` entitlement.

    Raises:
        EntitlementRequired: If the organization lacks the entitlement (403).
    """
    await check_entitlement(session, role, Entitlement.FORMS)


async def require_forms_enabled(
    role: WorkspaceActorRouteRole, session: AsyncDBSession
) -> None:
    await check_forms_enabled(session, role)


async def require_forms_enabled_for_org(
    role: OrgUserRole, session: AsyncDBSession
) -> None:
    await check_forms_enabled(session, role)


async def require_forms_enabled_for_executor(
    role: ExecutorWorkspaceRole, session: AsyncDBSession
) -> None:
    await check_forms_enabled(session, role)
