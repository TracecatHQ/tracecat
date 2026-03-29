from typing import Annotated

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    status,
)
from sqlalchemy.exc import IntegrityError, NoResultFound

from tracecat.auth.credentials import RoleACL
from tracecat.auth.dependencies import OrgUserRole
from tracecat.auth.types import Role
from tracecat.authz.controls import has_scope, require_scope
from tracecat.authz.service import MembershipService
from tracecat.db.dependencies import AsyncDBSession
from tracecat.exceptions import (
    TracecatAuthorizationError,
    TracecatManagementError,
    TracecatValidationError,
)
from tracecat.identifiers import UserID, WorkspaceID
from tracecat.logger import logger
from tracecat.workspaces.schemas import (
    WorkspaceCreate,
    WorkspaceMember,
    WorkspaceMembershipBulkCreate,
    WorkspaceMembershipBulkCreateResponse,
    WorkspaceMembershipCreate,
    WorkspaceMembershipRead,
    WorkspaceRead,
    WorkspaceReadMinimal,
    WorkspaceSearch,
    WorkspaceSettingsRead,
    WorkspaceUpdate,
)
from tracecat.workspaces.service import WorkspaceService

router = APIRouter(prefix="/workspaces", tags=["workspaces"])

# Workspace role types for path-based workspace access
WorkspaceUserInPath = Annotated[
    Role,
    RoleACL(
        allow_user=True,
        allow_service=False,
        require_workspace="yes",
        workspace_id_in_path=True,
    ),
]
# === Management === #


@router.get("")
@require_scope("org:read")
async def list_workspaces(
    *,
    role: OrgUserRole,
    session: AsyncDBSession,
) -> list[WorkspaceReadMinimal]:
    """List workspaces the user has access to.

    Access
    ------
    - Org owners/admins (have `org:workspace:read` scope): See all workspaces in the org.
    - Other users: See only workspaces where they are a member.

    No scope requirement - membership itself is the authorization.
    """
    service = WorkspaceService(session, role=role)

    # Org admins/owners have org:workspace:read scope and can see all workspaces
    # NOTE: org:read is too broad — organization-member also has it
    if role.scopes and has_scope(role.scopes, "org:workspace:read"):
        workspaces = await service.admin_list_workspaces()
    else:
        if role.user_id is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="User ID is required",
            )
        workspaces = await service.list_workspaces(role.user_id)
    return [WorkspaceReadMinimal(id=ws.id, name=ws.name) for ws in workspaces]


@router.post("", status_code=status.HTTP_201_CREATED)
@require_scope("workspace:create")
async def create_workspace(
    *,
    role: OrgUserRole,
    params: WorkspaceCreate,
    session: AsyncDBSession,
) -> WorkspaceReadMinimal:
    """Create a new workspace.

    Authorization
    -------------
    - Admin: Can create a workspace for any user.
    """
    service = WorkspaceService(session, role=role)
    try:
        workspace = await service.create_workspace(params.name)
    except TracecatAuthorizationError as e:
        logger.warning(
            "User does not have the required scope",
            role=role,
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden"
        ) from e
    except IntegrityError as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Resource already exists"
        ) from e
    return WorkspaceReadMinimal(id=workspace.id, name=workspace.name)


# NOTE: This route must be defined before the route for getting a single workspace for both to work
@router.get("/search")
@require_scope("org:read")
async def search_workspaces(
    *,
    role: OrgUserRole,
    session: AsyncDBSession,
    params: WorkspaceSearch = Depends(),
) -> list[WorkspaceReadMinimal]:
    """Return Workflow as title, description, list of Action JSONs, adjacency list of Action IDs."""
    service = WorkspaceService(session, role=role)
    workspaces = await service.search_workspaces(params)
    return [WorkspaceReadMinimal(id=ws.id, name=ws.name) for ws in workspaces]


@router.get("/{workspace_id}")
@require_scope("workspace:read")
async def get_workspace(
    *,
    role: WorkspaceUserInPath,
    workspace_id: WorkspaceID,
    session: AsyncDBSession,
) -> WorkspaceRead:
    """Return Workflow as title, description, list of Action JSONs, adjacency list of Action IDs."""
    service = WorkspaceService(session, role=role)
    workspace = await service.get_workspace(workspace_id)
    if workspace is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Resource not found"
        )

    return WorkspaceRead(
        id=workspace.id,
        name=workspace.name,
        settings=WorkspaceSettingsRead.model_validate(workspace.settings or {}),
        organization_id=workspace.organization_id,
    )


@router.patch("/{workspace_id}", status_code=status.HTTP_204_NO_CONTENT)
@require_scope("workspace:update")
async def update_workspace(
    *,
    role: WorkspaceUserInPath,
    workspace_id: WorkspaceID,
    params: WorkspaceUpdate,
    session: AsyncDBSession,
) -> None:
    """Update a workspace."""
    service = WorkspaceService(session, role=role)
    workspace = await service.get_workspace(workspace_id)
    if workspace is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found"
        )
    logger.info("Updating workspace", params=params)
    await service.update_workspace(workspace, params=params)


@router.delete(
    "/{workspace_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
@require_scope("workspace:delete")
async def delete_workspace(
    *,
    role: WorkspaceUserInPath,
    workspace_id: WorkspaceID,
    session: AsyncDBSession,
) -> None:
    """Delete a workspace."""

    service = WorkspaceService(session, role=role)
    try:
        await service.delete_workspace(workspace_id)
    except NoResultFound as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Resource not found"
        ) from e
    except TracecatManagementError as e:
        raise HTTPException(
            detail=str(e),
            status_code=status.HTTP_400_BAD_REQUEST,
        ) from e


# === Memberships === #
@router.get("/{workspace_id}/members")
@require_scope("workspace:member:read")
async def list_workspace_members(
    *,
    role: WorkspaceUserInPath,
    workspace_id: WorkspaceID,
    session: AsyncDBSession,
) -> list[WorkspaceMember]:
    """List members of a workspace."""
    service = MembershipService(session, role=role)
    memberships = await service.list_workspace_members(workspace_id)
    return memberships


@router.get("/{workspace_id}/memberships")
@require_scope("workspace:member:read")
async def list_workspace_memberships(
    *,
    role: WorkspaceUserInPath,
    workspace_id: WorkspaceID,
    session: AsyncDBSession,
) -> list[WorkspaceMembershipRead]:
    """List memberships of a workspace."""
    service = MembershipService(session, role=role)
    memberships = await service.list_memberships(workspace_id)
    return [
        WorkspaceMembershipRead(
            user_id=membership.user_id,
            workspace_id=membership.workspace_id,
        )
        for membership in memberships
    ]


@router.post(
    "/{workspace_id}/memberships/bulk",
    status_code=status.HTTP_201_CREATED,
    response_model=WorkspaceMembershipBulkCreateResponse,
)
@require_scope("workspace:member:invite")
async def create_workspace_memberships_bulk(
    *,
    role: WorkspaceUserInPath,
    workspace_id: WorkspaceID,
    params: WorkspaceMembershipBulkCreate,
    session: AsyncDBSession,
) -> WorkspaceMembershipBulkCreateResponse:
    """Create or update workspace access for multiple organization users."""
    service = MembershipService(session, role=role)
    try:
        processed_count = await service.create_memberships_bulk(
            workspace_id,
            params=params,
        )
    except TracecatValidationError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)
        ) from e
    return WorkspaceMembershipBulkCreateResponse(processed_count=processed_count)


@router.post("/{workspace_id}/memberships", status_code=status.HTTP_201_CREATED)
@require_scope("workspace:member:invite")
async def create_workspace_membership(
    *,
    role: WorkspaceUserInPath,
    workspace_id: WorkspaceID,
    params: WorkspaceMembershipCreate,
    session: AsyncDBSession,
) -> None:
    """Create a workspace membership for a user."""
    logger.info(
        f"User {role.user_id} requesting to create membership for {params.user_id} in workspace {workspace_id}"
    )
    service = MembershipService(session, role=role)
    try:
        await service.create_membership(workspace_id, params=params)
    except TracecatAuthorizationError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User does not have the required scope",
        ) from e
    except IntegrityError as e:
        logger.error("INTEGRITY ERROR", error=str(e))
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="User is already a member of workspace.",
        ) from e
    except TracecatValidationError as e:
        if str(e) == "User is already a member of workspace.":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=str(e),
            ) from e
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)
        ) from e


@router.get("/{workspace_id}/memberships/{user_id}")
@require_scope("workspace:member:read")
async def get_workspace_membership(
    *,
    role: WorkspaceUserInPath,
    workspace_id: WorkspaceID,
    user_id: UserID,
    session: AsyncDBSession,
) -> WorkspaceMembershipRead:
    """Get a workspace membership for a user."""
    service = MembershipService(session, role=role)
    membership_with_org = await service.get_membership(workspace_id, user_id=user_id)
    if not membership_with_org:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Membership not found",
        )
    membership = membership_with_org.membership
    return WorkspaceMembershipRead(
        user_id=membership.user_id,
        workspace_id=membership.workspace_id,
    )


@router.delete(
    "/{workspace_id}/memberships/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
@require_scope("workspace:member:remove")
async def delete_workspace_membership(
    *,
    role: WorkspaceUserInPath,
    workspace_id: WorkspaceID,
    user_id: UserID,
    session: AsyncDBSession,
) -> None:
    """Delete a workspace membership."""
    service = MembershipService(session, role=role)
    try:
        await service.delete_membership(workspace_id, user_id=user_id)
    except TracecatValidationError as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(e),
        ) from e
