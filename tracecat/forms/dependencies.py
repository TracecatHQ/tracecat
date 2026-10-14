"""Gates for the forms routers: the feature flag, then the entitlement."""

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.auth.dependencies import (
    ExecutorWorkspaceRole,
    OrgUserRole,
    WorkspaceActorRouteRole,
)
from tracecat.auth.types import Role
from tracecat.db.dependencies import AsyncDBSession
from tracecat.feature_flags import FeatureFlag, is_feature_enabled
from tracecat.tiers.entitlements import check_entitlement
from tracecat.tiers.enums import Entitlement


async def check_forms_enabled(session: AsyncSession, role: Role) -> None:
    """Require the ``forms`` feature flag and the ``forms`` entitlement.

    Raises:
        HTTPException: 404 if the flag is off.
        EntitlementRequired: If the organization lacks the entitlement (403).
    """
    if not is_feature_enabled(FeatureFlag.FORMS):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Feature not enabled"
        )
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
