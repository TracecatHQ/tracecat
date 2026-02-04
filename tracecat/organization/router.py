from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import and_, func, select
from sqlalchemy.exc import IntegrityError, NoResultFound

from tracecat.auth.credentials import AuthenticatedUserOnly, OptionalUserDep, RoleACL
from tracecat.auth.schemas import SessionRead, UserUpdate
from tracecat.auth.types import Role
from tracecat.auth.users import current_active_user
from tracecat.authz.enums import OrgRole
from tracecat.db.dependencies import AsyncDBSession
from tracecat.db.models import (
    Organization,
    OrganizationDomain,
    OrganizationInvitation,
    OrganizationMembership,
    User,
)
from tracecat.exceptions import (
    TracecatAuthorizationError,
    TracecatNotFoundError,
    TracecatValidationError,
)
from tracecat.identifiers import SessionID, UserID
from tracecat.invitations.enums import InvitationStatus
from tracecat.organization.schemas import (
    OrgDomainRead,
    OrgInvitationAccept,
    OrgInvitationCreate,
    OrgInvitationRead,
    OrgInvitationReadMinimal,
    OrgMemberRead,
    OrgPendingInvitationRead,
    OrgRead,
)
from tracecat.organization.service import OrgService, accept_invitation_for_user

router = APIRouter(prefix="/organization", tags=["organization"])

# RoleACL() returns a Depends object, so no need to wrap with Depends()
OrgUserRole = Annotated[
    Role,
    RoleACL(
        allow_user=True,
        allow_service=False,
        require_workspace="no",
    ),
]

OrgAdminRole = Annotated[
    Role,
    RoleACL(
        allow_user=True,
        allow_service=False,
        require_workspace="no",
        require_org_roles=[OrgRole.OWNER, OrgRole.ADMIN],
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


@router.get("", response_model=OrgRead)
async def get_organization(
    *,
    role: OrgUserRole,
    session: AsyncDBSession,
) -> OrgRead:
    """Get the current organization.

    Returns basic information about the organization the authenticated user belongs to.
    """
    if role.organization_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No organization context",
        )

    result = await session.execute(
        select(Organization).where(Organization.id == role.organization_id)
    )
    org = result.scalar_one_or_none()

    if org is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Organization not found",
        )

    return OrgRead(id=org.id, name=org.name)


@router.get("/domains", response_model=list[OrgDomainRead])
async def list_organization_domains(
    *,
    role: OrgUserRole,
    session: AsyncDBSession,
) -> list[OrgDomainRead]:
    """List domains assigned to the current organization."""
    if role.organization_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No organization context",
        )

    stmt = (
        select(OrganizationDomain)
        .where(OrganizationDomain.organization_id == role.organization_id)
        .order_by(
            OrganizationDomain.is_primary.desc(),
            OrganizationDomain.created_at.asc(),
            OrganizationDomain.id.asc(),
        )
    )
    result = await session.execute(stmt)
    domains = result.scalars().all()

    return [
        OrgDomainRead(
            id=domain.id,
            organization_id=domain.organization_id,
            domain=domain.domain,
            normalized_domain=domain.normalized_domain,
            is_primary=domain.is_primary,
            is_active=domain.is_active,
            verified_at=domain.verified_at,
            verification_method=domain.verification_method,
            created_at=domain.created_at,
            updated_at=domain.updated_at,
        )
        for domain in domains
    ]


@router.get("/members/me", response_model=OrgMemberRead)
async def get_current_org_member(
    *,
    role: OrgUserRole,
    session: AsyncDBSession,
) -> OrgMemberRead:
    """Get the current user's organization membership.

    Returns the organization membership details for the authenticated user,
    including their org role (member, admin, or owner).

    This endpoint doesn't require admin access - any authenticated org member
    can view their own membership details.
    """
    if role.user_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not authenticated",
        )
    if role.organization_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No organization context",
        )

    # Query user and membership directly (no admin access required)
    statement = (
        select(User, OrganizationMembership.role)
        .join(
            OrganizationMembership,
            OrganizationMembership.user_id == User.id,  # pyright: ignore[reportArgumentType]
        )
        .where(
            and_(
                User.id == role.user_id,  # pyright: ignore[reportArgumentType]
                OrganizationMembership.organization_id == role.organization_id,  # pyright: ignore[reportArgumentType]
            )
        )
    )
    result = await session.execute(statement)
    row = result.tuples().one_or_none()

    if row is not None:
        user, org_role = row
        return OrgMemberRead(
            user_id=user.id,
            first_name=user.first_name,
            last_name=user.last_name,
            email=user.email,
            role=org_role,
            is_active=user.is_active,
            is_verified=user.is_verified,
            last_login_at=user.last_login_at,
        )

    # No explicit membership found - check if user is a platform superuser
    # Superusers have implicit owner access to all organizations
    if role.is_platform_superuser:
        user_result = await session.execute(
            select(User).where(User.id == role.user_id)  # pyright: ignore[reportArgumentType]
        )
        user = user_result.scalar_one_or_none()
        if user is not None:
            return OrgMemberRead(
                user_id=user.id,
                first_name=user.first_name,
                last_name=user.last_name,
                email=user.email,
                role=OrgRole.OWNER,  # Superusers have implicit owner role
                is_active=user.is_active,
                is_verified=user.is_verified,
                last_login_at=user.last_login_at,
            )

    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail="User is not a member of this organization",
    )


@router.get("/members", response_model=list[OrgMemberRead])
async def list_org_members(
    *,
    role: OrgAdminRole,
    session: AsyncDBSession,
) -> list[OrgMemberRead]:
    service = OrgService(session, role=role)
    members = await service.list_members()
    return [
        OrgMemberRead(
            user_id=user.id,
            first_name=user.first_name,
            last_name=user.last_name,
            email=user.email,
            role=org_role,
            is_active=user.is_active,
            is_verified=user.is_verified,
            last_login_at=user.last_login_at,
        )
        for user, org_role in members
    ]


@router.delete("/members/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_org_member(
    *,
    role: OrgAdminRole,
    session: AsyncDBSession,
    user_id: UserID,
) -> None:
    service = OrgService(session, role=role)
    try:
        await service.delete_member(user_id)
    except NoResultFound as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="User not found"
        ) from e
    except IntegrityError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Action cannot be performed. Check if user is a superuser or has active sessions.",
        ) from e
    except TracecatAuthorizationError as e:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden"
        ) from e


@router.patch("/members/{user_id}", response_model=OrgMemberRead)
async def update_org_member(
    *,
    role: OrgAdminRole,
    session: AsyncDBSession,
    user_id: UserID,
    params: UserUpdate,
) -> OrgMemberRead:
    service = OrgService(session, role=role)
    try:
        user, org_role = await service.update_member(user_id, params)
        return OrgMemberRead(
            user_id=user.id,
            first_name=user.first_name,
            last_name=user.last_name,
            email=user.email,
            role=org_role,
            is_active=user.is_active,
            is_verified=user.is_verified,
            last_login_at=user.last_login_at,
        )
    except NoResultFound as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="User not found"
        ) from e
    except TracecatAuthorizationError as e:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden"
        ) from e


@router.get("/sessions", response_model=list[SessionRead])
async def list_sessions(
    *,
    role: OrgAdminRole,
    session: AsyncDBSession,
) -> list[SessionRead]:
    service = OrgService(session, role=role)
    return await service.list_sessions()


@router.delete("/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_session(
    *,
    role: OrgAdminRole,
    session: AsyncDBSession,
    session_id: SessionID,
) -> None:
    service = OrgService(session, role=role)
    try:
        await service.delete_session(session_id)
    except NoResultFound as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Session not found"
        ) from e


# === Invitations ===


@router.post(
    "/invitations",
    response_model=OrgInvitationRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_invitation(
    *,
    role: OrgAdminRole,
    session: AsyncDBSession,
    params: OrgInvitationCreate,
) -> OrgInvitationRead:
    """Create an invitation to join the organization."""
    service = OrgService(session, role=role)
    try:
        invitation = await service.create_invitation(
            email=params.email,
            role=params.role,
        )
    except TracecatAuthorizationError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e)) from e
    except TracecatValidationError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)
        ) from e
    except IntegrityError as e:
        # Race condition: another request created invitation for same email
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An invitation already exists for this email",
        ) from e
    return OrgInvitationRead(
        id=invitation.id,
        organization_id=invitation.organization_id,
        email=invitation.email,
        role=invitation.role,
        status=invitation.status,
        invited_by=invitation.invited_by,
        expires_at=invitation.expires_at,
        created_at=invitation.created_at,
        accepted_at=invitation.accepted_at,
    )


@router.get("/invitations", response_model=list[OrgInvitationRead])
async def list_invitations(
    *,
    role: OrgAdminRole,
    session: AsyncDBSession,
    invitation_status: InvitationStatus | None = Query(None, alias="status"),
) -> list[OrgInvitationRead]:
    """List invitations for the organization."""
    service = OrgService(session, role=role)
    invitations = await service.list_invitations(status=invitation_status)
    return [
        OrgInvitationRead(
            id=inv.id,
            organization_id=inv.organization_id,
            email=inv.email,
            role=inv.role,
            status=inv.status,
            invited_by=inv.invited_by,
            expires_at=inv.expires_at,
            created_at=inv.created_at,
            accepted_at=inv.accepted_at,
        )
        for inv in invitations
    ]


@router.delete("/invitations/{invitation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_invitation(
    *,
    role: OrgAdminRole,
    session: AsyncDBSession,
    invitation_id: UUID,
) -> None:
    """Revoke a pending invitation."""
    service = OrgService(session, role=role)
    try:
        await service.revoke_invitation(invitation_id)
    except NoResultFound as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Invitation not found"
        ) from e
    except TracecatAuthorizationError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e)) from e


@router.get("/invitations/{invitation_id}/token")
async def get_invitation_token(
    *,
    role: OrgAdminRole,
    session: AsyncDBSession,
    invitation_id: UUID,
) -> dict[str, str]:
    """Get the token for a specific invitation (admin only).

    This endpoint is used to generate shareable invitation links.
    """
    service = OrgService(session, role=role)
    try:
        invitation = await service.get_invitation(invitation_id)
        return {"token": invitation.token}
    except NoResultFound as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Invitation not found"
        ) from e


@router.post("/invitations/accept")
async def accept_invitation(
    *,
    role: AuthenticatedUserOnly,
    session: AsyncDBSession,
    params: OrgInvitationAccept,
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


@router.get("/invitations/pending/me", response_model=list[OrgPendingInvitationRead])
async def list_my_pending_invitations(
    *,
    role: AuthenticatedUserOnly,
    session: AsyncDBSession,
    user: Annotated[User, Depends(current_active_user)],
) -> list[OrgPendingInvitationRead]:
    """List pending, unexpired invitations for the authenticated user."""
    assert role.user_id is not None

    now = datetime.now(UTC)
    statement = (
        select(OrganizationInvitation, Organization, User)
        .join(
            Organization,
            Organization.id == OrganizationInvitation.organization_id,  # pyright: ignore[reportArgumentType]
        )
        .outerjoin(
            User,
            User.id == OrganizationInvitation.invited_by,  # pyright: ignore[reportArgumentType]
        )
        .where(
            func.lower(OrganizationInvitation.email) == user.email.lower(),
            OrganizationInvitation.status == InvitationStatus.PENDING,
            OrganizationInvitation.expires_at > now,
        )
        .order_by(OrganizationInvitation.created_at.desc())
    )
    result = await session.execute(statement)
    rows = result.tuples().all()

    pending_invitations: list[OrgPendingInvitationRead] = []
    for invitation, organization, inviter in rows:
        inviter_name, inviter_email = _get_user_display_name_and_email(inviter)

        pending_invitations.append(
            OrgPendingInvitationRead(
                token=invitation.token,
                organization_id=invitation.organization_id,
                organization_name=organization.name,
                inviter_name=inviter_name,
                inviter_email=inviter_email,
                role=invitation.role,
                expires_at=invitation.expires_at,
            )
        )
    return pending_invitations


@router.get("/invitations/token/{token}", response_model=OrgInvitationReadMinimal)
async def get_invitation_by_token(
    *,
    session: AsyncDBSession,
    token: str,
    user: OptionalUserDep = None,
) -> OrgInvitationReadMinimal:
    """Get minimal invitation details by token (public endpoint for UI).

    Returns organization name and inviter info for the acceptance page.
    If user is authenticated, also returns whether their email matches the invitation.
    """
    # Query invitation directly without OrgService since we don't have org context yet
    try:
        result = await session.execute(
            select(OrganizationInvitation).where(OrganizationInvitation.token == token)
        )
        invitation = result.scalar_one_or_none()
        if invitation is None:
            raise TracecatNotFoundError("Invitation not found")

        # Fetch organization name
        org_result = await session.execute(
            select(Organization).where(Organization.id == invitation.organization_id)
        )
        org = org_result.scalar_one()

        # Fetch inviter info if available
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

        return OrgInvitationReadMinimal(
            organization_id=invitation.organization_id,
            organization_name=org.name,
            inviter_name=inviter_name,
            inviter_email=inviter_email,
            role=invitation.role,
            status=invitation.status,
            expires_at=invitation.expires_at,
            email_matches=email_matches,
        )
    except TracecatNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
