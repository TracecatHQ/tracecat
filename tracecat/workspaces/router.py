from typing import Annotated

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    status,
)
from sqlalchemy.exc import IntegrityError, NoResultFound

from tracecat.auth.credentials import RoleACL
from tracecat.auth.types import AccessLevel, Role
from tracecat.authz.enums import WorkspaceRole
from tracecat.authz.service import MembershipService
from tracecat.db.dependencies import AsyncDBSession
from tracecat.exceptions import (
    TracecatAuthorizationError,
    TracecatManagementError,
    TracecatNotFoundError,
    TracecatValidationError,
)
from tracecat.identifiers import InvitationID, UserID, WorkspaceID
from tracecat.logger import logger
from tracecat.workspaces.schemas import (
    WorkspaceCreate,
    WorkspaceInvitationCreate,
    WorkspaceInvitationList,
    WorkspaceInvitationRead,
    WorkspaceMember,
    WorkspaceMembershipCreate,
    WorkspaceMembershipRead,
    WorkspaceMembershipUpdate,
    WorkspaceRead,
    WorkspaceReadMinimal,
    WorkspaceSearch,
    WorkspaceSettingsRead,
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
    return [WorkspaceReadMinimal(id=ws.id, name=ws.name) for ws in workspaces]


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
        workspace = await service.create_workspace(params.name)
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
    return WorkspaceReadMinimal(id=workspace.id, name=workspace.name)


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
    return [WorkspaceReadMinimal(id=ws.id, name=ws.name) for ws in workspaces]


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

    return WorkspaceRead(
        id=workspace.id,
        name=workspace.name,
        settings=WorkspaceSettingsRead.model_validate(workspace.settings),
        organization_id=workspace.organization_id,
    )


@router.patch("/{workspace_id}", status_code=status.HTTP_204_NO_CONTENT)
async def update_workspace(
    *,
    role: WorkspaceAdminUserInPath,
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
@router.get("/{workspace_id}/members")
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
    membership_with_org = await service.get_membership(workspace_id, user_id=user_id)
    if not membership_with_org:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Membership not found",
        )
    try:
        await service.update_membership(membership_with_org.membership, params=params)
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


# === Invitations === #


@router.post("/{workspace_id}/invitations", status_code=status.HTTP_201_CREATED)
async def create_workspace_invitation(
    *,
    role: WorkspaceAdminUserInPath,
    workspace_id: WorkspaceID,
    params: WorkspaceInvitationCreate,
    session: AsyncDBSession,
) -> WorkspaceInvitationRead:
    """Create a workspace invitation.

    Access Level
    ------------
    - Workspace Admin: Can create invitations for their workspace.
    """
    service = WorkspaceService(session, role=role)
    try:
        invitation = await service.create_invitation(workspace_id, params)
    except TracecatAuthorizationError as e:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User does not have permission to create invitations",
        ) from e
    except TracecatValidationError as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(e),
        ) from e
    return WorkspaceInvitationRead(
        id=invitation.id,
        workspace_id=invitation.workspace_id,
        email=invitation.email,
        role=invitation.role,
        status=invitation.status,
        invited_by=invitation.invited_by,
        token=invitation.token,
        expires_at=invitation.expires_at,
        accepted_at=invitation.accepted_at,
        created_at=invitation.created_at,
    )


@router.get("/{workspace_id}/invitations")
async def list_workspace_invitations(
    *,
    role: WorkspaceAdminUserInPath,
    workspace_id: WorkspaceID,
    session: AsyncDBSession,
    params: WorkspaceInvitationList = Depends(),
) -> list[WorkspaceInvitationRead]:
    """List workspace invitations.

    Access Level
    ------------
    - Workspace Admin: Can list invitations for their workspace.
    """
    service = WorkspaceService(session, role=role)
    try:
        invitations = await service.list_invitations(workspace_id, status=params.status)
    except TracecatAuthorizationError as e:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User does not have permission to list invitations",
        ) from e
    return [
        WorkspaceInvitationRead(
            id=inv.id,
            workspace_id=inv.workspace_id,
            email=inv.email,
            role=inv.role,
            status=inv.status,
            invited_by=inv.invited_by,
            token=inv.token,
            expires_at=inv.expires_at,
            accepted_at=inv.accepted_at,
            created_at=inv.created_at,
        )
        for inv in invitations
    ]


@router.delete(
    "/{workspace_id}/invitations/{invitation_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def revoke_workspace_invitation(
    *,
    role: WorkspaceAdminUserInPath,
    workspace_id: WorkspaceID,
    invitation_id: InvitationID,
    session: AsyncDBSession,
) -> None:
    """Revoke a workspace invitation.

    Access Level
    ------------
    - Workspace Admin: Can revoke invitations for their workspace.
    """
    service = WorkspaceService(session, role=role)
    try:
        await service.revoke_invitation(workspace_id, invitation_id)
    except TracecatAuthorizationError as e:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User does not have permission to revoke invitations",
        ) from e
    except TracecatNotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e),
        ) from e
    except TracecatValidationError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        ) from e
