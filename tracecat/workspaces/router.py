from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    status,
)
from sqlalchemy.exc import IntegrityError, NoResultFound

from tracecat.auth.credentials import RoleACL
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
    CreateWorkspaceMembershipParams,
    CreateWorkspaceParams,
    SearchWorkspacesParams,
    UpdateWorkspaceParams,
    WorkspaceMember,
    WorkspaceMembershipResponse,
    WorkspaceMetadataResponse,
    WorkspaceResponse,
)
from tracecat.workspaces.service import WorkspaceService

router = APIRouter(prefix="/workspaces")

# === Management === #


@router.get("", tags=["workspaces"])
async def list_workspaces(
    *,
    role: Role = RoleACL(
        allow_user=True,
        allow_service=False,
        require_workspace=False,
    ),
    session: AsyncDBSession,
) -> list[WorkspaceMetadataResponse]:
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
        workspaces = await service.list_workspaces(role.user_id)
    return [
        WorkspaceMetadataResponse(id=ws.id, name=ws.name, n_members=ws.n_members)
        for ws in workspaces
    ]


@router.post("", status_code=status.HTTP_201_CREATED, tags=["workspaces"])
async def create_workspace(
    *,
    role: Role = RoleACL(
        allow_user=True,
        allow_service=False,
        require_workspace=False,
        min_access_level=AccessLevel.ADMIN,
    ),
    params: CreateWorkspaceParams,
    session: AsyncDBSession,
) -> WorkspaceMetadataResponse:
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
    return WorkspaceMetadataResponse(
        id=workspace.id, name=workspace.name, n_members=workspace.n_members
    )


# NOTE: This route must be defined before the route for getting a single workspace for both to work
@router.get("/search", tags=["workspaces"])
async def search_workspaces(
    *,
    role: Role = RoleACL(
        allow_user=True,
        allow_service=False,
        require_workspace=False,
    ),
    session: AsyncDBSession,
    params: SearchWorkspacesParams = Depends(),
) -> list[WorkspaceMetadataResponse]:
    """Return Workflow as title, description, list of Action JSONs, adjacency list of Action IDs."""
    service = WorkspaceService(session, role=role)
    workspaces = await service.search_workspaces(params)
    return [
        WorkspaceMetadataResponse(id=ws.id, name=ws.name, n_members=ws.n_members)
        for ws in workspaces
    ]


@router.get("/{workspace_id}", tags=["workspaces"])
async def get_workspace(
    *,
    role: Role = RoleACL(
        allow_user=True,
        allow_service=False,
        require_workspace=True,
        workspace_id_in_path=True,
    ),
    workspace_id: WorkspaceID,
    session: AsyncDBSession,
) -> WorkspaceResponse:
    """Return Workflow as title, description, list of Action JSONs, adjacency list of Action IDs."""
    service = WorkspaceService(session, role=role)
    workspace = await service.get_workspace(workspace_id)
    if workspace is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Resource not found"
        )
    return WorkspaceResponse(
        id=workspace.id,
        name=workspace.name,
        settings=workspace.settings,
        owner_id=workspace.owner_id,
        n_members=workspace.n_members,
        members=[
            WorkspaceMember(
                user_id=member.id,
                first_name=member.first_name,
                last_name=member.last_name,
                email=member.email,
                role=member.role,
            )
            for member in workspace.members
        ],
    )


@router.patch(
    "/{workspace_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    tags=["workspaces"],
)
async def update_workspace(
    *,
    role: Role = RoleACL(
        allow_user=True,
        allow_service=False,
        min_access_level=AccessLevel.ADMIN,
        require_workspace=True,
        workspace_id_in_path=True,
    ),
    workspace_id: WorkspaceID,
    params: UpdateWorkspaceParams,
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
    tags=["workspaces"],
)
async def delete_workspace(
    *,
    role: Role = RoleACL(
        allow_user=True,
        allow_service=False,
        min_access_level=AccessLevel.ADMIN,
        require_workspace=True,
        workspace_id_in_path=True,
    ),
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


@router.get("/{workspace_id}/memberships", tags=["workspaces"])
async def list_workspace_memberships(
    *,
    role: Role = RoleACL(
        allow_user=True,
        allow_service=False,
        require_workspace=True,
        workspace_id_in_path=True,
    ),
    workspace_id: WorkspaceID,
    session: AsyncDBSession,
) -> list[WorkspaceMembershipResponse]:
    """List memberships of a workspace."""
    service = MembershipService(session, role=role)
    memberships = await service.list_memberships(workspace_id)
    return [
        WorkspaceMembershipResponse(
            user_id=membership.user_id, workspace_id=membership.workspace_id
        )
        for membership in memberships
    ]


@router.post(
    "/{workspace_id}/memberships",
    tags=["workspaces"],
    status_code=status.HTTP_201_CREATED,
)
async def create_workspace_membership(
    *,
    role: Role = RoleACL(
        allow_user=True,
        allow_service=False,
        min_access_level=AccessLevel.ADMIN,
        require_workspace=True,
        workspace_id_in_path=True,
    ),
    workspace_id: WorkspaceID,
    params: CreateWorkspaceMembershipParams,
    session: AsyncDBSession,
) -> None:
    """Create a workspace membership for a user."""
    logger.info(
        f"User {role.user_id} requesting to create membership for {params.user_id} in workspace {workspace_id}"
    )
    service = MembershipService(session, role=role)
    try:
        await service.create_membership(workspace_id, user_id=params.user_id)
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


@router.get("/{workspace_id}/memberships/{user_id}", tags=["workspaces"])
async def get_workspace_membership(
    *,
    role: Role = RoleACL(
        allow_user=True,
        allow_service=False,
        require_workspace=True,
        workspace_id_in_path=True,
    ),
    workspace_id: WorkspaceID,
    user_id: UserID,
    session: AsyncDBSession,
) -> list[WorkspaceMembershipResponse]:
    """Get a workspace membership for a user."""
    service = MembershipService(session, role=role)
    membership = await service.get_membership(workspace_id, user_id=user_id)
    if not membership:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Membership not found",
        )
    return WorkspaceMembershipResponse(
        user_id=membership.user_id, workspace_id=membership.workspace_id
    )


@router.delete(
    "/{workspace_id}/memberships/{user_id}",
    tags=["workspaces"],
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_workspace_membership(
    *,
    role: Role = RoleACL(
        allow_user=True,
        allow_service=False,
        min_access_level=AccessLevel.ADMIN,
        require_workspace=True,
        workspace_id_in_path=True,
    ),
    workspace_id: WorkspaceID,
    user_id: UserID,
    session: AsyncDBSession,
) -> None:
    """Delete a workspace membership."""
    service = MembershipService(session, role=role)
    await service.delete_membership(workspace_id, user_id=user_id)
