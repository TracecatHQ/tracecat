from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from tracecat.auth.credentials import (
    AuthenticatedUserOnly,
    OptionalUserDep,
    RoleACL,
    compute_effective_scopes,
)
from tracecat.auth.types import Role
from tracecat.auth.users import current_active_user
from tracecat.authz.controls import has_scope
from tracecat.db.dependencies import AsyncDBSession, AsyncDBSessionBypass
from tracecat.db.models import Invitation, User
from tracecat.exceptions import (
    TracecatAuthorizationError,
    TracecatNotFoundError,
    TracecatValidationError,
)
from tracecat.identifiers import InvitationID, OrganizationID, WorkspaceID
from tracecat.invitations.enums import InvitationStatus
from tracecat.invitations.schemas import (
    InvitationAccept,
    InvitationCreate,
    InvitationCreateResponse,
    InvitationDecline,
    InvitationRead,
    InvitationReadMinimal,
    InvitationWorkspaceOptionRead,
    PendingInvitationRead,
)
from tracecat.invitations.service import (
    InvitationGroup,
    InvitationService,
    accept_invitation_for_user,
    decline_invitation_for_user,
    get_invitation_group_by_token,
    list_pending_invitation_groups_for_email,
)

router = APIRouter(prefix="/invitations", tags=["invitations"])

InvitationUserRole = Annotated[
    Role,
    RoleACL(
        allow_user=True,
        allow_service=False,
        require_workspace="optional",
    ),
]


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


async def _check_scope(
    role: Role,
    required_scope: str,
    workspace_id: WorkspaceID | None = None,
) -> None:
    """Verify the operator has the required scope in the target context."""
    if workspace_id is None:
        scoped_role = role
        if scoped_role.organization_id is not None and not scoped_role.scopes:
            scoped_scopes = await compute_effective_scopes(scoped_role)
            scoped_role = scoped_role.model_copy(update={"scopes": scoped_scopes})
        if scoped_role.scopes and has_scope(scoped_role.scopes, required_scope):
            return
    else:
        ws_role = role.model_copy(update={"workspace_id": workspace_id})
        ws_scopes = await compute_effective_scopes(ws_role)
        if has_scope(ws_scopes, required_scope):
            return

    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Insufficient permissions",
    )


async def _scoped_role_for_org_context(
    role: Role,
    *,
    organization_id: OrganizationID,
    workspace_id: WorkspaceID | None = None,
) -> Role:
    """Return a role scoped to the target org/workspace with computed scopes."""
    scoped_role = role.model_copy(
        update={
            "organization_id": organization_id,
            "workspace_id": workspace_id,
        }
    )
    scoped_scopes = await compute_effective_scopes(scoped_role)
    return scoped_role.model_copy(update={"scopes": scoped_scopes})


def _workspace_option_to_read(
    invitation: Invitation,
) -> InvitationWorkspaceOptionRead:
    assert invitation.workspace_id is not None
    workspace = invitation.__dict__.get("workspace")
    role_obj = invitation.__dict__.get("role_obj")
    assert role_obj is not None
    return InvitationWorkspaceOptionRead(
        invitation_id=invitation.id,
        workspace_id=invitation.workspace_id,
        workspace_name=workspace.name if workspace is not None else None,
        role_id=invitation.role_id,
        role_name=role_obj.name,
        role_slug=role_obj.slug,
        status=invitation.status,
        expires_at=invitation.expires_at,
        created_at=invitation.created_at,
        accepted_at=invitation.accepted_at,
    )


def _group_to_read(
    group: InvitationGroup,
    *,
    include_token: bool = False,
) -> InvitationRead:
    invitation = group.invitation
    workspace_name = None
    if invitation.workspace_id is not None:
        if (workspace := invitation.__dict__.get("workspace")) is not None:
            workspace_name = workspace.name
    role_obj = invitation.__dict__.get("role_obj")
    assert role_obj is not None
    return InvitationRead(
        id=invitation.id,
        organization_id=invitation.organization_id,
        workspace_id=invitation.workspace_id,
        workspace_name=workspace_name,
        email=invitation.email,
        role_id=invitation.role_id,
        role_name=role_obj.name,
        role_slug=role_obj.slug,
        status=invitation.status,
        invited_by=invitation.invited_by,
        expires_at=invitation.expires_at,
        created_at=invitation.created_at,
        accepted_at=invitation.accepted_at,
        token=group.accept_token if include_token else None,
        workspace_options=[
            _workspace_option_to_read(workspace_invitation)
            for workspace_invitation in group.workspace_invitations
        ],
    )


def _group_to_minimal(
    group: InvitationGroup,
    *,
    user: User | None,
) -> InvitationReadMinimal:
    invitation = group.invitation
    inviter = invitation.__dict__.get("inviter")
    organization = invitation.__dict__.get("organization")
    role_obj = invitation.__dict__.get("role_obj")
    assert organization is not None
    assert role_obj is not None
    inviter_name, inviter_email = _get_user_display_name_and_email(inviter)
    email_matches: bool | None = None
    if user is not None:
        email_matches = user.email.lower() == invitation.email.lower()
    workspace_name = None
    if invitation.workspace_id is not None:
        if (workspace := invitation.__dict__.get("workspace")) is not None:
            workspace_name = workspace.name

    return InvitationReadMinimal(
        invitation_id=invitation.id,
        organization_id=invitation.organization_id,
        organization_slug=organization.slug,
        organization_name=organization.name,
        workspace_id=invitation.workspace_id,
        workspace_name=workspace_name,
        inviter_name=inviter_name,
        inviter_email=inviter_email,
        role_name=role_obj.name,
        role_slug=role_obj.slug,
        status=invitation.status,
        expires_at=invitation.expires_at,
        email_matches=email_matches,
        accept_token=group.accept_token,
        workspace_options=[
            _workspace_option_to_read(workspace_invitation)
            for workspace_invitation in group.workspace_invitations
        ],
    )


def _group_to_pending_read(group: InvitationGroup) -> PendingInvitationRead:
    invitation = group.invitation
    inviter = invitation.__dict__.get("inviter")
    organization = invitation.__dict__.get("organization")
    role_obj = invitation.__dict__.get("role_obj")
    assert organization is not None
    assert role_obj is not None
    inviter_name, inviter_email = _get_user_display_name_and_email(inviter)
    workspace_name = None
    if invitation.workspace_id is not None:
        if (workspace := invitation.__dict__.get("workspace")) is not None:
            workspace_name = workspace.name
    return PendingInvitationRead(
        accept_token=group.accept_token,
        organization_id=invitation.organization_id,
        organization_name=organization.name,
        workspace_id=invitation.workspace_id,
        workspace_name=workspace_name,
        inviter_name=inviter_name,
        inviter_email=inviter_email,
        role_name=role_obj.name,
        role_slug=role_obj.slug,
        expires_at=invitation.expires_at,
        workspace_options=[
            _workspace_option_to_read(workspace_invitation)
            for workspace_invitation in group.workspace_invitations
        ],
    )


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=InvitationCreateResponse,
)
async def create_invitation(
    *,
    role: InvitationUserRole,
    session: AsyncDBSession,
    params: InvitationCreate,
) -> InvitationCreateResponse:
    """Create an organization or workspace invitation."""
    service = InvitationService(session, role=role)

    if params.workspace_id is not None:
        await _check_scope(role, "workspace:member:invite", params.workspace_id)
    else:
        await _check_scope(role, "org:member:invite")

    try:
        invitation = await service.create_invitation(params)
    except TracecatAuthorizationError as e:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(e),
        ) from e
    except TracecatValidationError as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(e),
        ) from e
    except IntegrityError as e:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"An invitation already exists for {params.email.lower()}",
        ) from e

    invitation_read: InvitationRead | None = None
    if invitation is not None:
        group = await service.build_grouped_invitation(invitation)
        invitation_read = _group_to_read(group, include_token=True)

    return InvitationCreateResponse(
        message="Invitation request processed",
        invitation=invitation_read,
    )


@router.get("", response_model=list[InvitationRead])
async def list_invitations(
    *,
    role: InvitationUserRole,
    session: AsyncDBSession,
    workspace_id: WorkspaceID | None = Query(default=None),
    invitation_status: InvitationStatus | None = Query(default=None, alias="status"),
) -> list[InvitationRead]:
    """List invitations, grouping pending org invites with attached workspaces."""
    service = InvitationService(session, role=role)

    if workspace_id is not None:
        await _check_scope(role, "workspace:member:read", workspace_id)
    else:
        await _check_scope(role, "org:member:read")

    try:
        groups = await service.list_grouped_invitations(
            workspace_id=workspace_id,
            status=invitation_status,
        )
    except TracecatAuthorizationError as e:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(e),
        ) from e
    except TracecatValidationError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        ) from e

    return [_group_to_read(group) for group in groups]


@router.get("/pending/me", response_model=list[PendingInvitationRead])
async def list_my_pending_invitations(
    *,
    session: AsyncDBSessionBypass,
    user: Annotated[User, Depends(current_active_user)],
) -> list[PendingInvitationRead]:
    """List grouped pending invitations for the authenticated user."""
    groups = await list_pending_invitation_groups_for_email(
        session,
        email=user.email,
    )
    return [_group_to_pending_read(group) for group in groups]


@router.get("/token/{token}", response_model=InvitationReadMinimal)
async def get_invitation_by_token(
    *,
    session: AsyncDBSessionBypass,
    token: str,
    user: OptionalUserDep = None,
) -> InvitationReadMinimal:
    """Resolve a token to its grouped invitation view for the accept page."""
    try:
        group = await get_invitation_group_by_token(session, token=token)
    except TracecatNotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e),
        ) from e

    return _group_to_minimal(group, user=user)


@router.post("/accept")
async def accept_invitation(
    *,
    role: AuthenticatedUserOnly,
    session: AsyncDBSessionBypass,
    params: InvitationAccept,
) -> dict[str, str]:
    """Accept an invitation using the grouped token flow."""
    assert role.user_id is not None
    try:
        await accept_invitation_for_user(
            session,
            user_id=role.user_id,
            token=params.token,
            selected_workspace_ids=params.selected_workspace_ids,
        )
    except TracecatNotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e),
        ) from e
    except (TracecatAuthorizationError, TracecatValidationError) as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        ) from e
    return {"message": "Invitation accepted successfully"}


@router.post("/decline")
async def decline_invitation(
    *,
    role: AuthenticatedUserOnly,
    session: AsyncDBSessionBypass,
    params: InvitationDecline,
) -> dict[str, str]:
    """Decline an invitation using the grouped token flow."""
    assert role.user_id is not None
    try:
        await decline_invitation_for_user(
            session,
            user_id=role.user_id,
            token=params.token,
        )
    except TracecatNotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e),
        ) from e
    except (TracecatAuthorizationError, TracecatValidationError) as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        ) from e
    return {"message": "Invitation declined successfully"}


@router.delete("/{invitation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_invitation(
    *,
    role: AuthenticatedUserOnly,
    session: AsyncDBSession,
    invitation_id: InvitationID,
) -> None:
    """Revoke a pending invitation."""
    invitation = await session.scalar(
        select(Invitation).where(Invitation.id == invitation_id)
    )
    if invitation is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Invitation not found",
        )

    scoped_role = await _scoped_role_for_org_context(
        role,
        organization_id=invitation.organization_id,
        workspace_id=invitation.workspace_id,
    )
    service = InvitationService(session, role=scoped_role)

    if invitation.workspace_id is not None:
        await _check_scope(
            scoped_role,
            "workspace:member:invite",
            invitation.workspace_id,
        )
    else:
        await _check_scope(scoped_role, "org:member:invite")

    try:
        await service.revoke_invitation(invitation_id)
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


@router.get("/{invitation_id}/token")
async def get_invitation_token(
    *,
    role: AuthenticatedUserOnly,
    session: AsyncDBSession,
    invitation_id: InvitationID,
) -> dict[str, str]:
    """Get the token for an invitation row."""
    invitation = await session.scalar(
        select(Invitation).where(Invitation.id == invitation_id)
    )
    if invitation is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Invitation not found",
        )

    scoped_role = await _scoped_role_for_org_context(
        role,
        organization_id=invitation.organization_id,
        workspace_id=invitation.workspace_id,
    )

    if invitation.workspace_id is not None:
        await _check_scope(
            scoped_role,
            "workspace:member:invite",
            invitation.workspace_id,
        )
    else:
        await _check_scope(scoped_role, "org:member:invite")

    return {"token": invitation.token}
