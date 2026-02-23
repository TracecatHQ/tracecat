from datetime import UTC, datetime
from typing import Annotated

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    status,
)
from sqlalchemy.exc import IntegrityError, NoResultFound

from tracecat.auth.credentials import AuthenticatedUserOnly, OptionalUserDep, RoleACL
from tracecat.auth.dependencies import OrgUserRole
from tracecat.auth.types import Role
from tracecat.authz.controls import has_scope, require_scope
from tracecat.authz.service import MembershipService
from tracecat.db.dependencies import AsyncDBSession
from tracecat.db.models import Organization, User
from tracecat.exceptions import (
    TracecatAuthorizationError,
    TracecatManagementError,
    TracecatNotFoundError,
    TracecatValidationError,
)
from tracecat.identifiers import InvitationID, UserID, WorkspaceID
from tracecat.invitations.enums import InvitationStatus
from tracecat.logger import logger
from tracecat.workspaces.schemas import (
    WorkspaceCreate,
    WorkspaceInvitationAccept,
    WorkspaceInvitationCreate,
    WorkspaceInvitationList,
    WorkspaceInvitationRead,
    WorkspaceInvitationReadMinimal,
    WorkspaceMember,
    WorkspaceMembershipCreate,
    WorkspaceMembershipRead,
    WorkspaceMemberStatus,
    WorkspaceRead,
    WorkspaceReadMinimal,
    WorkspaceSearch,
    WorkspaceSettingsRead,
    WorkspaceUpdate,
)
from tracecat.workspaces.service import (
    WorkspaceService,
    accept_workspace_invitation_for_user,
)

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


# === Workspace Invitation Public Endpoints === #
# NOTE: These must be defined before /{workspace_id} to avoid path conflicts


def _get_user_display_name_and_email(
    user: User | None,
) -> tuple[str | None, str | None]:
    """Build display name/email pair for inviter fields."""
    if user is None:
        return None, None
    if user.first_name or user.last_name:
        name_parts = [user.first_name, user.last_name]
        name = " ".join(part for part in name_parts if part)
    else:
        name = user.email
    return name, user.email


@router.get(
    "/invitations/token/{token}",
    response_model=WorkspaceInvitationReadMinimal,
)
async def get_workspace_invitation_by_token(
    *,
    session: AsyncDBSession,
    token: str,
    user: OptionalUserDep = None,
) -> WorkspaceInvitationReadMinimal:
    """Get minimal workspace invitation details by token (public endpoint).

    Returns workspace name, org name, and inviter info for the acceptance page.
    If user is authenticated, also returns whether their email matches.
    """
    from sqlalchemy import select

    from tracecat.db.models import Invitation
    from tracecat.db.models import Role as DBRole

    result = await session.execute(
        select(Invitation, DBRole)
        .join(DBRole, DBRole.id == Invitation.role_id)  # pyright: ignore[reportArgumentType]
        .where(Invitation.token == token)
    )
    row = result.first()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Invitation not found",
        )
    invitation, role_obj = row

    # Fetch workspace
    from tracecat.db.models import Workspace

    ws_result = await session.execute(
        select(Workspace).where(Workspace.id == invitation.workspace_id)
    )
    workspace = ws_result.scalar_one()

    # Fetch organization name
    org_result = await session.execute(
        select(Organization).where(Organization.id == workspace.organization_id)
    )
    org = org_result.scalar_one()

    # Fetch inviter info
    inviter_name: str | None = None
    inviter_email: str | None = None
    if invitation.invited_by:
        inviter_result = await session.execute(
            select(User).where(User.id == invitation.invited_by)  # pyright: ignore[reportArgumentType]
        )
        inviter = inviter_result.scalar_one_or_none()
        inviter_name, inviter_email = _get_user_display_name_and_email(inviter)

    # Check email match for authenticated user
    email_matches: bool | None = None
    if user is not None:
        email_matches = user.email.lower() == invitation.email.lower()

    return WorkspaceInvitationReadMinimal(
        workspace_id=workspace.id,
        workspace_name=workspace.name,
        organization_name=org.name,
        inviter_name=inviter_name,
        inviter_email=inviter_email,
        role_name=role_obj.name,
        status=invitation.status,
        expires_at=invitation.expires_at,
        email_matches=email_matches,
    )


@router.post("/invitations/accept")
async def accept_workspace_invitation(
    *,
    role: AuthenticatedUserOnly,
    session: AsyncDBSession,
    params: WorkspaceInvitationAccept,
) -> dict[str, str]:
    """Accept a workspace invitation and join the workspace.

    Uses AuthenticatedUserOnly — no org context required since user
    may not belong to the workspace's org yet.
    """
    assert role.user_id is not None
    try:
        await accept_workspace_invitation_for_user(
            session,
            user_id=role.user_id,
            token=params.token,
        )
        return {"message": "Workspace invitation accepted successfully"}
    except TracecatNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    except TracecatValidationError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)
        ) from e
    except IntegrityError as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="User is already a member of this workspace",
        ) from e


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
    """List members of a workspace, including pending invitations."""
    service = MembershipService(session, role=role)
    result = await service.list_workspace_members(workspace_id)

    # Append pending, non-expired invitations as "invited" members
    now = datetime.now(UTC)
    ws_service = WorkspaceService(session, role=role)
    invitations = await ws_service.list_invitations(
        workspace_id, status=InvitationStatus.PENDING
    )
    for inv in invitations:
        if inv.expires_at > now:
            result.append(
                WorkspaceMember(
                    invitation_id=inv.id,
                    email=inv.email,
                    role_name=inv.role_obj.name,
                    status=WorkspaceMemberStatus.INVITED,
                    token=inv.token,
                    expires_at=inv.expires_at,
                    created_at=inv.created_at,
                )
            )

    return result


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
    await service.delete_membership(workspace_id, user_id=user_id)


# === Invitations === #


@router.post("/{workspace_id}/invitations", status_code=status.HTTP_201_CREATED)
@require_scope("workspace:member:invite")
async def create_workspace_invitation(
    *,
    role: WorkspaceUserInPath,
    workspace_id: WorkspaceID,
    params: WorkspaceInvitationCreate,
    session: AsyncDBSession,
) -> WorkspaceInvitationRead:
    """Create a workspace invitation.

    Authorization
    -------------
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
        role_id=str(invitation.role_id),
        role_name=invitation.role_obj.name,
        role_slug=invitation.role_obj.slug,
        status=invitation.status,
        invited_by=invitation.invited_by,
        expires_at=invitation.expires_at,
        accepted_at=invitation.accepted_at,
        created_at=invitation.created_at,
        token=invitation.token,
    )


@router.get("/{workspace_id}/invitations")
@require_scope("workspace:member:read")
async def list_workspace_invitations(
    *,
    role: WorkspaceUserInPath,
    workspace_id: WorkspaceID,
    session: AsyncDBSession,
    params: WorkspaceInvitationList = Depends(),
) -> list[WorkspaceInvitationRead]:
    """List workspace invitations.

    Authorization
    -------------
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
            role_id=str(inv.role_id),
            role_name=inv.role_obj.name,
            role_slug=inv.role_obj.slug,
            status=inv.status,
            invited_by=inv.invited_by,
            expires_at=inv.expires_at,
            accepted_at=inv.accepted_at,
            created_at=inv.created_at,
            token=inv.token,
        )
        for inv in invitations
    ]


@router.get("/{workspace_id}/invitations/{invitation_id}/token")
@require_scope("workspace:member:invite")
async def get_workspace_invitation_token(
    *,
    role: WorkspaceUserInPath,
    workspace_id: WorkspaceID,
    invitation_id: InvitationID,
    session: AsyncDBSession,
) -> dict[str, str]:
    """Get the token for a specific workspace invitation (admin only).

    Used to generate shareable invitation links.
    """
    service = WorkspaceService(session, role=role)
    invitation = await service.get_invitation(workspace_id, invitation_id)
    if invitation is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Invitation not found",
        )
    return {"token": invitation.token}


@router.delete(
    "/{workspace_id}/invitations/{invitation_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
@require_scope("workspace:member:remove")
async def revoke_workspace_invitation(
    *,
    role: WorkspaceUserInPath,
    workspace_id: WorkspaceID,
    invitation_id: InvitationID,
    session: AsyncDBSession,
) -> None:
    """Revoke a workspace invitation.

    Authorization
    -------------
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
