from fastapi import APIRouter, HTTPException, status
from sqlalchemy.exc import IntegrityError, NoResultFound

from tracecat.auth.credentials import RoleACL
from tracecat.auth.models import UserUpdate
from tracecat.db.dependencies import AsyncDBSession
from tracecat.identifiers import UserID
from tracecat.organization.models import OrgMemberRead, OrgRead
from tracecat.organization.service import OrgService
from tracecat.types.auth import AccessLevel, Role
from tracecat.types.exceptions import TracecatAuthorizationError

router = APIRouter(prefix="/organization", tags=["organization"])


@router.get("", response_model=OrgRead, include_in_schema=False)
async def get_organization(
    *,
    role: Role = RoleACL(allow_user=True, allow_service=False, require_workspace="no"),
):
    raise NotImplementedError


@router.get("/members", response_model=list[OrgMemberRead])
async def list_org_members(
    *,
    role: Role = RoleACL(
        allow_user=True,
        allow_service=False,
        require_workspace="no",
        min_access_level=AccessLevel.ADMIN,
    ),
    session: AsyncDBSession,
):
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
    role: Role = RoleACL(
        allow_user=True,
        allow_service=False,
        require_workspace="no",
        min_access_level=AccessLevel.ADMIN,
    ),
    session: AsyncDBSession,
    user_id: UserID,
):
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
    role: Role = RoleACL(
        allow_user=True,
        allow_service=False,
        require_workspace="no",
        min_access_level=AccessLevel.ADMIN,
    ),
    session: AsyncDBSession,
    user_id: UserID,
    params: UserUpdate,
):
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
