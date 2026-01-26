from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy.exc import IntegrityError, NoResultFound

from tracecat.auth.credentials import RoleACL
from tracecat.auth.schemas import SessionRead, UserUpdate
from tracecat.auth.types import AccessLevel, Role
from tracecat.db.dependencies import AsyncDBSession
from tracecat.exceptions import (
    TracecatAuthorizationError,
    TracecatNotFoundError,
    TracecatValidationError,
)
from tracecat.identifiers import SessionID, UserID
from tracecat.invitations.enums import InvitationStatus
from tracecat.organization.schemas import (
    OrgInvitationAccept,
    OrgInvitationCreate,
    OrgInvitationRead,
    OrgInvitationReadMinimal,
    OrgMemberRead,
    OrgRead,
)
from tracecat.organization.service import OrgService

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
        min_access_level=AccessLevel.ADMIN,
    ),
]


@router.get("", response_model=OrgRead, include_in_schema=False)
async def get_organization(
    *,
    role: OrgUserRole,
) -> OrgRead:
    raise NotImplementedError


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
            role=user.role,
            is_active=user.is_active,
            is_superuser=user.is_superuser,
            is_verified=user.is_verified,
            last_login_at=user.last_login_at,
        )
        for user in members
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
        user = await service.update_member(user_id, params)
        return OrgMemberRead(
            user_id=user.id,
            first_name=user.first_name,
            last_name=user.last_name,
            email=user.email,
            role=user.role,
            is_active=user.is_active,
            is_superuser=user.is_superuser,
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
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)
        ) from e


@router.post("/invitations/accept")
async def accept_invitation(
    *,
    role: OrgUserRole,
    session: AsyncDBSession,
    params: OrgInvitationAccept,
) -> dict[str, str]:
    """Accept an invitation and join the organization."""
    service = OrgService(session, role=role)
    try:
        await service.accept_invitation(params.token)
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


@router.get("/invitations/token/{token}", response_model=OrgInvitationReadMinimal)
async def get_invitation_by_token(
    *,
    session: AsyncDBSession,
    token: str,
) -> OrgInvitationReadMinimal:
    """Get minimal invitation details by token (public endpoint for UI).

    Returns only essential fields to reduce information disclosure.
    """
    # Create a minimal role for unauthenticated access
    role = Role(
        type="service",
        service_id="tracecat-api",
        access_level=AccessLevel.BASIC,
    )
    service = OrgService(session, role=role)
    try:
        invitation = await service.get_invitation_by_token(token)
        return OrgInvitationReadMinimal(
            organization_id=invitation.organization_id,
            role=invitation.role,
            status=invitation.status,
            expires_at=invitation.expires_at,
        )
    except TracecatNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
