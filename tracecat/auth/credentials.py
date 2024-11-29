"""Tracecat authn credentials."""

from __future__ import annotations

import os
from contextlib import contextmanager
from functools import partial
from typing import Annotated, Any, Literal

from fastapi import (
    Depends,
    HTTPException,
    Path,
    Query,
    Request,
    Security,
    status,
)
from fastapi.security import APIKeyHeader, OAuth2PasswordBearer
from pydantic import UUID4
from sqlmodel.ext.asyncio.session import AsyncSession

from tracecat import config
from tracecat.auth.models import UserRole
from tracecat.auth.users import (
    current_active_user,
    is_unprivileged,
    optional_current_active_user,
)
from tracecat.authz.service import AuthorizationService
from tracecat.contexts import ctx_role
from tracecat.db.dependencies import AsyncDBSession
from tracecat.db.engine import get_async_session
from tracecat.db.schemas import User
from tracecat.identifiers import InternalServiceID
from tracecat.logger import logger
from tracecat.types.auth import AccessLevel, Role

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token", auto_error=False)
api_key_header_scheme = APIKeyHeader(name="x-tracecat-service-key", auto_error=False)


CREDENTIALS_EXCEPTION = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Could not validate credentials",
    headers={"WWW-Authenticate": "Cookie"},
)

HTTP_EXC = partial(
    lambda msg: HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=msg or "Could not validate credentials",
        headers={"WWW-Authenticate": "Cookie"},
    )
)


USER_ROLE_TO_ACCESS_LEVEL = {
    UserRole.ADMIN: AccessLevel.ADMIN,
    UserRole.BASIC: AccessLevel.BASIC,
}


def get_role_from_user(
    user: User,
    workspace_id: UUID4 | None = None,
    service_id: InternalServiceID = "tracecat-api",
) -> Role:
    return Role(
        type="user",
        workspace_id=workspace_id,
        user_id=user.id,
        service_id=service_id,
        access_level=USER_ROLE_TO_ACCESS_LEVEL[user.role],
    )


def authenticate_user_access_level(access_level: AccessLevel) -> Any:
    """Returns a FastAPI dependency that asserts that the user has at least
    the provided access level."""

    # XXX: There may be a use case to use `current_admin_user` here.
    async def dependency(
        user: Annotated[User, Depends(current_active_user)],
    ) -> Role:
        """Authenticate a user with access levels and return a `User` object."""
        user_access_level = USER_ROLE_TO_ACCESS_LEVEL[user.role]
        if user_access_level < access_level:
            logger.warning(
                "User does not have the appropriate access level",
                user_id=user.id,
                access_level=user_access_level,
                required_access_level=access_level,
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden"
            )
        role = get_role_from_user(user)
        ctx_role.set(role)
        return role

    return dependency


async def authenticate_user_for_workspace(
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
    workspace_id: UUID4 = Query(...),
) -> Role | None:
    """Authenticate a user for a workspace passed in as a query parameter.

    `ctx_role` ContextVar is set here.
    """
    return await _authenticate_user_for_workspace(user, session, workspace_id)


async def _authenticate_user_for_workspace(
    user: User, session: AsyncSession, workspace_id: UUID4
) -> Role:
    """Authenticate a user for a workspace.

    `ctx_role` ContextVar is set here.
    """
    if is_unprivileged(user):
        # Check if unprivileged user is a member of the workspace
        authz_service = AuthorizationService(session)
        if not await authz_service.user_is_workspace_member(
            user_id=user.id, workspace_id=workspace_id
        ):
            logger.warning(
                "User is not a member of this workspace",
                user_id=user.id,
                workspace_id=workspace_id,
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden"
            )
    role = get_role_from_user(user, workspace_id=workspace_id)
    ctx_role.set(role)
    return role


async def authenticate_optional_user_for_workspace(
    user: Annotated[User | None, Depends(optional_current_active_user)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
    workspace_id: UUID4 = Query(...),
) -> Role | None:
    """Authenticate a user for a workspace.

    If no user available, return None.
    If a user is available, `ctx_role` ContextVar is set here.
    """
    if not user:
        return None
    return await _authenticate_user_for_workspace(user, session, workspace_id)


async def _authenticate_service(
    request: Request, api_key: str | None = None
) -> Role | None:
    if not api_key:
        return None

    service_role_id = request.headers.get("x-tracecat-role-service-id")
    if service_role_id is None:
        msg = "Missing x-tracecat-role-service-id header"
        logger.error(msg)
        raise HTTP_EXC(msg)
    if service_role_id not in config.TRACECAT__SERVICE_ROLES_WHITELIST:
        msg = f"x-tracecat-role-service-id {service_role_id!r} invalid or not allowed"
        logger.error(msg)
        raise HTTP_EXC(msg)
    if api_key != os.environ["TRACECAT__SERVICE_KEY"]:
        logger.error("Could not validate service key")
        raise CREDENTIALS_EXCEPTION
    role_params = {
        "type": "service",
        "service_id": service_role_id,
        "access_level": AccessLevel[request.headers["x-tracecat-role-access-level"]],
    }
    if (user_id := request.headers.get("x-tracecat-role-user-id")) is not None:
        role_params["user_id"] = user_id
    if (ws_id := request.headers.get("x-tracecat-role-workspace-id")) is not None:
        role_params["workspace_id"] = ws_id
    return Role(**role_params)


async def authenticate_service(
    request: Request,
    api_key: Annotated[str | None, Security(api_key_header_scheme)] = None,
) -> Role:
    """Authenticate a service using an API key.

    `ctx_role` ContextVar is set here.
    """
    role = await _authenticate_service(request, api_key)
    if not role:
        raise HTTP_EXC("Could not validate credentials")
    logger.debug("Authenticated service", role=role)
    ctx_role.set(role)
    return role


async def authenticate_user_or_service_for_workspace(
    request: Request,
    role_from_user: Annotated[
        Role | None, Depends(authenticate_optional_user_for_workspace)
    ] = None,
    api_key: Annotated[str | None, Security(api_key_header_scheme)] = None,
) -> Role:
    """Authenticate a user or service and return the role.

    Note: Don't have to set the session context here,
    we've already done that in the user/service checks."""
    if role_from_user:
        logger.trace("User authentication")
        return role_from_user
    if api_key:
        logger.trace("Service authentication")
        return await authenticate_service(request, api_key)
    logger.error("Could not validate credentials")
    raise HTTP_EXC("Could not validate credentials")


@contextmanager
def TemporaryRole(
    type: Literal["user", "service"] = "service",
    user_id: str | None = None,
    service_id: str | None = None,
):
    """An async context manager to authenticate a user or service."""
    prev_role = ctx_role.get()
    temp_role = Role(type=type, workspace_id=user_id, service_id=service_id)
    ctx_role.set(temp_role)
    try:
        yield temp_role
    finally:
        ctx_role.set(prev_role)


OptionalUserDep = Annotated[User | None, Depends(optional_current_active_user)]
OptionalApiKeyDep = Annotated[str | None, Security(api_key_header_scheme)]


def RoleACL(
    *,
    allow_user: bool = True,
    allow_service: bool = False,
    require_workspace: bool = True,
    min_access_level: AccessLevel | None = None,
    workspace_id_in_path: bool = False,
) -> Any:
    """
    Check the user or service against the authentication requirements and return a role.
    Returns the correct FastAPI auth dependency.
    """
    if not any((allow_user, allow_service, require_workspace)):
        raise ValueError("Must allow either user, service, or require workspace")

    async def role_dependency(
        request: Request,
        session: AsyncSession,
        workspace_id: UUID4 | None = None,
        user: User | None = None,
        api_key: str | None = None,
    ) -> Role:
        if user and allow_user:
            role = get_role_from_user(user, workspace_id=workspace_id)
            if is_unprivileged(user) and workspace_id is not None:
                # Check if unprivileged user is a member of the workspace
                authz_service = AuthorizationService(session)
                if not await authz_service.user_is_workspace_member(
                    user_id=user.id, workspace_id=workspace_id
                ):
                    logger.warning(
                        "User is not a member of this workspace",
                        role=role,
                        workspace_id=workspace_id,
                    )
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden"
                    )
        elif api_key and allow_service:
            role = await _authenticate_service(request, api_key)
        else:
            logger.warning("Invalid authentication or authorization", user=user)
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden"
            )

        if role is None:
            logger.warning("Invalid role", role=role)
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Unauthorized",
            )

        if require_workspace and role.workspace_id is None:
            logger.warning("User does not have access to this workspace", role=role)
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden"
            )

        if min_access_level is not None:
            if role.access_level < min_access_level:
                logger.warning(
                    "User does not have the appropriate access level",
                    role=role,
                    min_access_level=min_access_level,
                )
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden"
                )
        ctx_role.set(role)
        return role

    if require_workspace:
        GetWsDep = Path if workspace_id_in_path else Query

        async def role_dependency_req_ws(
            request: Request,
            session: AsyncDBSession,
            workspace_id: UUID4 = GetWsDep(...),
            user: OptionalUserDep = None,
            api_key: OptionalApiKeyDep = None,
        ) -> Role:
            return await role_dependency(request, session, workspace_id, user, api_key)

        return Depends(role_dependency_req_ws)

    else:
        if workspace_id_in_path:
            raise ValueError(
                "workspace_id_in_path is not allowed without require_workspace"
            )

        async def role_dependency_opt_ws(
            request: Request,
            session: AsyncDBSession,
            workspace_id: UUID4 | None = Query(None),
            user: OptionalUserDep = None,
            api_key: OptionalApiKeyDep = None,
        ) -> Role:
            return await role_dependency(request, session, workspace_id, user, api_key)

        return Depends(role_dependency_opt_ws)
