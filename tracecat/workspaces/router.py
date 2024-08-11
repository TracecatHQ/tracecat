from typing import Annotated

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    status,
)
from sqlalchemy.exc import IntegrityError, NoResultFound
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from tracecat.auth.credentials import (
    authenticate_user,
    authenticate_user_access_level,
)
from tracecat.authz.service import MembershipService
from tracecat.db.engine import get_async_session
from tracecat.db.schemas import Workflow
from tracecat.identifiers import UserID, WorkspaceID
from tracecat.logging import logger
from tracecat.types.auth import AccessLevel, Role
from tracecat.types.exceptions import TracecatAuthorizationError
from tracecat.workspaces.models import (
    CreateWorkspaceMembershipParams,
    CreateWorkspaceParams,
    UpdateWorkspaceParams,
    WorkspaceMembershipResponse,
    WorkspaceMetadataResponse,
    WorkspaceResponse,
)
from tracecat.workspaces.service import WorkspaceService

router = APIRouter(prefix="/workspaces")

# === Management === #


@router.get("", tags=["workspaces"])
async def list_workspaces(
    role: Annotated[Role, Depends(authenticate_user)],
    session: AsyncSession = Depends(get_async_session),
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
    role: Annotated[Role, Depends(authenticate_user_access_level(AccessLevel.ADMIN))],
    params: CreateWorkspaceParams,
    session: AsyncSession = Depends(get_async_session),
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
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied. User does not have the appropriate access level.",
        ) from e
    except IntegrityError as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Resource already exists"
        ) from e
    return WorkspaceMetadataResponse(
        id=workspace.id, name=workspace.name, n_members=workspace.n_members
    )


@router.get("/{workspace_id}", tags=["workspaces"])
async def get_workspace(
    role: Annotated[Role, Depends(authenticate_user)],
    workspace_id: WorkspaceID,
    session: AsyncSession = Depends(get_async_session),
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
        members=[m.id for m in workspace.members],
    )


@router.patch(
    "/{workspace_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    tags=["workspaces"],
)
async def update_workspace(
    role: Annotated[Role, Depends(authenticate_user_access_level(AccessLevel.ADMIN))],
    workspace_id: WorkspaceID,
    params: UpdateWorkspaceParams,
    session: AsyncSession = Depends(get_async_session),
) -> None:
    """Update a workspace."""
    raise NotImplementedError


@router.delete(
    "/{workspace_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    tags=["workspaces"],
)
async def delete_workspace(
    role: Annotated[Role, Depends(authenticate_user_access_level(AccessLevel.ADMIN))],
    workspace_id: WorkspaceID,
    session: AsyncSession = Depends(get_async_session),
) -> None:
    """Delete a workspace."""

    statement = select(Workflow).where(
        Workflow.owner_id == role.workspace_id,
        Workflow.id == workspace_id,
    )
    result = await session.exec(statement)
    try:
        workspace = result.one()
    except NoResultFound as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Resource not found"
        ) from e
    await session.delete(workspace)
    await session.commit()


# === Memberships === #


@router.get("/{workspace_id}/memberships", tags=["workspaces"])
async def list_workspace_memberships(
    role: Annotated[Role, Depends(authenticate_user)],
    workspace_id: WorkspaceID,
    session: AsyncSession = Depends(get_async_session),
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
    role: Annotated[
        Role,
        Depends(authenticate_user),
    ],
    workspace_id: WorkspaceID,
    params: CreateWorkspaceMembershipParams,
    session: AsyncSession = Depends(get_async_session),
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


@router.get("/{workspace_id}/memberships/{user_id}", tags=["workspaces"])
async def get_workspace_membership(
    role: Annotated[Role, Depends(authenticate_user)],
    workspace_id: WorkspaceID,
    user_id: UserID,
    session: AsyncSession = Depends(get_async_session),
) -> list[WorkspaceMembershipResponse]:
    """Get a workspace membership for a user."""
    service = MembershipService(session, role=role)
    membership = await service.get_membership(workspace_id, user_id=user_id)
    return WorkspaceMembershipResponse(
        user_id=membership.user_id, workspace_id=membership.workspace_id
    )


@router.delete(
    "/{workspace_id}/memberships/{user_id}",
    tags=["workspaces"],
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_workspace_membership(
    role: Annotated[
        Role,
        Depends(authenticate_user_access_level(AccessLevel.ADMIN)),
    ],
    workspace_id: WorkspaceID,
    user_id: UserID,
    session: AsyncSession = Depends(get_async_session),
) -> None:
    """Delete a workspace membership."""
    service = MembershipService(session, role=role)
    await service.delete_membership(workspace_id, user_id=user_id)
