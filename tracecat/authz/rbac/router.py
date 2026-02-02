"""RBAC API routers - user scopes endpoint (OSS).

The RBAC management endpoints (scopes, roles, groups, assignments) are
available in the Enterprise Edition (tracecat_ee.rbac.router).
"""

from fastapi import APIRouter
from pydantic import BaseModel, Field

from tracecat.auth.dependencies import OrgUserRole
from tracecat.authz.scopes import ORG_ROLE_SCOPES, PRESET_ROLE_SCOPES
from tracecat.contexts import ctx_scopes
from tracecat.db.dependencies import AsyncDBSession

# =============================================================================
# User Scopes Schemas (kept here for OSS endpoint)
# =============================================================================


class UserScopesRead(BaseModel):
    """Read schema for a user's effective scopes."""

    scopes: list[str] = Field(
        ..., description="List of effective scope strings for the user"
    )
    org_role_scopes: list[str] = Field(
        default_factory=list, description="Scopes from organization role"
    )
    workspace_role_scopes: list[str] = Field(
        default_factory=list, description="Scopes from workspace role"
    )
    group_scopes: list[str] = Field(
        default_factory=list, description="Scopes from group memberships"
    )
    user_role_scopes: list[str] = Field(
        default_factory=list, description="Scopes from direct user role assignments"
    )


# =============================================================================
# User Scopes Router (OSS - needed for ScopeProvider)
# =============================================================================

user_scopes_router = APIRouter(prefix="/users/me", tags=["users"])


@user_scopes_router.get("/scopes", response_model=UserScopesRead)
async def get_my_scopes(
    *,
    role: OrgUserRole,
    session: AsyncDBSession,
) -> UserScopesRead:
    """Get the current user's effective scopes.

    Returns a breakdown of scopes by source:
    - org_role_scopes: From org membership role (OWNER/ADMIN/MEMBER)
    - workspace_role_scopes: From workspace membership role (if in workspace context)
    - group_scopes: From group memberships and their role assignments (EE only)
    - user_role_scopes: From direct user role assignments (EE only)

    The combined `scopes` list is what's actually used for authorization.
    """
    # Get the current scopes from context (already computed during auth)
    current_scopes = ctx_scopes.get()

    # Get org role scopes
    org_role_scopes: list[str] = []
    if role.org_role is not None:
        org_role_scopes = sorted(ORG_ROLE_SCOPES.get(role.org_role, frozenset()))

    # Get workspace role scopes
    workspace_role_scopes: list[str] = []
    if role.workspace_role is not None:
        workspace_role_scopes = sorted(
            PRESET_ROLE_SCOPES.get(role.workspace_role, frozenset())
        )

    # Get group and user role scopes (EE only)
    group_scopes: list[str] = []
    user_role_scopes: list[str] = []
    if role.user_id is not None:
        try:
            from tracecat_ee.rbac.service import RBACService

            async with RBACService.with_session(role, session=session) as rbac_svc:
                group_scope_set = await rbac_svc.get_group_scopes(
                    role.user_id,
                    workspace_id=role.workspace_id,
                )
                group_scopes = sorted(group_scope_set)

                user_role_scope_set = await rbac_svc.get_user_role_scopes(
                    role.user_id,
                    workspace_id=role.workspace_id,
                )
                user_role_scopes = sorted(user_role_scope_set)
        except ImportError:
            pass  # EE not installed, use only preset role scopes

    return UserScopesRead(
        scopes=sorted(current_scopes),
        org_role_scopes=org_role_scopes,
        workspace_role_scopes=workspace_role_scopes,
        group_scopes=group_scopes,
        user_role_scopes=user_role_scopes,
    )
