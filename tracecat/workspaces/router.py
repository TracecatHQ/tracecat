from datetime import UTC, datetime
from typing import Annotated

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    status,
)
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError, NoResultFound

from tracecat.auth.credentials import (
    AuthenticatedUserOnly,
    OptionalUserDep,
    RoleACL,
)
from tracecat.auth.dependencies import OrgActorRole
from tracecat.auth.types import Role
from tracecat.auth.users import current_active_user
from tracecat.authz.controls import require_scope
from tracecat.authz.service import MembershipService
from tracecat.db.dependencies import AsyncDBSession, AsyncDBSessionBypass
from tracecat.db.models import Invitation, Organization, User, Workspace
from tracecat.db.models import Role as DBRole
from tracecat.email import (
    InvitationEmail,
    build_accept_url,
    is_email_configured,
    send_invitation_emails_batch,
)
from tracecat.exceptions import (
    TracecatAuthorizationError,
    TracecatConflictError,
    TracecatManagementError,
    TracecatNotFoundError,
    TracecatValidationError,
)
from tracecat.identifiers import InvitationID, UserID, WorkspaceID
from tracecat.invitations.enums import InvitationStatus
from tracecat.invitations.types import BatchInviteStatus
from tracecat.logger import logger
from tracecat.workspaces.schemas import (
    WorkspaceBatchInvitationItemResult,
    WorkspaceCreate,
    WorkspaceInvitationAccept,
    WorkspaceInvitationBatchCreate,
    WorkspaceInvitationBatchResult,
    WorkspaceInvitationCreate,
    WorkspaceInvitationList,
    WorkspaceInvitationRead,
    WorkspaceInvitationReadMinimal,
    WorkspaceInvitationTokenRead,
    WorkspaceMember,
    WorkspaceMembershipCreate,
    WorkspaceMembershipRead,
    WorkspacePendingInvitationRead,
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
        allow_api_key=True,
        require_workspace="yes",
        workspace_id_in_path=True,
    ),
]
# === Management === #


@router.get("")
@require_scope("org:read", "org:workspace:read", "workspace:read", require_all=False)
async def list_workspaces(
    *,
    role: OrgActorRole,
    session: AsyncDBSession,
) -> list[WorkspaceReadMinimal]:
    """List workspaces the user has access to.

    Access
    ------
    - Org owners/admins (have `org:workspace:read` scope): See all workspaces in the org.
    - Org members (have `org:read` scope): See only workspaces where they are a member.
    - Workspace-bound service accounts: See only their bound workspace.

    Membership limits workspace-scoped access to the actor's workspaces.
    """
    service = WorkspaceService(session, role=role)
    try:
        workspaces = await service.list_accessible_workspaces()
    except TracecatAuthorizationError as e:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden"
        ) from e
    return [WorkspaceReadMinimal(id=ws.id, name=ws.name) for ws in workspaces]


@router.post("", status_code=status.HTTP_201_CREATED)
@require_scope("workspace:create")
async def create_workspace(
    *,
    role: OrgActorRole,
    params: WorkspaceCreate,
    session: AsyncDBSession,
) -> WorkspaceReadMinimal:
    """Create a new workspace.

    Authorization
    -------------
    - Admin: Can create a workspace for any user.
    """
    if role.type == "service_account" and role.bound_workspace_id is not None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")
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
@require_scope("org:read", "org:workspace:read", "workspace:read", require_all=False)
async def search_workspaces(
    *,
    role: OrgActorRole,
    session: AsyncDBSession,
    params: WorkspaceSearch = Depends(),
) -> list[WorkspaceReadMinimal]:
    """Return Workflow as title, description, list of Action JSONs, adjacency list of Action IDs."""
    service = WorkspaceService(session, role=role)
    try:
        workspaces = await service.search_workspaces(params)
    except TracecatAuthorizationError as e:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden"
        ) from e
    return [WorkspaceReadMinimal(id=ws.id, name=ws.name) for ws in workspaces]


def _inviter_display_name_and_email(
    user: User | None,
) -> tuple[str | None, str | None]:
    """Build display name/email pair for inviter fields."""
    if user is None:
        return None, None
    if user.first_name or user.last_name:
        name = " ".join(p for p in (user.first_name, user.last_name) if p)
    else:
        name = user.email
    return name, user.email


# Public, token-based invitation routes (no workspace context until the invite
# is accepted). These are NOT nested under /{workspace_id}; the token identifies
# the invite. They mirror the organization invitation token/accept endpoints so
# the shared /invitations/accept page can resolve a workspace token.
#
# NOTE: These must be declared BEFORE `GET /{workspace_id}`. `workspace_id` is a
# UUID path param, so a request to `/invitations/...` would otherwise match the
# single-workspace route first and 422 on the non-UUID segment. Same reason the
# `/search` route is declared above.


@router.get(
    "/invitations/pending/me",
    response_model=list[WorkspacePendingInvitationRead],
)
async def list_my_pending_workspace_invitations(
    *,
    role: AuthenticatedUserOnly,
    session: AsyncDBSessionBypass,
    user: Annotated[User, Depends(current_active_user)],
) -> list[WorkspacePendingInvitationRead]:
    """List pending, unexpired workspace invitations for the current user.

    Matches by the authenticated user's email so a freshly signed-up user can
    discover invitations without the original token link. Mirrors the
    organization-level ``/invitations/pending/me`` endpoint.
    """
    assert role.user_id is not None

    now = datetime.now(UTC)
    statement = (
        select(Invitation, Workspace, Organization, DBRole)
        .join(Workspace, Workspace.id == Invitation.workspace_id)  # pyright: ignore[reportArgumentType]
        .join(Organization, Organization.id == Workspace.organization_id)  # pyright: ignore[reportArgumentType]
        .join(DBRole, DBRole.id == Invitation.role_id)  # pyright: ignore[reportArgumentType]
        .where(
            func.lower(Invitation.email) == user.email.lower(),
            Invitation.status == InvitationStatus.PENDING,
            Invitation.expires_at > now,
        )
        .order_by(Invitation.created_at.desc())
    )
    result = await session.execute(statement)
    rows = result.tuples().all()

    pending: list[WorkspacePendingInvitationRead] = []
    for invitation, workspace, organization, role_obj in rows:
        inviter_name: str | None = None
        inviter_email: str | None = None
        if invitation.invited_by:
            inviter = await session.scalar(
                select(User).where(User.id == invitation.invited_by)  # pyright: ignore[reportArgumentType]
            )
            inviter_name, inviter_email = _inviter_display_name_and_email(inviter)

        pending.append(
            WorkspacePendingInvitationRead(
                token=invitation.token,
                workspace_id=invitation.workspace_id,
                workspace_name=workspace.name,
                organization_id=workspace.organization_id,
                organization_slug=organization.slug,
                inviter_name=inviter_name,
                inviter_email=inviter_email,
                role_name=role_obj.name,
                role_slug=role_obj.slug,
                expires_at=invitation.expires_at,
            )
        )
    return pending


@router.get("/invitations/token/{token}", response_model=WorkspaceInvitationReadMinimal)
async def get_workspace_invitation_by_token(
    *,
    session: AsyncDBSessionBypass,
    token: str,
    user: OptionalUserDep = None,
) -> WorkspaceInvitationReadMinimal:
    """Get minimal workspace invitation details by token (public endpoint).

    Returns workspace name and inviter info for the acceptance page. If the
    user is authenticated, also returns whether their email matches the invite.
    """
    result = await session.execute(
        select(Invitation, Workspace, Organization, DBRole)
        .join(Workspace, Workspace.id == Invitation.workspace_id)  # pyright: ignore[reportArgumentType]
        .join(Organization, Organization.id == Workspace.organization_id)  # pyright: ignore[reportArgumentType]
        .join(DBRole, DBRole.id == Invitation.role_id)  # pyright: ignore[reportArgumentType]
        .where(Invitation.token == token)
    )
    row = result.first()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Invitation not found"
        )
    invitation, workspace, organization, role_obj = row

    inviter_name: str | None = None
    inviter_email: str | None = None
    if invitation.invited_by:
        inviter = await session.scalar(
            select(User).where(User.id == invitation.invited_by)  # pyright: ignore[reportArgumentType]
        )
        inviter_name, inviter_email = _inviter_display_name_and_email(inviter)

    email_matches: bool | None = None
    if user is not None:
        email_matches = user.email.lower() == invitation.email.lower()

    return WorkspaceInvitationReadMinimal(
        workspace_id=invitation.workspace_id,
        workspace_name=workspace.name,
        organization_id=workspace.organization_id,
        organization_slug=organization.slug,
        inviter_name=inviter_name,
        inviter_email=inviter_email,
        role_name=role_obj.name,
        role_slug=role_obj.slug,
        status=invitation.status,
        expires_at=invitation.expires_at,
        email_matches=email_matches,
    )


@router.post("/invitations/accept")
async def accept_workspace_invitation(
    *,
    role: AuthenticatedUserOnly,
    session: AsyncDBSessionBypass,
    params: WorkspaceInvitationAccept,
) -> dict[str, str]:
    """Accept a workspace invitation and join the workspace.

    This endpoint does not require workspace context: the user may not belong
    to the workspace (or its organization) yet. Acceptance auto-creates the
    organization membership when needed. ``AuthenticatedUserOnly`` requires only
    an authenticated user (``role.organization_id`` may be None).
    """
    assert role.user_id is not None

    # The accepting user has no org context yet (AuthenticatedUserOnly). Derive
    # the organization from the invitation's workspace so WorkspaceService can be
    # constructed; acceptance itself re-validates the token atomically.
    org_id = await session.scalar(
        select(Workspace.organization_id)
        .join(Invitation, Invitation.workspace_id == Workspace.id)  # pyright: ignore[reportArgumentType]
        .where(Invitation.token == params.token)
    )
    if org_id is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Invitation not found"
        )

    accept_role = role.model_copy(update={"organization_id": org_id})
    service = WorkspaceService(session, role=accept_role)
    try:
        await service.accept_invitation(params.token, role.user_id)
        return {"message": "Invitation accepted successfully"}
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
    """List active members of a workspace.

    Pending invitations are served separately by the invitations endpoint; the
    members table merges them in client-side for display.
    """
    service = MembershipService(session, role=role)
    return await service.list_workspace_members(workspace_id)


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
    try:
        await service.delete_membership(workspace_id, user_id=user_id)
    except TracecatConflictError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e)) from e


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
        )
        for inv in invitations
    ]


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


@router.post("/{workspace_id}/invitations/bulk")
@require_scope("workspace:member:invite")
async def create_workspace_invitations_bulk(
    *,
    role: WorkspaceUserInPath,
    workspace_id: WorkspaceID,
    params: WorkspaceInvitationBatchCreate,
    session: AsyncDBSession,
) -> WorkspaceInvitationBatchResult:
    """Create workspace invitations in bulk.

    Valid emails are invited; already-members and emails with a live pending
    invitation are skipped and reported per-email. When email is configured,
    invitation emails are sent (best-effort) for created invites; otherwise the
    admin shares the copy-paste invitation links.
    """
    service = WorkspaceService(session, role=role)
    try:
        items = await service.batch_create_invitations(
            workspace_id,
            emails=[str(e) for e in params.emails],
            role_id=params.role_id,
        )
    except TracecatAuthorizationError as e:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User does not have permission to create invitations",
        ) from e
    except TracecatValidationError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        ) from e

    created = [i for i in items if i.status == BatchInviteStatus.CREATED]
    if created and is_email_configured():
        workspace_name = await service.get_workspace_name(workspace_id)
        await send_invitation_emails_batch(
            [
                InvitationEmail(
                    to=i.email,
                    accept_url=build_accept_url(i.token),  # type: ignore[arg-type]
                    context_name=workspace_name,
                    kind="workspace",
                )
                for i in created
                if i.token
            ]
        )

    created_count = sum(1 for i in items if i.status == BatchInviteStatus.CREATED)
    return WorkspaceInvitationBatchResult(
        results=[
            WorkspaceBatchInvitationItemResult(
                email=i.email, status=i.status, reason=i.reason
            )
            for i in items
        ],
        created_count=created_count,
        skipped_count=len(items) - created_count,
    )


@router.get("/{workspace_id}/invitations/{invitation_id}/token")
@require_scope("workspace:member:invite")
async def get_workspace_invitation_token(
    *,
    role: WorkspaceUserInPath,
    workspace_id: WorkspaceID,
    invitation_id: InvitationID,
    session: AsyncDBSession,
) -> WorkspaceInvitationTokenRead:
    """Get the token for a pending workspace invitation (copy-link flow)."""
    service = WorkspaceService(session, role=role)
    try:
        token = await service.get_invitation_token(workspace_id, invitation_id)
    except TracecatNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    except TracecatAuthorizationError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)
        ) from e
    return WorkspaceInvitationTokenRead(token=token)


@router.post(
    "/{workspace_id}/invitations/{invitation_id}/resend",
    status_code=status.HTTP_204_NO_CONTENT,
)
@require_scope("workspace:member:invite")
async def resend_workspace_invitation(
    *,
    role: WorkspaceUserInPath,
    workspace_id: WorkspaceID,
    invitation_id: InvitationID,
    session: AsyncDBSession,
) -> None:
    """Re-send the invitation email for a pending workspace invitation."""
    if not is_email_configured():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email is not configured",
        )
    service = WorkspaceService(session, role=role)
    try:
        invitation = await service.get_pending_invitation(workspace_id, invitation_id)
    except TracecatNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    except TracecatAuthorizationError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)
        ) from e

    workspace_name = await service.get_workspace_name(workspace_id)
    await send_invitation_emails_batch(
        [
            InvitationEmail(
                to=invitation.email,
                accept_url=build_accept_url(invitation.token),
                context_name=workspace_name,
                kind="workspace",
            )
        ]
    )
