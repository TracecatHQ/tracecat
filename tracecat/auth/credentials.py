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
from tracecat.auth.users import is_unprivileged, optional_current_active_user
from tracecat.authz.models import WorkspaceRole
from tracecat.authz.service import MembershipService
from tracecat.contexts import ctx_role
from tracecat.db.dependencies import AsyncDBSession
from tracecat.db.schemas import User
from tracecat.identifiers import InternalServiceID
from tracecat.logger import logger
from tracecat.types.auth import AccessLevel, Role

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token", auto_error=False)
api_key_header_scheme = APIKeyHeader(name="x-tracecat-service-key", auto_error=False)

# Maximum number of memberships to cache per user to prevent memory exhaustion
MAX_CACHED_MEMBERSHIPS = 1000

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
    workspace_role: WorkspaceRole | None = None,
    service_id: InternalServiceID = "tracecat-api",
) -> Role:
    return Role(
        type="user",
        workspace_id=workspace_id,
        user_id=user.id,
        service_id=service_id,
        access_level=USER_ROLE_TO_ACCESS_LEVEL[user.role],
        workspace_role=workspace_role,
    )


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


@contextmanager
def TemporaryRole(
    type: Literal["user", "service"] = "service",
    user_id: str | None = None,
    service_id: str | None = None,
):
    """An async context manager to authenticate a user or service."""
    prev_role = ctx_role.get()
    temp_role = Role(type=type, workspace_id=user_id, service_id=service_id)  # type: ignore
    ctx_role.set(temp_role)
    try:
        yield temp_role
    finally:
        ctx_role.set(prev_role)


OptionalUserDep = Annotated[User | None, Depends(optional_current_active_user)]
OptionalApiKeyDep = Annotated[str | None, Security(api_key_header_scheme)]


async def _role_dependency(
    *,
    request: Request,
    session: AsyncSession,
    workspace_id: UUID4 | None = None,
    user: User | None = None,
    api_key: str | None = None,
    allow_user: bool,
    allow_service: bool,
    require_workspace: Literal["yes", "no", "optional"],
    min_access_level: AccessLevel | None = None,
    require_workspace_roles: WorkspaceRole | list[WorkspaceRole] | None = None,
) -> Role:
    if user and allow_user:
        if is_unprivileged(user) and workspace_id is not None:
            # Unprivileged user trying to target a workspace
            # Check if we have a cache available (from middleware)
            auth_cache = getattr(request.state, "auth_cache", None)

            membership = None
            if auth_cache:
                # Try to get from cache first
                cached_membership = auth_cache["memberships"].get(str(workspace_id))
                # Validate cached membership belongs to requesting user
                if (
                    cached_membership is not None
                    and cached_membership.user_id == user.id
                ):
                    membership = cached_membership
                    logger.debug(
                        "Using cached membership",
                        user_id=user.id,
                        workspace_id=workspace_id,
                        cached=True,
                    )
                elif not auth_cache["membership_checked"]:
                    # Load all memberships once if not already done
                    svc = MembershipService(session)
                    all_memberships = await svc.list_user_memberships(user_id=user.id)

                    # Check cache size limit to prevent memory exhaustion
                    if len(all_memberships) > MAX_CACHED_MEMBERSHIPS:
                        logger.warning(
                            "User has excessive memberships, caching disabled for security",
                            user_id=user.id,
                            membership_count=len(all_memberships),
                            max_allowed=MAX_CACHED_MEMBERSHIPS,
                        )
                        # Find membership without caching
                        membership = next(
                            (
                                m
                                for m in all_memberships
                                if m.workspace_id == workspace_id
                            ),
                            None,
                        )
                    else:
                        # Cache all memberships with user context
                        auth_cache["user_id"] = user.id
                        auth_cache["memberships"] = {
                            str(m.workspace_id): m for m in all_memberships
                        }
                        auth_cache["membership_checked"] = True
                        auth_cache["all_memberships"] = all_memberships

                        # Get the specific membership
                        membership = auth_cache["memberships"].get(str(workspace_id))

                        logger.debug(
                            "Loaded and cached all user memberships",
                            user_id=user.id,
                            workspace_count=len(all_memberships),
                            workspace_id=workspace_id,
                            found=membership is not None,
                        )
                elif auth_cache.get("user_id") != user.id:
                    # Cache belongs to different user - security fallback
                    logger.warning(
                        "Cache user mismatch, falling back to direct query",
                        cache_user_id=auth_cache.get("user_id"),
                        request_user_id=user.id,
                    )
                    svc = MembershipService(session)
                    membership = await svc.get_membership(
                        workspace_id=workspace_id, user_id=user.id
                    )
            else:
                # No cache available (e.g., in tests), fall back to direct query
                svc = MembershipService(session)
                membership = await svc.get_membership(
                    workspace_id=workspace_id, user_id=user.id
                )
                logger.debug(
                    "No cache available, using direct query",
                    user_id=user.id,
                    workspace_id=workspace_id,
                )

            if membership is None:
                logger.warning(
                    "User is not a member of this workspace",
                    user=user,
                    workspace_id=workspace_id,
                )
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden"
                )
            # 2. Check if they have the appropriate workspace role
            if isinstance(require_workspace_roles, WorkspaceRole):
                require_workspace_roles = [require_workspace_roles]
            if (
                require_workspace_roles
                and membership.role not in require_workspace_roles
            ):
                logger.warning(
                    "User does not have the appropriate workspace role",
                    user=user,
                    workspace_id=workspace_id,
                    role=require_workspace_roles,
                )
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="You cannot perform this operation",
                )
            # User has appropriate workspace role
            workspace_role = membership.role
        else:
            # Privileged user doesn't need workspace role verification
            workspace_role = None

        role = get_role_from_user(
            user, workspace_id=workspace_id, workspace_role=workspace_role
        )
    elif api_key and allow_service:
        role = await _authenticate_service(request, api_key)
    else:
        logger.warning("Invalid authentication or authorization", user=user)
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")

    # Structural checks
    if role is None:
        logger.warning("Invalid role", role=role)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unauthorized",
        )

    if require_workspace == "yes" and role.workspace_id is None:
        logger.warning("User does not have access to this workspace", role=role)
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")

    # TODO(security): If min_access_level is not set, we should require max privilege by default
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


def RoleACL(
    *,
    allow_user: bool = True,
    allow_service: bool = False,
    require_workspace: Literal["yes", "no", "optional"] = "yes",
    min_access_level: AccessLevel | None = None,
    workspace_id_in_path: bool = False,
    require_workspace_roles: WorkspaceRole | list[WorkspaceRole] | None = None,
) -> Any:
    """
    Check the user or service against the authentication requirements and return a role.
    Returns the correct FastAPI auth dependency.
    """
    if not any((allow_user, allow_service, require_workspace)):
        raise ValueError("Must allow either user, service, or require workspace")

    if require_workspace == "yes":
        GetWsDep = Path if workspace_id_in_path else Query

        # Required workspace ID
        async def role_dependency_req_ws(
            request: Request,
            session: AsyncDBSession,
            workspace_id: UUID4 = GetWsDep(...),
            user: OptionalUserDep = None,
            api_key: OptionalApiKeyDep = None,
        ) -> Role:
            return await _role_dependency(
                request=request,
                session=session,
                workspace_id=workspace_id,
                user=user,
                api_key=api_key,
                allow_user=allow_user,
                allow_service=allow_service,
                min_access_level=min_access_level,
                require_workspace=require_workspace,
                require_workspace_roles=require_workspace_roles,
            )

        return Depends(role_dependency_req_ws)

    elif require_workspace == "optional":
        if workspace_id_in_path:
            raise ValueError(
                "workspace_id_in_path is not allowed with optional workspace"
            )

        async def role_dependency_opt_ws(
            request: Request,
            session: AsyncDBSession,
            workspace_id: UUID4 | None = Query(None),
            user: OptionalUserDep = None,
            api_key: OptionalApiKeyDep = None,
        ) -> Role:
            return await _role_dependency(
                request=request,
                session=session,
                workspace_id=workspace_id,
                user=user,
                api_key=api_key,
                allow_user=allow_user,
                allow_service=allow_service,
                min_access_level=min_access_level,
                require_workspace=require_workspace,
                require_workspace_roles=require_workspace_roles,
            )

        return Depends(role_dependency_opt_ws)
    elif require_workspace == "no":
        if workspace_id_in_path:
            raise ValueError("workspace_id_in_path is not allowed with no workspace")

        async def role_dependency_not_req_ws(
            request: Request,
            session: AsyncDBSession,
            user: OptionalUserDep = None,
            api_key: OptionalApiKeyDep = None,
        ) -> Role:
            return await _role_dependency(
                request=request,
                session=session,
                user=user,
                api_key=api_key,
                allow_user=allow_user,
                allow_service=allow_service,
                min_access_level=min_access_level,
                require_workspace=require_workspace,
                require_workspace_roles=require_workspace_roles,
            )

        return Depends(role_dependency_not_req_ws)
    else:
        raise ValueError(f"Invalid require_workspace value: {require_workspace}")
