from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi_users import InvalidPasswordException
from pydantic import EmailStr
from sqlalchemy import select

from tracecat.auth.credentials import AuthenticatedUserOnly, RoleACL
from tracecat.auth.enums import CredentialChangeErrorCode
from tracecat.auth.schemas import (
    LoginMethodsRead,
    PasswordChange,
    UserRead,
    UserSearchRead,
    UserSelfUpdate,
    UserUpdate,
)
from tracecat.auth.types import Role
from tracecat.auth.users import (
    CredentialChangeError,
    PermissionsException,
    UserManagerDep,
    current_active_user,
    fastapi_users,
)
from tracecat.authz.controls import has_any_scope
from tracecat.db.dependencies import AsyncDBSession
from tracecat.db.models import OrganizationMembership, User

router = APIRouter(prefix="/users")

current_active_user_token = fastapi_users.authenticator.current_user_token(active=True)

CurrentUser = Annotated[User, Depends(current_active_user)]
CurrentUserToken = Annotated[tuple[User, str], Depends(current_active_user_token)]


def _credential_change_http_error(e: CredentialChangeError) -> HTTPException:
    status_code = (
        status.HTTP_403_FORBIDDEN
        if e.code is CredentialChangeErrorCode.PASSWORD_LOGIN_NOT_ALLOWED
        else status.HTTP_400_BAD_REQUEST
    )
    return HTTPException(
        status_code=status_code, detail={"code": e.code, "message": str(e)}
    )


@router.patch("/me", name="users:patch_current_user", tags=["users"])
async def patch_current_user(
    *,
    request: Request,
    _role: AuthenticatedUserOnly,
    user: CurrentUser,
    user_manager: UserManagerDep,
    params: UserSelfUpdate,
) -> UserRead:
    """Update the current user's profile.

    Replaces the fastapi-users route, which also accepted email and password
    without the current password.
    """
    update = UserUpdate.model_validate(params.model_dump(exclude_unset=True))
    try:
        updated = await user_manager.update(update, user, safe=True, request=request)
    except PermissionsException as e:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Operation not permitted"
        ) from e
    return UserRead.model_validate(updated)


@router.get("/me/login-methods", tags=["users"])
async def get_current_user_login_methods(
    *,
    _role: AuthenticatedUserOnly,
    user: CurrentUser,
    user_manager: UserManagerDep,
) -> LoginMethodsRead:
    """Report which sign-in credentials the current user can change."""
    return LoginMethodsRead(password=await user_manager.can_change_password(user))


@router.post("/me/password", tags=["users"], status_code=status.HTTP_204_NO_CONTENT)
async def change_current_user_password(
    *,
    _role: AuthenticatedUserOnly,
    user_token: CurrentUserToken,
    user_manager: UserManagerDep,
    params: PasswordChange,
) -> None:
    """Change the current user's password and sign out their other sessions."""
    user, token = user_token
    try:
        await user_manager.change_password(
            user,
            current_password=params.current_password.get_secret_value(),
            new_password=params.new_password.get_secret_value(),
            current_token=token,
        )
    except CredentialChangeError as e:
        raise _credential_change_http_error(e) from e
    except InvalidPasswordException as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=e.reason
        ) from e


@router.get("/search", tags=["users"])
async def search_user(
    *,
    role: Role = RoleACL(
        allow_user=True,
        allow_service=False,
        require_workspace="optional",
    ),
    email: EmailStr | None = Query(None),
    session: AsyncDBSession,
) -> UserSearchRead:
    """Find a member of the caller's organization by email.

    Users outside the caller's organization return 404, the same as an unknown
    email, so the route cannot be used to enumerate accounts. Platform admins
    are scoped the same way; platform-wide lookups go through ``/admin/users``.
    """
    # Platform admin or users with org/workspace member read scope
    if not (
        role.is_privileged
        or has_any_scope(
            role.scopes or frozenset(),
            {"org:member:read", "workspace:member:read"},
        )
    ):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")

    if not email:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Must provide a search query",
        )

    not_found = HTTPException(
        status_code=status.HTTP_404_NOT_FOUND, detail="Resource not found"
    )
    if role.organization_id is None:
        raise not_found

    statement = (
        select(User)
        .join(OrganizationMembership, OrganizationMembership.user_id == User.id)
        .where(
            User.email == email,  # pyright: ignore[reportArgumentType]
            OrganizationMembership.organization_id == role.organization_id,
        )
    )
    user = (await session.execute(statement)).scalar_one_or_none()
    if user is None:
        raise not_found
    return UserSearchRead.model_validate(user, from_attributes=True)
