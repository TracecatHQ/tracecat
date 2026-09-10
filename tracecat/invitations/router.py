from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError, NoResultFound
from sqlalchemy.orm import selectinload

from tracecat.auth.credentials import AuthenticatedUserOnly, OptionalUserDep
from tracecat.auth.dependencies import OrgUserRole
from tracecat.auth.users import current_active_user
from tracecat.authz.controls import require_scope
from tracecat.db.dependencies import AsyncDBSession, AsyncDBSessionBypass
from tracecat.db.models import (
    Invitation,
    Organization,
    User,
)
from tracecat.exceptions import (
    TracecatAuthorizationError,
    TracecatNotFoundError,
    TracecatValidationError,
)
from tracecat.invitations.enums import InvitationStatus
from tracecat.invitations.schemas import (
    InvitationAccept,
    InvitationCreate,
    InvitationRead,
    InvitationReadMinimal,
    InvitationTokenRead,
    PendingInvitationRead,
)
from tracecat.invitations.service import (
    InvitationService,
    accept_invitation_for_user,
    find_invitation_by_token,
)

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


@router.post(
    "",
    response_model=InvitationRead,
    status_code=status.HTTP_201_CREATED,
)
@require_scope("org:member:invite")
async def create_invitation(
    *,
    role: OrgUserRole,
    session: AsyncDBSession,
    params: InvitationCreate,
) -> InvitationRead:
    """Create an invitation carrying one or more role grants."""
    service = InvitationService(session, role=role)
    try:
        invitation = await service.create_invitation(params)
    except TracecatAuthorizationError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e)) from e
    except TracecatValidationError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)
        ) from e
    except IntegrityError as e:
        # Race condition: another request created an invitation for same email
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An invitation already exists for this email",
        ) from e

    return InvitationRead.model_validate(invitation)


@router.post("/accept")
async def accept_invitation(
    *,
    role: AuthenticatedUserOnly,
    session: AsyncDBSessionBypass,
    params: InvitationAccept,
) -> dict[str, str]:
    """Accept an invitation and join the organization.

    This endpoint doesn't require organization context since the user
    may not belong to any organization yet. Uses AuthenticatedUserOnly
    which only requires an authenticated user (role.organization_id is None).
    """
    # user_id is guaranteed to be set by AuthenticatedUserOnly
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
    except TracecatAuthorizationError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)
        ) from e
    except IntegrityError as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="User is already a member of this organization",
        ) from e


@router.get("/pending/me", response_model=list[PendingInvitationRead])
async def list_my_pending_invitations(
    *,
    role: AuthenticatedUserOnly,
    session: AsyncDBSessionBypass,
    user: Annotated[User, Depends(current_active_user)],
) -> list[PendingInvitationRead]:
    """List pending, unexpired invitations for the authenticated user."""
    assert role.user_id is not None

    now = datetime.now(UTC)
    statement = (
        select(Invitation, Organization, User)
        .join(
            Organization,
            Organization.id == Invitation.organization_id,  # pyright: ignore[reportArgumentType]
        )
        .outerjoin(
            User,
            User.id == Invitation.invited_by,  # pyright: ignore[reportArgumentType]
        )
        .where(
            func.lower(Invitation.email) == user.email.lower(),
            Invitation.status == InvitationStatus.PENDING,
            Invitation.expires_at > now,
            Invitation.grants.any(),
        )
        .order_by(Invitation.created_at.desc())
        .options(selectinload(Invitation.grants))
    )
    result = await session.execute(statement)
    rows = result.tuples().all()

    pending_invitations: list[PendingInvitationRead] = []
    for invitation, organization, inviter in rows:
        inviter_name, inviter_email = _get_user_display_name_and_email(inviter)

        pending_invitations.append(
            PendingInvitationRead.model_validate(
                {
                    "token": invitation.token,
                    "organization_id": invitation.organization_id,
                    "organization_name": organization.name,
                    "inviter_name": inviter_name,
                    "inviter_email": inviter_email,
                    "grants": invitation.grants,
                    "expires_at": invitation.expires_at,
                }
            )
        )
    return pending_invitations


@router.get("/token/{token}", response_model=InvitationReadMinimal)
async def get_invitation_by_token(
    *,
    user: OptionalUserDep = None,
    session: AsyncDBSessionBypass,
    token: str,
) -> InvitationReadMinimal:
    """Get minimal invitation details by token (public endpoint for UI).

    Returns organization name and inviter info for the acceptance page.
    If user is authenticated, also returns whether their email matches the invitation.
    """
    # Queried directly rather than through the service: there is no org context yet.
    invitation = await find_invitation_by_token(session, token)
    if invitation is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Invitation not found"
        )

    org = invitation.organization

    inviter_name: str | None = None
    inviter_email: str | None = None
    if invitation.invited_by:
        inviter_result = await session.execute(
            select(User).where(User.id == invitation.invited_by)  # pyright: ignore[reportArgumentType]
        )
        inviter = inviter_result.scalar_one_or_none()
        inviter_name, inviter_email = _get_user_display_name_and_email(inviter)

    # Check if authenticated user's email matches the invitation (case-insensitive)
    email_matches: bool | None = None
    if user is not None:
        email_matches = user.email.lower() == invitation.email.lower()

    return InvitationReadMinimal.model_validate(
        {
            "organization_id": invitation.organization_id,
            "organization_name": org.name,
            "organization_slug": org.slug,
            "inviter_name": inviter_name,
            "inviter_email": inviter_email,
            "grants": invitation.grants,
            "status": invitation.status,
            "expires_at": invitation.expires_at,
            "email_matches": email_matches,
        }
    )


@router.delete("/{invitation_id}", status_code=status.HTTP_204_NO_CONTENT)
@require_scope("org:member:invite")
async def revoke_invitation(
    *,
    role: OrgUserRole,
    session: AsyncDBSession,
    invitation_id: UUID,
) -> None:
    """Revoke a pending invitation."""
    service = InvitationService(session, role=role)
    try:
        await service.revoke_invitation(invitation_id)
    except NoResultFound as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Invitation not found"
        ) from e
    except TracecatAuthorizationError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e)) from e


@router.get("/{invitation_id}/token", response_model=InvitationTokenRead)
@require_scope("org:member:invite")
async def get_invitation_token(
    *,
    role: OrgUserRole,
    session: AsyncDBSession,
    invitation_id: UUID,
) -> InvitationTokenRead:
    """Get the token for a specific invitation (admin only).

    This endpoint is used to generate shareable invitation links.
    """
    service = InvitationService(session, role=role)
    try:
        invitation = await service.get_invitation(invitation_id)
    except NoResultFound as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Invitation not found"
        ) from e
    return InvitationTokenRead(token=invitation.token)
