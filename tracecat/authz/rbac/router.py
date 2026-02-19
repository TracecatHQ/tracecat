"""RBAC API routers - user scopes endpoint (OSS).

The RBAC management endpoints (scopes, roles, groups, assignments) are
available in the Enterprise Edition (tracecat_ee.rbac.router).
"""

from typing import Annotated

from fastapi import APIRouter
from pydantic import BaseModel, Field

from tracecat.auth.credentials import RoleACL
from tracecat.auth.types import Role

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
    role: Annotated[
        Role,
        RoleACL(
            allow_user=True,
            allow_service=False,
            require_workspace="optional",
        ),
    ],
) -> UserScopesRead:
    """Get the current user's effective scopes.

    Scopes are computed from DB-driven role assignments during auth
    (UserRoleAssignment + GroupRoleAssignment → Role → RoleScope → Scope).
    """
    current_scopes = role.scopes or frozenset()
    return UserScopesRead(scopes=sorted(current_scopes))
