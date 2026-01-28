"""Tracecat authn credentials."""

from __future__ import annotations

import secrets
import uuid
from contextlib import contextmanager
from functools import partial
from typing import Annotated, Any, Literal

from async_lru import alru_cache
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
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat import config
from tracecat.auth.executor_tokens import verify_executor_token
from tracecat.auth.schemas import UserRole
from tracecat.auth.types import AccessLevel, PlatformRole, Role
from tracecat.auth.users import (
    current_active_user,
    is_unprivileged,
    optional_current_active_user,
)
from tracecat.authz.enums import OrgRole, WorkspaceRole
from tracecat.authz.service import MembershipService, MembershipWithOrg
from tracecat.contexts import ctx_role
from tracecat.db.dependencies import AsyncDBSession
from tracecat.db.engine import get_async_session_context_manager
from tracecat.db.models import Organization, OrganizationMembership, User, Workspace
from tracecat.identifiers import InternalServiceID
from tracecat.logger import logger

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token", auto_error=False)
api_key_header_scheme = APIKeyHeader(name="x-tracecat-service-key", auto_error=False)

# Maximum number of memberships to cache per user to prevent memory exhaustion
MAX_CACHED_MEMBERSHIPS = 1000


@alru_cache(maxsize=10000)
async def _get_workspace_org_id(workspace_id: uuid.UUID) -> uuid.UUID | None:
    """Get organization_id for a workspace (cached).

    The workspace→organization mapping is immutable, so this can be cached
    indefinitely without TTL.
    """
    async with get_async_session_context_manager() as session:
        stmt = select(Workspace.organization_id).where(Workspace.id == workspace_id)
        result = await session.execute(stmt)
        return result.scalar_one_or_none()


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

ORG_OVERRIDE_COOKIE = "tracecat-org-id"


def get_role_from_user(
    user: User,
    organization_id: UUID4,
    workspace_id: UUID4 | None = None,
    workspace_role: WorkspaceRole | None = None,
    org_role: OrgRole | None = None,
    service_id: InternalServiceID = "tracecat-api",
) -> Role:
    # Superusers always get ADMIN access level
    access_level = (
        AccessLevel.ADMIN if user.is_superuser else USER_ROLE_TO_ACCESS_LEVEL[user.role]
    )
    if user.is_superuser:
        org_role = OrgRole.OWNER
    return Role(
        type="user",
        workspace_id=workspace_id,
        organization_id=organization_id,
        user_id=user.id,
        service_id=service_id,
        access_level=access_level,
        workspace_role=workspace_role,
        org_role=org_role,
        is_platform_superuser=user.is_superuser,
    )


def _get_bearer_token(request: Request) -> str | None:
    auth_header = request.headers.get("Authorization")
    if not auth_header:
        return None
    scheme, _, token = auth_header.partition(" ")
    if scheme.lower() != "bearer" or not token:
        return None
    return token


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
    expected_key = config.TRACECAT__SERVICE_KEY
    if not expected_key:
        raise KeyError("TRACECAT__SERVICE_KEY is not set")
    if not secrets.compare_digest(api_key, expected_key):
        logger.error("Could not validate service key")
        raise CREDENTIALS_EXCEPTION
    user_id = (
        uuid.UUID(uid)
        if (uid := request.headers.get("x-tracecat-role-user-id")) is not None
        else None
    )
    workspace_id = (
        uuid.UUID(ws_id)
        if (ws_id := request.headers.get("x-tracecat-role-workspace-id")) is not None
        else None
    )
    workspace_role = (
        WorkspaceRole(ws_role)
        if (ws_role := request.headers.get("x-tracecat-role-workspace-role"))
        is not None
        else None
    )
    org_role = (
        OrgRole(org_role_str)
        if (org_role_str := request.headers.get("x-tracecat-role-org-role")) is not None
        else None
    )
    service_id: InternalServiceID = service_role_id  # type: ignore[assignment]
    return Role(
        type="service",
        service_id=service_id,
        access_level=AccessLevel[request.headers["x-tracecat-role-access-level"]],
        user_id=user_id,
        workspace_id=workspace_id,
        workspace_role=workspace_role,
        org_role=org_role,
    )


@contextmanager
def TemporaryRole(
    type: Literal["user", "service"] = "service",
    user_id: uuid.UUID | None = None,
    service_id: InternalServiceID = "tracecat-service",
):
    """An async context manager to authenticate a user or service."""
    prev_role = ctx_role.get()
    temp_role = Role(type=type, user_id=user_id, service_id=service_id)
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
    allow_executor: bool = False,
    require_workspace: Literal["yes", "no", "optional"],
    min_access_level: AccessLevel | None = None,
    require_workspace_roles: WorkspaceRole | list[WorkspaceRole] | None = None,
) -> Role:
    if user and allow_user:
        if is_unprivileged(user) and workspace_id is not None:
            # Unprivileged user trying to target a workspace
            # Check if we have a cache available (from middleware)
            auth_cache = getattr(request.state, "auth_cache", None)

            membership_with_org: MembershipWithOrg | None = None
            if auth_cache:
                # Try to get from cache first
                cached_membership = auth_cache["memberships"].get(str(workspace_id))
                # Validate cached membership belongs to requesting user
                if (
                    cached_membership is not None
                    and cached_membership.user_id == user.id
                ):
                    # Convert cached Membership to MembershipWithOrg by fetching org_id
                    svc = MembershipService(session)
                    membership_with_org = await svc.get_membership(
                        workspace_id=workspace_id, user_id=user.id
                    )
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
                        # Find membership without caching - fetch with org_id
                        membership_with_org = await svc.get_membership(
                            workspace_id=workspace_id, user_id=user.id
                        )
                    else:
                        # Cache all memberships with user context
                        auth_cache["user_id"] = user.id
                        auth_cache["memberships"] = {
                            str(m.workspace_id): m for m in all_memberships
                        }
                        auth_cache["membership_checked"] = True
                        auth_cache["all_memberships"] = all_memberships

                        # Get the specific membership with org_id
                        membership_with_org = await svc.get_membership(
                            workspace_id=workspace_id, user_id=user.id
                        )

                        logger.debug(
                            "Loaded and cached all user memberships",
                            user_id=user.id,
                            workspace_count=len(all_memberships),
                            workspace_id=workspace_id,
                            found=membership_with_org is not None,
                        )
                elif auth_cache.get("user_id") != user.id:
                    # Cache belongs to different user - security fallback
                    logger.warning(
                        "Cache user mismatch, falling back to direct query",
                        cache_user_id=auth_cache.get("user_id"),
                        request_user_id=user.id,
                    )
                    svc = MembershipService(session)
                    membership_with_org = await svc.get_membership(
                        workspace_id=workspace_id, user_id=user.id
                    )
            else:
                # No cache available (e.g., in tests), fall back to direct query
                svc = MembershipService(session)
                membership_with_org = await svc.get_membership(
                    workspace_id=workspace_id, user_id=user.id
                )
                logger.debug(
                    "No cache available, using direct query",
                    user_id=user.id,
                    workspace_id=workspace_id,
                )

            if membership_with_org is None:
                logger.debug(
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
                and membership_with_org.membership.role not in require_workspace_roles
            ):
                logger.debug(
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
            workspace_role = membership_with_org.membership.role
            organization_id = membership_with_org.org_id
        else:
            # No workspace specified; determine org context
            workspace_role = None
            organization_id: UUID4 | None = None

            if user.is_superuser:
                # Superusers must explicitly select an organization via cookie
                org_override = request.cookies.get(ORG_OVERRIDE_COOKIE)
                if org_override:
                    try:
                        candidate_org_id = uuid.UUID(org_override)
                        # Validate that the organization actually exists
                        org_exists_stmt = select(Organization.id).where(
                            Organization.id == candidate_org_id
                        )
                        org_exists_result = await session.execute(org_exists_stmt)
                        if org_exists_result.scalar_one_or_none() is not None:
                            organization_id = candidate_org_id
                        else:
                            logger.warning(
                                "Organization from cookie does not exist",
                                user_id=user.id,
                                org_id=candidate_org_id,
                            )
                    except ValueError:
                        logger.warning(
                            "Invalid org override cookie format",
                            user_id=user.id,
                            org_override=org_override,
                        )
                if organization_id is None:
                    # No cookie, invalid cookie, or org doesn't exist - prompt for org selection
                    raise HTTPException(
                        status_code=status.HTTP_428_PRECONDITION_REQUIRED,
                        detail="Organization selection required",
                    )
            else:
                # Regular users: infer org from memberships
                svc = MembershipService(session)
                memberships_with_org = await svc.list_user_memberships_with_org(
                    user_id=user.id
                )
                org_ids = {m.org_id for m in memberships_with_org}
                if len(org_ids) == 0:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail="User has no organization memberships",
                    )
                elif len(org_ids) == 1:
                    organization_id = next(iter(org_ids))
                else:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail="Multiple organizations found. Provide workspace_id to select an organization.",
                    )

        # Fetch org-level role from OrganizationMembership
        org_role: OrgRole | None = None
        org_membership_stmt = select(OrganizationMembership.role).where(
            OrganizationMembership.user_id == user.id,
            OrganizationMembership.organization_id == organization_id,
        )
        org_membership_result = await session.execute(org_membership_stmt)
        if org_role_value := org_membership_result.scalar_one_or_none():
            org_role = org_role_value

        role = get_role_from_user(
            user,
            workspace_id=workspace_id,
            workspace_role=workspace_role,
            organization_id=organization_id,
            org_role=org_role,
        )
    elif api_key and allow_service:
        role = await _authenticate_service(request, api_key)
    elif allow_executor:
        token = _get_bearer_token(request)
        if not token:
            logger.info("Missing executor bearer token")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized"
            )
        try:
            token_payload = verify_executor_token(token)
        except ValueError as exc:
            logger.info("Invalid executor token", error=str(exc))
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized"
            ) from exc

        # Derive access_level from DB lookup on user_id (prevents privilege escalation)
        access_level = AccessLevel.BASIC  # Default for system/anonymous executions
        if token_payload.user_id is not None:
            stmt = select(User.role).where(User.id == token_payload.user_id)  # pyright: ignore[reportArgumentType]
            result = await session.execute(stmt)
            user_role = result.scalar_one_or_none()
            if user_role is not None:
                access_level = USER_ROLE_TO_ACCESS_LEVEL.get(
                    user_role, AccessLevel.BASIC
                )

        # Look up organization_id from workspace (cached, immutable relationship)
        organization_id = None
        if token_payload.workspace_id is not None:
            organization_id = await _get_workspace_org_id(token_payload.workspace_id)

        # Construct Role from token payload + derived access_level + organization_id
        role = Role(
            type="service",
            service_id="tracecat-executor",
            workspace_id=token_payload.workspace_id,
            organization_id=organization_id,
            user_id=token_payload.user_id,
            access_level=access_level,
        )

        if require_workspace == "yes":
            if role.workspace_id is None:
                logger.warning("Executor role missing workspace_id", role=role)
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden"
                )
            if workspace_id is not None and str(role.workspace_id) != str(workspace_id):
                logger.warning(
                    "Executor role workspace mismatch",
                    role_workspace_id=role.workspace_id,
                    request_workspace_id=workspace_id,
                )
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden"
                )
    else:
        logger.debug("Invalid authentication or authorization", user=user)
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
    allow_executor: bool = False,
    require_workspace: Literal["yes", "no", "optional"] = "yes",
    min_access_level: AccessLevel | None = None,
    workspace_id_in_path: bool = False,
    require_workspace_roles: WorkspaceRole | list[WorkspaceRole] | None = None,
) -> Any:
    """
    Factory for FastAPI dependency that enforces role-based access control.

    This function creates a dependency for authenticating and authorizing requests
    based on user/service role, workspace membership, and required access level.
    It ensures that the caller meets specified requirements and, if successful,
    returns a validated `Role` object describing their permissions.

    Args:
        allow_user (bool, optional): Allow authentication via user session/JWT. Defaults to True.
        allow_service (bool, optional): Allow authentication via service API key. Defaults to False.
        require_workspace (Literal["yes", "no", "optional"], optional): Specifies if a workspace ID is required.
            - "yes": Workspace ID is required.
            - "no": Workspace ID is not required.
            - "optional": Workspace ID may be omitted.
            Defaults to "yes".
        min_access_level (AccessLevel | None, optional): Minimum organization access level required for the request. Defaults to None.
        workspace_id_in_path (bool, optional): Whether to extract `workspace_id` from the path rather than the query string.
            Defaults to False.
        require_workspace_roles (WorkspaceRole | list[WorkspaceRole] | None, optional): Required workspace role(s)
            for user requests. Ignored for service API keys. Defaults to None.

    Returns:
        Any: A FastAPI dependency that yields a `Role` instance upon successful authentication and authorization.
        If validation fails, raises an HTTPException (401 or 403).

    Raises:
        ValueError: If invalid or conflicting options are provided (such as `workspace_id_in_path=True`
            with `require_workspace="optional"`).
        HTTPException: If authentication fails or the caller lacks required permissions.

    """
    if not any((allow_user, allow_service, require_workspace, allow_executor)):
        raise ValueError(
            "Must allow either user, service, executor, or require workspace"
        )

    # Executor-only auth: workspace_id comes from JWT, not query param
    is_executor_only = allow_executor and not allow_user and not allow_service
    if is_executor_only and require_workspace == "yes":

        async def role_dependency_executor_only(
            request: Request,
            session: AsyncDBSession,
        ) -> Role:
            return await _role_dependency(
                request=request,
                session=session,
                workspace_id=None,  # Comes from JWT
                user=None,
                api_key=None,
                allow_user=False,
                allow_service=False,
                allow_executor=True,
                min_access_level=min_access_level,
                require_workspace=require_workspace,
                require_workspace_roles=require_workspace_roles,
            )

        return Depends(role_dependency_executor_only)

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
                allow_executor=allow_executor,
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
                allow_executor=allow_executor,
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
                allow_executor=allow_executor,
                min_access_level=min_access_level,
                require_workspace=require_workspace,
                require_workspace_roles=require_workspace_roles,
            )

        return Depends(role_dependency_not_req_ws)
    else:
        raise ValueError(f"Invalid require_workspace value: {require_workspace}")


# --- Platform-level (Superuser) Authentication ---


async def _require_superuser(
    user: Annotated[User, Depends(current_active_user)],
) -> PlatformRole:
    """Require superuser access for platform admin operations.

    This dependency is used for /admin routes that require platform-level access.
    Superusers can manage organizations, platform settings, and platform-level
    registry sync operations.

    Returns a PlatformRole (not Role) to enforce type separation between
    platform and org-scoped operations.
    """
    if not user.is_superuser:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Forbidden",
        )
    return PlatformRole(
        type="user",
        user_id=user.id,
        access_level=AccessLevel.ADMIN,
        service_id="tracecat-api",
    )


SuperuserRole = Annotated[PlatformRole, Depends(_require_superuser)]
"""Dependency for platform admin (superuser) operations.

Returns a PlatformRole which is distinct from Role - platform operations
are not scoped to any organization or workspace.
"""


# --- Authenticated User Only (No Organization Context) ---


async def _authenticated_user_only(
    user: Annotated[User, Depends(current_active_user)],
) -> Role:
    """Dependency for endpoints requiring only an authenticated user.

    No organization context required. Use this for operations like:
    - Accepting invitations (user may not belong to any org yet)
    - User profile operations that don't require org context

    Sets ctx_role for consistency but organization_id will be None.
    """
    access_level = (
        AccessLevel.ADMIN if user.is_superuser else USER_ROLE_TO_ACCESS_LEVEL[user.role]
    )
    role = Role(
        type="user",
        user_id=user.id,
        access_level=access_level,
        service_id="tracecat-api",
        is_platform_superuser=user.is_superuser,
        # organization_id intentionally None - user may not belong to any org
    )
    ctx_role.set(role)
    return role


AuthenticatedUserOnly = Annotated[Role, Depends(_authenticated_user_only)]
"""Dependency for an authenticated user without organization context.

Use this for endpoints where the user is authenticated but may not
belong to any organization (e.g., accepting invitations).

Sets the `ctx_role` context variable.
"""
