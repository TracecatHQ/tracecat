"""RBAC API routers - OSS endpoints.

Read-only role listing is exposed here; write operations are in the Enterprise
Edition RBAC router (`tracecat_ee.rbac.router`).
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload
from tracecat_ee.rbac.schemas import (
    RoleList,
    RoleReadWithScopes,
    ScopeRead,
    UserRoleAssignmentCreate,
    UserRoleAssignmentList,
    UserRoleAssignmentReadWithDetails,
    UserRoleAssignmentUpdate,
)
from tracecat_ee.rbac.service import RBACService

from tracecat.auth.credentials import RoleACL
from tracecat.auth.dependencies import OrgUserRole
from tracecat.auth.types import Role
from tracecat.db.dependencies import AsyncDBSession
from tracecat.db.models import Role as DBRole
from tracecat.exceptions import TracecatNotFoundError

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


# =============================================================================
# Roles Router (Public read)
# =============================================================================

roles_router = APIRouter(prefix="/rbac/roles", tags=["rbac"])


@roles_router.get("", response_model=RoleList)
async def list_roles(
    *,
    role: OrgUserRole,
    session: AsyncDBSession,
) -> RoleList:
    """List roles for the organization.

    Requires: authenticated organization member.
    """
    if role.organization_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No organization context",
        )

    stmt = (
        select(DBRole)
        .where(DBRole.organization_id == role.organization_id)
        .options(selectinload(DBRole.scopes))
        .order_by(DBRole.name)
    )
    result = await session.execute(stmt)
    roles = result.scalars().all()

    return RoleList(
        items=[
            RoleReadWithScopes(
                id=r.id,
                name=r.name,
                slug=r.slug,
                description=r.description,
                organization_id=r.organization_id,
                created_at=r.created_at,
                updated_at=r.updated_at,
                created_by=r.created_by,
                scopes=ScopeRead.list_adapter().validate_python(r.scopes),
            )
            for r in roles
        ],
        total=len(roles),
    )


# =============================================================================
# User Role Assignments Router (Public read/write for direct user-role assignment)
# =============================================================================

user_assignments_router = APIRouter(prefix="/rbac/user-assignments", tags=["rbac"])


@user_assignments_router.get("", response_model=UserRoleAssignmentList)
async def list_user_assignments(
    *,
    role: OrgUserRole,
    session: AsyncDBSession,
    user_id: UUID | None = Query(None, description="Filter by user ID"),
    workspace_id: UUID | None = Query(None, description="Filter by workspace ID"),
) -> UserRoleAssignmentList:
    """List user role assignments for the organization."""
    service = RBACService(session, role=role)
    assignments = await service.list_user_assignments(
        user_id=user_id,
        workspace_id=workspace_id,
    )
    return UserRoleAssignmentList(
        items=[
            UserRoleAssignmentReadWithDetails(
                id=a.id,
                organization_id=a.organization_id,
                user_id=a.user_id,
                workspace_id=a.workspace_id,
                role_id=a.role_id,
                assigned_at=a.assigned_at,
                assigned_by=a.assigned_by,
                user_email=a.user.email,
                role_name=a.role.name,
                workspace_name=a.workspace.name if a.workspace else None,
            )
            for a in assignments
        ],
        total=len(assignments),
    )


@user_assignments_router.get(
    "/{assignment_id}", response_model=UserRoleAssignmentReadWithDetails
)
async def get_user_assignment(
    *,
    role: OrgUserRole,
    session: AsyncDBSession,
    assignment_id: UUID,
) -> UserRoleAssignmentReadWithDetails:
    """Get a user role assignment by ID."""
    service = RBACService(session, role=role)
    try:
        a = await service.get_user_assignment(assignment_id)
        return UserRoleAssignmentReadWithDetails(
            id=a.id,
            organization_id=a.organization_id,
            user_id=a.user_id,
            workspace_id=a.workspace_id,
            role_id=a.role_id,
            assigned_at=a.assigned_at,
            assigned_by=a.assigned_by,
            user_email=a.user.email,
            role_name=a.role.name,
            workspace_name=a.workspace.name if a.workspace else None,
        )
    except TracecatNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e


@user_assignments_router.post(
    "",
    response_model=UserRoleAssignmentReadWithDetails,
    status_code=status.HTTP_201_CREATED,
)
async def create_user_assignment(
    *,
    role: OrgUserRole,
    session: AsyncDBSession,
    params: UserRoleAssignmentCreate,
) -> UserRoleAssignmentReadWithDetails:
    """Create a user role assignment.

    Assigns a role directly to a user. If workspace_id is None, creates an org-wide
    assignment that applies to all workspaces. Each user can have at most
    one assignment per workspace (or one org-wide assignment).
    """
    service = RBACService(session, role=role)
    try:
        a = await service.create_user_assignment(
            user_id=params.user_id,
            role_id=params.role_id,
            workspace_id=params.workspace_id,
        )
        return UserRoleAssignmentReadWithDetails(
            id=a.id,
            organization_id=a.organization_id,
            user_id=a.user_id,
            workspace_id=a.workspace_id,
            role_id=a.role_id,
            assigned_at=a.assigned_at,
            assigned_by=a.assigned_by,
            user_email=a.user.email,
            role_name=a.role.name,
            workspace_name=a.workspace.name if a.workspace else None,
        )
    except TracecatNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    except IntegrityError as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="User already has an assignment for this workspace",
        ) from e


@user_assignments_router.patch(
    "/{assignment_id}", response_model=UserRoleAssignmentReadWithDetails
)
async def update_user_assignment(
    *,
    role: OrgUserRole,
    session: AsyncDBSession,
    assignment_id: UUID,
    params: UserRoleAssignmentUpdate,
) -> UserRoleAssignmentReadWithDetails:
    """Update a user role assignment (change role)."""
    service = RBACService(session, role=role)
    try:
        a = await service.update_user_assignment(assignment_id, role_id=params.role_id)
        return UserRoleAssignmentReadWithDetails(
            id=a.id,
            organization_id=a.organization_id,
            user_id=a.user_id,
            workspace_id=a.workspace_id,
            role_id=a.role_id,
            assigned_at=a.assigned_at,
            assigned_by=a.assigned_by,
            user_email=a.user.email,
            role_name=a.role.name,
            workspace_name=a.workspace.name if a.workspace else None,
        )
    except TracecatNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e


@user_assignments_router.delete(
    "/{assignment_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def delete_user_assignment(
    *,
    role: OrgUserRole,
    session: AsyncDBSession,
    assignment_id: UUID,
) -> None:
    """Delete a user role assignment."""
    service = RBACService(session, role=role)
    try:
        await service.delete_user_assignment(assignment_id)
    except TracecatNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
