from typing import Annotated

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    status,
)
from sqlalchemy.exc import IntegrityError, NoResultFound

from tracecat.auth.credentials import RoleACL
from tracecat.authz.models import WorkspaceRole
from tracecat.authz.service import MembershipService
from tracecat.db.dependencies import AsyncDBSession
from tracecat.identifiers import UserID, WorkspaceID
from tracecat.logger import logger
from tracecat.types.auth import AccessLevel, Role
from tracecat.types.exceptions import (
    TracecatAuthorizationError,
    TracecatManagementError,
)
from tracecat.workspaces.models import (
    WorkspaceCreate,
    WorkspaceMember,
    WorkspaceMembershipCreate,
    WorkspaceMembershipRead,
    WorkspaceMembershipUpdate,
    WorkspaceRead,
    WorkspaceReadMinimal,
    WorkspaceSearch,
    WorkspaceUpdate,
)
from tracecat.workspaces.service import WorkspaceService

router = APIRouter(prefix="/workspaces", tags=["workspaces"])

OrgUser = Annotated[
    Role,
    RoleACL(
        allow_user=True,
        allow_service=False,
        require_workspace="no",
    ),
]
OrgAdminUser = Annotated[
    Role,
    RoleACL(
        allow_user=True,
        allow_service=False,
        require_workspace="no",
        min_access_level=AccessLevel.ADMIN,
    ),
]
WorkspaceUserInPath = Annotated[
    Role,
    RoleACL(
        allow_user=True,
        allow_service=False,
        require_workspace="yes",
        require_workspace_roles=[WorkspaceRole.EDITOR, WorkspaceRole.ADMIN],
        workspace_id_in_path=True,
    ),
]
WorkspaceAdminUserInPath = Annotated[
    Role,
    RoleACL(
        allow_user=True,
        allow_service=False,
        require_workspace="yes",
        require_workspace_roles=WorkspaceRole.ADMIN,
        workspace_id_in_path=True,
    ),
]
# === Management === #


@router.get("")
async def list_workspaces(
    *,
    role: OrgUser,
    session: AsyncDBSession,
) -> list[WorkspaceReadMinimal]:
    """List workspaces.

    Access Level
    ------------
    - Basic: Can list workspaces where they are a member.
    - Admin: Can list all workspaces regardless of membership.
    """
    service = WorkspaceService(session, role=role)
    if role.access_level == AccessLevel.ADMIN:
        workspaces = await service.admin_list_workspaces()
    else:
        if role.user_id is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="User ID is required",
            )
        workspaces = await service.list_workspaces(role.user_id)
    return [
        WorkspaceReadMinimal(id=ws.id, name=ws.name, n_members=ws.n_members)
        for ws in workspaces
    ]


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_workspace(
    *,
    role: OrgAdminUser,
    params: WorkspaceCreate,
    session: AsyncDBSession,
) -> WorkspaceReadMinimal:
    """Create a new workspace.

    Access Level
    ------------
    - Admin: Can create a workspace for any user.
    """
    service = WorkspaceService(session, role=role)
    try:
        workspace = await service.create_workspace(
            params.name, owner_id=params.owner_id
        )
    except TracecatAuthorizationError as e:
        logger.warning(
            "User does not have the appropriate access level",
            role=role,
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden"
        ) from e
    except IntegrityError as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Resource already exists"
        ) from e
    return WorkspaceReadMinimal(
        id=workspace.id, name=workspace.name, n_members=workspace.n_members
    )


# NOTE: This route must be defined before the route for getting a single workspace for both to work
@router.get("/search")
async def search_workspaces(
    *,
    role: OrgUser,
    session: AsyncDBSession,
    params: WorkspaceSearch = Depends(),
) -> list[WorkspaceReadMinimal]:
    """Return Workflow as title, description, list of Action JSONs, adjacency list of Action IDs."""
    service = WorkspaceService(session, role=role)
    workspaces = await service.search_workspaces(params)
    return [
        WorkspaceReadMinimal(id=ws.id, name=ws.name, n_members=ws.n_members)
        for ws in workspaces
    ]


@router.get("/{workspace_id}")
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
    membership_svc = MembershipService(session, role=role)
    memberships = await membership_svc.list_memberships_with_users(workspace_id)

    return WorkspaceRead(
        id=workspace.id,
        name=workspace.name,
        settings=workspace.settings,
        owner_id=workspace.owner_id,
        n_members=workspace.n_members,
        members=[
            WorkspaceMember(
                user_id=user.id,
                first_name=user.first_name,
                last_name=user.last_name,
                email=user.email,
                org_role=user.role,
                workspace_role=membership.role,
            )
            for membership, user in memberships
        ],
    )


@router.patch(
    "/{workspace_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def update_workspace(
    *,
    role: WorkspaceAdminUserInPath,
    workspace_id: WorkspaceID,
    params: WorkspaceUpdate,
    session: AsyncDBSession,
) -> None:
    """Update a workspace."""
    service = WorkspaceService(session, role=role)
    try:
        await service.update_workspace(workspace_id, params=params)
    except NoResultFound as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Resource not found"
        ) from e


@router.delete(
    "/{workspace_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_workspace(
    *,
    role: WorkspaceAdminUserInPath,
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


@router.get("/{workspace_id}/memberships")
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
            role=membership.role,
        )
        for membership in memberships
    ]


@router.post("/{workspace_id}/memberships", status_code=status.HTTP_201_CREATED)
async def create_workspace_membership(
    *,
    role: WorkspaceAdminUserInPath,
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
            detail="User does not have the appropriate access level",
        ) from e
    except IntegrityError as e:
        logger.error("INTEGRITY ERROR")
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="User is already a member of workspace.",
        ) from e


@router.patch(
    "/{workspace_id}/memberships/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def update_workspace_membership(
    *,
    role: WorkspaceAdminUserInPath,
    workspace_id: WorkspaceID,
    user_id: UserID,
    params: WorkspaceMembershipUpdate,
    session: AsyncDBSession,
) -> None:
    """Update a workspace membership for a user."""
    service = MembershipService(session, role=role)
    membership = await service.get_membership(workspace_id, user_id=user_id)
    if not membership:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Membership not found",
        )
    try:
        await service.update_membership(membership, params=params)
    except TracecatAuthorizationError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User does not have the appropriate access level",
        ) from e
    except IntegrityError as e:
        logger.error("INTEGRITY ERROR")
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="User is already a member of workspace.",
        ) from e


@router.get("/{workspace_id}/memberships/{user_id}")
async def get_workspace_membership(
    *,
    role: WorkspaceUserInPath,
    workspace_id: WorkspaceID,
    user_id: UserID,
    session: AsyncDBSession,
) -> WorkspaceMembershipRead:
    """Get a workspace membership for a user."""
    service = MembershipService(session, role=role)
    membership = await service.get_membership(workspace_id, user_id=user_id)
    if not membership:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Membership not found",
        )
    return WorkspaceMembershipRead(
        user_id=membership.user_id,
        workspace_id=membership.workspace_id,
        role=membership.role,
    )


@router.delete(
    "/{workspace_id}/memberships/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_workspace_membership(
    *,
    role: WorkspaceAdminUserInPath,
    workspace_id: WorkspaceID,
    user_id: UserID,
    session: AsyncDBSession,
) -> None:
    """Delete a workspace membership."""
    service = MembershipService(session, role=role)
    await service.delete_membership(workspace_id, user_id=user_id)
