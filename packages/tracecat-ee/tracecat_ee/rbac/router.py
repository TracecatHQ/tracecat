"""RBAC API routers for scopes, roles, groups, and assignments (EE)."""

from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy.exc import IntegrityError

from tracecat.auth.dependencies import OrgUserRole
from tracecat.authz.controls import require_scope
from tracecat.authz.enums import ScopeSource
from tracecat.db.dependencies import AsyncDBSession
from tracecat.exceptions import (
    TracecatAuthorizationError,
    TracecatNotFoundError,
    TracecatValidationError,
)
from tracecat_ee.rbac.schemas import (
    GroupCreate,
    GroupList,
    GroupMemberAdd,
    GroupMemberRead,
    GroupReadWithMembers,
    GroupRoleAssignmentCreate,
    GroupRoleAssignmentList,
    GroupRoleAssignmentReadWithDetails,
    GroupRoleAssignmentUpdate,
    GroupUpdate,
    RoleCreate,
    RoleList,
    RoleReadWithScopes,
    RoleUpdate,
    ScopeCreate,
    ScopeList,
    ScopeRead,
    UserRoleAssignmentCreate,
    UserRoleAssignmentList,
    UserRoleAssignmentReadWithDetails,
    UserRoleAssignmentUpdate,
)
from tracecat_ee.rbac.service import RBACService

# =============================================================================
# Scopes Router
# =============================================================================

scopes_router = APIRouter(prefix="/rbac/scopes", tags=["rbac"])


@scopes_router.get("", response_model=ScopeList)
@require_scope("org:rbac:read")
async def list_scopes(
    *,
    role: OrgUserRole,
    session: AsyncDBSession,
    include_system: bool = Query(True, description="Include system/registry scopes"),
    source: ScopeSource | None = Query(None, description="Filter by scope source"),
) -> ScopeList:
    """List scopes available to the organization.

    Requires: org:rbac:read scope
    """
    service = RBACService(session, role=role)
    scopes = await service.list_scopes(include_system=include_system, source=source)
    return ScopeList(
        items=ScopeRead.list_adapter().validate_python(scopes),
        total=len(scopes),
    )


@scopes_router.get("/{scope_id}", response_model=ScopeRead)
@require_scope("org:rbac:read")
async def get_scope(
    *,
    role: OrgUserRole,
    session: AsyncDBSession,
    scope_id: UUID,
) -> ScopeRead:
    """Get a scope by ID.

    Requires: org:rbac:read scope
    """
    service = RBACService(session, role=role)
    try:
        scope = await service.get_scope(scope_id)
        return ScopeRead.model_validate(scope)
    except TracecatNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e


@scopes_router.post("", response_model=ScopeRead, status_code=status.HTTP_201_CREATED)
@require_scope("org:rbac:create")
async def create_scope(
    *,
    role: OrgUserRole,
    session: AsyncDBSession,
    params: ScopeCreate,
) -> ScopeRead:
    """Create a custom scope.

    Requires: one of org:rbac:create, org:rbac:update, or org:rbac:delete scopes
    """
    service = RBACService(session, role=role)
    try:
        scope = await service.create_scope(
            name=params.name,
            description=params.description,
        )
        return ScopeRead.model_validate(scope)
    except TracecatValidationError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)
        ) from e
    except IntegrityError as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A scope with this name already exists",
        ) from e


@scopes_router.delete("/{scope_id}", status_code=status.HTTP_204_NO_CONTENT)
@require_scope("org:rbac:delete")
async def delete_scope(
    *,
    role: OrgUserRole,
    session: AsyncDBSession,
    scope_id: UUID,
) -> None:
    """Delete a custom scope.

    Only custom scopes can be deleted. System and registry scopes are protected.

    Requires: one of org:rbac:create, org:rbac:update, or org:rbac:delete scopes
    """
    service = RBACService(session, role=role)
    try:
        await service.delete_scope(scope_id)
    except TracecatNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    except TracecatAuthorizationError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e)) from e


# =============================================================================
# Roles Router
# =============================================================================

roles_router = APIRouter(prefix="/rbac/roles", tags=["rbac"])


@roles_router.get("", response_model=RoleList)
@require_scope("org:rbac:read")
async def list_roles(
    *,
    role: OrgUserRole,
    session: AsyncDBSession,
) -> RoleList:
    """List roles for the organization.

    Requires: org:rbac:read scope
    """
    service = RBACService(session, role=role)
    roles = await service.list_roles()
    return RoleList(
        items=[
            RoleReadWithScopes(
                id=r.id,
                name=r.name,
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


@roles_router.get("/{role_id}", response_model=RoleReadWithScopes)
@require_scope("org:rbac:read")
async def get_role(
    *,
    role: OrgUserRole,
    session: AsyncDBSession,
    role_id: UUID,
) -> RoleReadWithScopes:
    """Get a role by ID with its scopes.

    Requires: org:rbac:read scope
    """
    service = RBACService(session, role=role)
    try:
        db_role = await service.get_role(role_id)
        return RoleReadWithScopes(
            id=db_role.id,
            name=db_role.name,
            description=db_role.description,
            organization_id=db_role.organization_id,
            created_at=db_role.created_at,
            updated_at=db_role.updated_at,
            created_by=db_role.created_by,
            scopes=ScopeRead.list_adapter().validate_python(db_role.scopes),
        )
    except TracecatNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e


@roles_router.post(
    "", response_model=RoleReadWithScopes, status_code=status.HTTP_201_CREATED
)
@require_scope("org:rbac:create")
async def create_role(
    *,
    role: OrgUserRole,
    session: AsyncDBSession,
    params: RoleCreate,
) -> RoleReadWithScopes:
    """Create a custom role.

    Requires: one of org:rbac:create, org:rbac:update, or org:rbac:delete scopes
    """
    service = RBACService(session, role=role)
    try:
        db_role = await service.create_role(
            name=params.name,
            description=params.description,
            scope_ids=params.scope_ids,
        )
        return RoleReadWithScopes(
            id=db_role.id,
            name=db_role.name,
            description=db_role.description,
            organization_id=db_role.organization_id,
            created_at=db_role.created_at,
            updated_at=db_role.updated_at,
            created_by=db_role.created_by,
            scopes=ScopeRead.list_adapter().validate_python(db_role.scopes),
        )
    except TracecatNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    except TracecatAuthorizationError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e)) from e
    except IntegrityError as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A role with this name already exists",
        ) from e


@roles_router.patch("/{role_id}", response_model=RoleReadWithScopes)
@require_scope("org:rbac:update")
async def update_role(
    *,
    role: OrgUserRole,
    session: AsyncDBSession,
    role_id: UUID,
    params: RoleUpdate,
) -> RoleReadWithScopes:
    """Update a role.

    Requires: one of org:rbac:create, org:rbac:update, or org:rbac:delete scopes
    """
    service = RBACService(session, role=role)
    try:
        db_role = await service.update_role(
            role_id,
            name=params.name,
            description=params.description,
            scope_ids=params.scope_ids,
        )
        return RoleReadWithScopes(
            id=db_role.id,
            name=db_role.name,
            description=db_role.description,
            organization_id=db_role.organization_id,
            created_at=db_role.created_at,
            updated_at=db_role.updated_at,
            created_by=db_role.created_by,
            scopes=ScopeRead.list_adapter().validate_python(db_role.scopes),
        )
    except TracecatNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    except TracecatAuthorizationError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e)) from e
    except IntegrityError as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A role with this name already exists",
        ) from e


@roles_router.delete("/{role_id}", status_code=status.HTTP_204_NO_CONTENT)
@require_scope("org:rbac:delete")
async def delete_role(
    *,
    role: OrgUserRole,
    session: AsyncDBSession,
    role_id: UUID,
) -> None:
    """Delete a role.

    Roles with active assignments must have all assignments removed first.

    Requires: one of org:rbac:create, org:rbac:update, or org:rbac:delete scopes
    """
    service = RBACService(session, role=role)
    try:
        await service.delete_role(role_id)
    except TracecatNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    except TracecatAuthorizationError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e)) from e
    except TracecatValidationError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)
        ) from e


# =============================================================================
# Groups Router
# =============================================================================

groups_router = APIRouter(prefix="/rbac/groups", tags=["rbac"])


@groups_router.get("", response_model=GroupList)
@require_scope("org:rbac:read")
async def list_groups(
    *,
    role: OrgUserRole,
    session: AsyncDBSession,
) -> GroupList:
    """List groups for the organization.

    Requires: org:rbac:read scope
    """
    service = RBACService(session, role=role)
    groups = await service.list_groups()
    return GroupList(
        items=[
            GroupReadWithMembers(
                id=g.id,
                name=g.name,
                description=g.description,
                organization_id=g.organization_id,
                created_at=g.created_at,
                updated_at=g.updated_at,
                created_by=g.created_by,
                members=[],  # Don't include full member list in list view
                member_count=len(g.members),
            )
            for g in groups
        ],
        total=len(groups),
    )


@groups_router.get("/{group_id}", response_model=GroupReadWithMembers)
@require_scope("org:rbac:read")
async def get_group(
    *,
    role: OrgUserRole,
    session: AsyncDBSession,
    group_id: UUID,
) -> GroupReadWithMembers:
    """Get a group by ID with its members.

    Requires: org:rbac:read scope
    """
    service = RBACService(session, role=role)
    try:
        group = await service.get_group(group_id)
        members = await service.list_group_members(group_id)
        return GroupReadWithMembers(
            id=group.id,
            name=group.name,
            description=group.description,
            organization_id=group.organization_id,
            created_at=group.created_at,
            updated_at=group.updated_at,
            created_by=group.created_by,
            members=[
                GroupMemberRead(
                    user_id=user.id,
                    email=user.email,
                    first_name=user.first_name,
                    last_name=user.last_name,
                    added_at=gm.added_at,
                )
                for user, gm in members
            ],
            member_count=len(members),
        )
    except TracecatNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e


@groups_router.post(
    "", response_model=GroupReadWithMembers, status_code=status.HTTP_201_CREATED
)
@require_scope("org:rbac:create")
async def create_group(
    *,
    role: OrgUserRole,
    session: AsyncDBSession,
    params: GroupCreate,
) -> GroupReadWithMembers:
    """Create a group.

    Requires: one of org:rbac:create, org:rbac:update, or org:rbac:delete scopes
    """
    service = RBACService(session, role=role)
    try:
        group = await service.create_group(
            name=params.name,
            description=params.description,
        )
        return GroupReadWithMembers(
            id=group.id,
            name=group.name,
            description=group.description,
            organization_id=group.organization_id,
            created_at=group.created_at,
            updated_at=group.updated_at,
            created_by=group.created_by,
            members=[],
            member_count=0,
        )
    except IntegrityError as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A group with this name already exists",
        ) from e


@groups_router.patch("/{group_id}", response_model=GroupReadWithMembers)
@require_scope("org:rbac:update")
async def update_group(
    *,
    role: OrgUserRole,
    session: AsyncDBSession,
    group_id: UUID,
    params: GroupUpdate,
) -> GroupReadWithMembers:
    """Update a group.

    Requires: one of org:rbac:create, org:rbac:update, or org:rbac:delete scopes
    """
    service = RBACService(session, role=role)
    try:
        group = await service.update_group(
            group_id,
            name=params.name,
            description=params.description,
        )
        members = await service.list_group_members(group_id)
        return GroupReadWithMembers(
            id=group.id,
            name=group.name,
            description=group.description,
            organization_id=group.organization_id,
            created_at=group.created_at,
            updated_at=group.updated_at,
            created_by=group.created_by,
            members=[
                GroupMemberRead(
                    user_id=user.id,
                    email=user.email,
                    first_name=user.first_name,
                    last_name=user.last_name,
                    added_at=gm.added_at,
                )
                for user, gm in members
            ],
            member_count=len(members),
        )
    except TracecatNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    except IntegrityError as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A group with this name already exists",
        ) from e


@groups_router.delete("/{group_id}", status_code=status.HTTP_204_NO_CONTENT)
@require_scope("org:rbac:delete")
async def delete_group(
    *,
    role: OrgUserRole,
    session: AsyncDBSession,
    group_id: UUID,
) -> None:
    """Delete a group.

    This will also delete all group assignments and memberships.

    Requires: one of org:rbac:create, org:rbac:update, or org:rbac:delete scopes
    """
    service = RBACService(session, role=role)
    try:
        await service.delete_group(group_id)
    except TracecatNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e


# =============================================================================
# Group Members Router
# =============================================================================


@groups_router.post(
    "/{group_id}/members",
    status_code=status.HTTP_201_CREATED,
)
@require_scope("org:rbac:create")
async def add_group_member(
    *,
    role: OrgUserRole,
    session: AsyncDBSession,
    group_id: UUID,
    params: GroupMemberAdd,
) -> dict[str, str]:
    """Add a member to a group.

    Requires: one of org:rbac:create, org:rbac:update, or org:rbac:delete scopes
    """
    service = RBACService(session, role=role)
    try:
        await service.add_group_member(group_id, params.user_id)
        return {"message": "Member added successfully"}
    except TracecatNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    except TracecatValidationError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)
        ) from e
    except IntegrityError as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="User is already a member of this group",
        ) from e


@groups_router.delete(
    "/{group_id}/members/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
@require_scope("org:rbac:delete")
async def remove_group_member(
    *,
    role: OrgUserRole,
    session: AsyncDBSession,
    group_id: UUID,
    user_id: UUID,
) -> None:
    """Remove a member from a group.

    Requires: one of org:rbac:create, org:rbac:update, or org:rbac:delete scopes
    """
    service = RBACService(session, role=role)
    try:
        await service.remove_group_member(group_id, user_id)
    except TracecatNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e


# =============================================================================
# Group Assignments Router
# =============================================================================

assignments_router = APIRouter(prefix="/rbac/assignments", tags=["rbac"])


@assignments_router.get("", response_model=GroupRoleAssignmentList)
@require_scope("org:rbac:read")
async def list_assignments(
    *,
    role: OrgUserRole,
    session: AsyncDBSession,
    group_id: UUID | None = Query(None, description="Filter by group ID"),
    workspace_id: UUID | None = Query(None, description="Filter by workspace ID"),
) -> GroupRoleAssignmentList:
    """List group assignments for the organization.

    Requires: org:rbac:read scope
    """
    service = RBACService(session, role=role)
    assignments = await service.list_group_role_assignments(
        group_id=group_id,
        workspace_id=workspace_id,
    )
    return GroupRoleAssignmentList(
        items=[
            GroupRoleAssignmentReadWithDetails(
                id=a.id,
                organization_id=a.organization_id,
                group_id=a.group_id,
                workspace_id=a.workspace_id,
                role_id=a.role_id,
                assigned_at=a.assigned_at,
                assigned_by=a.assigned_by,
                group_name=a.group.name,
                role_name=a.role.name,
                workspace_name=a.workspace.name if a.workspace else None,
            )
            for a in assignments
        ],
        total=len(assignments),
    )


@assignments_router.get(
    "/{assignment_id}", response_model=GroupRoleAssignmentReadWithDetails
)
@require_scope("org:rbac:read")
async def get_assignment(
    *,
    role: OrgUserRole,
    session: AsyncDBSession,
    assignment_id: UUID,
) -> GroupRoleAssignmentReadWithDetails:
    """Get a group assignment by ID.

    Requires: org:rbac:read scope
    """
    service = RBACService(session, role=role)
    try:
        a = await service.get_group_role_assignment(assignment_id)
        return GroupRoleAssignmentReadWithDetails(
            id=a.id,
            organization_id=a.organization_id,
            group_id=a.group_id,
            workspace_id=a.workspace_id,
            role_id=a.role_id,
            assigned_at=a.assigned_at,
            assigned_by=a.assigned_by,
            group_name=a.group.name,
            role_name=a.role.name,
            workspace_name=a.workspace.name if a.workspace else None,
        )
    except TracecatNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e


@assignments_router.post(
    "",
    response_model=GroupRoleAssignmentReadWithDetails,
    status_code=status.HTTP_201_CREATED,
)
@require_scope("org:rbac:create")
async def create_assignment(
    *,
    role: OrgUserRole,
    session: AsyncDBSession,
    params: GroupRoleAssignmentCreate,
) -> GroupRoleAssignmentReadWithDetails:
    """Create a group assignment.

    Assigns a role to a group. If workspace_id is None, creates an org-wide
    assignment that applies to all workspaces. Each group can have at most
    one assignment per workspace (or one org-wide assignment).

    Requires: one of org:rbac:create, org:rbac:update, or org:rbac:delete scopes
    """
    service = RBACService(session, role=role)
    try:
        a = await service.create_group_role_assignment(
            group_id=params.group_id,
            role_id=params.role_id,
            workspace_id=params.workspace_id,
        )
        return GroupRoleAssignmentReadWithDetails(
            id=a.id,
            organization_id=a.organization_id,
            group_id=a.group_id,
            workspace_id=a.workspace_id,
            role_id=a.role_id,
            assigned_at=a.assigned_at,
            assigned_by=a.assigned_by,
            group_name=a.group.name,
            role_name=a.role.name,
            workspace_name=a.workspace.name if a.workspace else None,
        )
    except TracecatNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    except IntegrityError as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Group already has an assignment for this workspace",
        ) from e


@assignments_router.patch(
    "/{assignment_id}", response_model=GroupRoleAssignmentReadWithDetails
)
@require_scope("org:rbac:update")
async def update_assignment(
    *,
    role: OrgUserRole,
    session: AsyncDBSession,
    assignment_id: UUID,
    params: GroupRoleAssignmentUpdate,
) -> GroupRoleAssignmentReadWithDetails:
    """Update a group assignment (change role).

    Requires: one of org:rbac:create, org:rbac:update, or org:rbac:delete scopes
    """
    service = RBACService(session, role=role)
    try:
        a = await service.update_group_role_assignment(
            assignment_id, role_id=params.role_id
        )
        return GroupRoleAssignmentReadWithDetails(
            id=a.id,
            organization_id=a.organization_id,
            group_id=a.group_id,
            workspace_id=a.workspace_id,
            role_id=a.role_id,
            assigned_at=a.assigned_at,
            assigned_by=a.assigned_by,
            group_name=a.group.name,
            role_name=a.role.name,
            workspace_name=a.workspace.name if a.workspace else None,
        )
    except TracecatNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e


@assignments_router.delete("/{assignment_id}", status_code=status.HTTP_204_NO_CONTENT)
@require_scope("org:rbac:delete")
async def delete_assignment(
    *,
    role: OrgUserRole,
    session: AsyncDBSession,
    assignment_id: UUID,
) -> None:
    """Delete a group assignment.

    Requires: one of org:rbac:create, org:rbac:update, or org:rbac:delete scopes
    """
    service = RBACService(session, role=role)
    try:
        await service.delete_group_role_assignment(assignment_id)
    except TracecatNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e


# =============================================================================
# User Role Assignments Router
# =============================================================================

user_assignments_router = APIRouter(prefix="/rbac/user-assignments", tags=["rbac"])


@user_assignments_router.get("", response_model=UserRoleAssignmentList)
@require_scope("org:rbac:read")
async def list_user_assignments(
    *,
    role: OrgUserRole,
    session: AsyncDBSession,
    user_id: UUID | None = Query(None, description="Filter by user ID"),
    workspace_id: UUID | None = Query(None, description="Filter by workspace ID"),
) -> UserRoleAssignmentList:
    """List user role assignments for the organization.

    Requires: org:rbac:read scope
    """
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
@require_scope("org:rbac:read")
async def get_user_assignment(
    *,
    role: OrgUserRole,
    session: AsyncDBSession,
    assignment_id: UUID,
) -> UserRoleAssignmentReadWithDetails:
    """Get a user role assignment by ID.

    Requires: org:rbac:read scope
    """
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
@require_scope("org:rbac:create")
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

    Requires: one of org:rbac:create, org:rbac:update, or org:rbac:delete scopes
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
@require_scope("org:rbac:update")
async def update_user_assignment(
    *,
    role: OrgUserRole,
    session: AsyncDBSession,
    assignment_id: UUID,
    params: UserRoleAssignmentUpdate,
) -> UserRoleAssignmentReadWithDetails:
    """Update a user role assignment (change role).

    Requires: one of org:rbac:create, org:rbac:update, or org:rbac:delete scopes
    """
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
@require_scope("org:rbac:delete")
async def delete_user_assignment(
    *,
    role: OrgUserRole,
    session: AsyncDBSession,
    assignment_id: UUID,
) -> None:
    """Delete a user role assignment.

    Requires: one of org:rbac:create, org:rbac:update, or org:rbac:delete scopes
    """
    service = RBACService(session, role=role)
    try:
        await service.delete_user_assignment(assignment_id)
    except TracecatNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
