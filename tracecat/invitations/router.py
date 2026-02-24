from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import cast, select
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.exc import IntegrityError

from tracecat.auth.credentials import AuthenticatedUserOnly, OptionalUserDep
from tracecat.auth.dependencies import OrgUserRole
from tracecat.authz.controls import has_scope
from tracecat.db.dependencies import AsyncDBSession
from tracecat.db.models import (
    Invitation,
    Organization,
    User,
    Workspace,
)
from tracecat.db.models import Role as DBRole
from tracecat.exceptions import (
    TracecatAuthorizationError,
    TracecatNotFoundError,
    TracecatValidationError,
)
from tracecat.identifiers import InvitationID
from tracecat.invitations.schemas import InvitationAccept, InvitationReadMinimal
from tracecat.invitations.service import InvitationService, accept_invitation_for_user

router = APIRouter(prefix="/invitations", tags=["invitations"])


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


@router.get("/token/{token}", response_model=InvitationReadMinimal)
async def get_invitation_by_token(
    *,
    session: AsyncDBSession,
    token: str,
    user: OptionalUserDep = None,
) -> InvitationReadMinimal:
    """Get invitation details by token (public endpoint for the accept page).

    Works for both org-level and workspace-level invitations.
    Returns organization/workspace name and inviter info.
    If user is authenticated, also returns whether their email matches.
    """
    result = await session.execute(
        select(Invitation, DBRole)
        .join(DBRole, cast(DBRole.id, UUID) == Invitation.role_id)
        .where(Invitation.token == token)
    )
    row = result.first()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Invitation not found",
        )
    invitation, role_obj = row

    # Fetch organization
    org_result = await session.execute(
        select(Organization).where(Organization.id == invitation.organization_id)
    )
    org = org_result.scalar_one()

    # Fetch workspace if workspace-scoped
    workspace_name: str | None = None
    if invitation.workspace_id is not None:
        ws_result = await session.execute(
            select(Workspace).where(Workspace.id == invitation.workspace_id)
        )
        workspace = ws_result.scalar_one()
        workspace_name = workspace.name

    # Fetch inviter info
    inviter_name: str | None = None
    inviter_email: str | None = None
    if invitation.invited_by:
        inviter_result = await session.execute(
            select(User).where(cast(User.id, UUID) == invitation.invited_by)
        )
        inviter = inviter_result.scalar_one_or_none()
        inviter_name, inviter_email = _get_user_display_name_and_email(inviter)

    # Check email match for authenticated user
    email_matches: bool | None = None
    if user is not None:
        email_matches = user.email.lower() == invitation.email.lower()

    return InvitationReadMinimal(
        invitation_id=invitation.id,
        organization_id=invitation.organization_id,
        organization_name=org.name,
        workspace_id=invitation.workspace_id,
        workspace_name=workspace_name,
        inviter_name=inviter_name,
        inviter_email=inviter_email,
        role_name=role_obj.name,
        role_slug=role_obj.slug,
        status=invitation.status,
        expires_at=invitation.expires_at,
        email_matches=email_matches,
    )


@router.post("/accept")
async def accept_invitation(
    *,
    role: AuthenticatedUserOnly,
    session: AsyncDBSession,
    params: InvitationAccept,
) -> dict[str, str]:
    """Accept an invitation (org or workspace) and join.

    Determines the invitation type from the token and creates the appropriate
    memberships. Uses AuthenticatedUserOnly — no org context required since
    the user may not belong to the org yet.
    """
    assert role.user_id is not None
    try:
        await accept_invitation_for_user(
            session,
            user_id=role.user_id,
            token=params.token,
        )
        return {"message": "Invitation accepted successfully"}
    except TracecatNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    except (TracecatAuthorizationError, TracecatValidationError) as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)
        ) from e
    except IntegrityError as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="User is already a member",
        ) from e


def _check_invitation_scope(
    role: OrgUserRole, invitation: Invitation, scope_action: str
) -> None:
    """Verify the user has the appropriate scope for the invitation type.

    For workspace-scoped invitations, checks ``workspace:member:{scope_action}``.
    For org-scoped invitations, checks ``org:member:invite``.

    Raises:
        HTTPException 403 if the user lacks the required scope.
    """
    if invitation.workspace_id is not None:
        required = f"workspace:member:{scope_action}"
    else:
        required = "org:member:invite"

    if not role.scopes or not has_scope(role.scopes, required):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Insufficient permissions",
        )


@router.delete("/{invitation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_invitation(
    *,
    role: OrgUserRole,
    session: AsyncDBSession,
    invitation_id: InvitationID,
) -> None:
    """Revoke any pending invitation (org or workspace)."""
    service = InvitationService(session, role=role)
    try:
        invitation = await service.get_invitation(invitation_id)
    except TracecatNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e

    _check_invitation_scope(role, invitation, "remove")

    try:
        await service.revoke_invitation(invitation_id)
    except TracecatValidationError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)
        ) from e


@router.get("/{invitation_id}/token")
async def get_invitation_token(
    *,
    role: OrgUserRole,
    session: AsyncDBSession,
    invitation_id: InvitationID,
) -> dict[str, str]:
    """Get the token for a specific invitation (admin only).

    Used to generate shareable invitation links.
    """
    service = InvitationService(session, role=role)
    try:
        invitation = await service.get_invitation(invitation_id)
    except TracecatNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e

    _check_invitation_scope(role, invitation, "invite")

    return {"token": invitation.token}
