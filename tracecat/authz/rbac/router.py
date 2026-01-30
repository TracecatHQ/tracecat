"""RBAC API routers - user scopes endpoint (OSS).

The RBAC management endpoints (scopes, roles, groups, assignments) are
available in the Enterprise Edition (tracecat_ee.rbac.router).
"""

import uuid

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from tracecat.auth.dependencies import OrgUserRole

# =============================================================================
# User Scopes Schemas (kept here for OSS endpoint)
# =============================================================================


class UserScopesRead(BaseModel):
    """Read schema for a user's effective scopes."""

    scopes: list[str] = Field(
        ..., description="List of effective scope strings for the user"
    )


# =============================================================================
# User Scopes Router (OSS - needed for ScopeProvider)
# =============================================================================

user_scopes_router = APIRouter(prefix="/users/me", tags=["users"])


@user_scopes_router.get("/scopes", response_model=UserScopesRead)
async def get_my_scopes(
    *,
    role: OrgUserRole,
) -> UserScopesRead:
    """Get the current user's effective scopes.

    Scopes are computed from DB-driven role assignments during auth
    (UserRoleAssignment + GroupRoleAssignment → Role → RoleScope → Scope).
    """
    current_scopes = role.scopes or frozenset()
    return UserScopesRead(scopes=sorted(current_scopes))
