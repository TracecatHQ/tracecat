"""Tracecat authn credentials."""

from __future__ import annotations

import secrets
import uuid
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
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat import config
from tracecat.auth.executor_tokens import verify_executor_token
from tracecat.auth.schemas import UserRole
from tracecat.auth.types import AccessLevel, Role
from tracecat.auth.users import (
    current_active_user,
    is_unprivileged,
    optional_current_active_user,
)
from tracecat.authz.enums import OrgRole, WorkspaceRole
from tracecat.authz.scopes import ORG_ROLE_SCOPES, SYSTEM_ROLE_SCOPES
from tracecat.authz.service import MembershipService, MembershipWithOrg
from tracecat.contexts import ctx_role, ctx_scopes
from tracecat.db.dependencies import AsyncDBSession
from tracecat.db.models import Role as RoleModel
from tracecat.db.models import User, UserRoleAssignment
from tracecat.identifiers import InternalServiceID, OrganizationID, UserID, WorkspaceID
from tracecat.logger import logger

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

ORG_OVERRIDE_COOKIE = "tracecat-org-id"

# Mapping from system role slugs to WorkspaceRole/OrgRole enums
SLUG_TO_WORKSPACE_ROLE: dict[str, WorkspaceRole] = {
    "admin": WorkspaceRole.ADMIN,
    "editor": WorkspaceRole.EDITOR,
    "viewer": WorkspaceRole.VIEWER,
}

SLUG_TO_ORG_ROLE: dict[str, OrgRole] = {
    "owner": OrgRole.OWNER,
    "admin": OrgRole.ADMIN,
    "member": OrgRole.MEMBER,
}


async def get_user_workspace_role(
    session: AsyncSession,
    user_id: UserID,
    workspace_id: WorkspaceID,
) -> WorkspaceRole | None:
    """Look up a user's workspace role from UserRoleAssignment."""
    stmt = (
        select(RoleModel.slug)
        .join(UserRoleAssignment, UserRoleAssignment.role_id == RoleModel.id)
        .where(
            UserRoleAssignment.user_id == user_id,
            UserRoleAssignment.workspace_id == workspace_id,
        )
    )
    result = await session.execute(stmt)
    slug = result.scalar_one_or_none()
    if slug is None:
        return None
    return SLUG_TO_WORKSPACE_ROLE.get(slug)


async def get_user_org_role(
    session: AsyncSession,
    user_id: UserID,
    organization_id: OrganizationID,
) -> OrgRole | None:
    """Look up a user's org-level role from UserRoleAssignment (workspace_id=NULL)."""
    stmt = (
        select(RoleModel.slug)
        .join(UserRoleAssignment, UserRoleAssignment.role_id == RoleModel.id)
        .where(
            UserRoleAssignment.user_id == user_id,
            UserRoleAssignment.organization_id == organization_id,
            UserRoleAssignment.workspace_id.is_(None),
        )
    )
    result = await session.execute(stmt)
    slug = result.scalar_one_or_none()
    if slug is None:
        return None
    return SLUG_TO_ORG_ROLE.get(slug)


def compute_effective_scopes(role: Role) -> frozenset[str]:
    """Compute the effective scopes for a role.

    Scope computation follows this hierarchy:
    1. Platform superusers get "*" (all scopes)
    2. Org OWNER/ADMIN get their org-level scopes (includes full workspace access)
    3. Org MEMBER gets base org scopes + workspace membership scopes (if in workspace)
    4. Service roles inherit scopes based on the user they're acting on behalf of

    For workspace-scoped requests:
    - Org OWNER/ADMIN: org-level scopes (they can access all workspaces)
    - Workspace members: workspace role scopes from SYSTEM_ROLE_SCOPES

    Note: Group-based scopes will be added in PR 4 (RBAC Service & APIs).
    """
    if role.is_platform_superuser:
        return frozenset({"*"})

    scope_set: set[str] = set()

    # Add org-level scopes based on org role
    if role.org_role is not None:
        scope_set |= ORG_ROLE_SCOPES.get(role.org_role, set())

    # For workspace-scoped requests, add workspace role scopes
    # (only if not an org admin/owner, who already have full access via org scopes)
    if role.workspace_id and role.workspace_role:
        # Org admins/owners already have workspace scopes via their org role
        # Regular members need their workspace role scopes
        if role.org_role not in (OrgRole.OWNER, OrgRole.ADMIN):
            scope_set |= SYSTEM_ROLE_SCOPES.get(role.workspace_role, set())

    # Note: Group-based scopes (from group_assignment table) will be added in PR 4
    # via RBACService.get_group_scopes()

    return frozenset(scope_set)


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

            # Look up workspace role from RBAC tables
            workspace_role = await get_user_workspace_role(
                session, user.id, workspace_id
            )

            # 2. Check if they have the appropriate workspace role
            if isinstance(require_workspace_roles, WorkspaceRole):
                require_workspace_roles = [require_workspace_roles]
            if (
                require_workspace_roles
                and workspace_role not in require_workspace_roles
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
            organization_id = membership_with_org.org_id
        else:
            # No workspace specified; infer org from memberships when possible.
            workspace_role = None
            organization_id = config.TRACECAT__DEFAULT_ORG_ID
            override_applied = False
            org_override = request.cookies.get(ORG_OVERRIDE_COOKIE)
            if org_override and user.is_superuser:
                try:
                    organization_id = uuid.UUID(org_override)
                    override_applied = True
                except ValueError:
                    logger.warning(
                        "Invalid org override cookie, falling back to membership",
                        user_id=user.id,
                        org_override=org_override,
                    )

            if not override_applied:
                svc = MembershipService(session)
                memberships_with_org = await svc.list_user_memberships_with_org(
                    user_id=user.id
                )
                org_ids = {m.org_id for m in memberships_with_org}
                if len(org_ids) == 1:
                    organization_id = next(iter(org_ids))
                elif len(org_ids) > 1:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail="Multiple organizations found. Provide workspace_id to select an organization.",
                    )

        # Fetch org-level role from RBAC tables
        org_role = await get_user_org_role(session, user.id, organization_id)

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

        # Construct Role from token payload + derived access_level
        role = Role(
            type="service",
            service_id="tracecat-executor",
            workspace_id=token_payload.workspace_id,
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

    # Compute and set effective scopes
    scopes = compute_effective_scopes(role)
    ctx_scopes.set(scopes)
    logger.debug(
        "Computed effective scopes",
        user_id=role.user_id,
        org_role=role.org_role,
        workspace_role=role.workspace_role,
        is_superuser=role.is_platform_superuser,
        scope_count=len(scopes),
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
) -> Role:
    """Require superuser access for platform admin operations.

    This dependency is used for /admin routes that require platform-level access.
    Superusers can manage organizations, platform settings, and platform-level
    registry sync operations.
    """
    if not user.is_superuser:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Forbidden",
        )
    role = Role(
        type="user",
        user_id=user.id,
        access_level=AccessLevel.ADMIN,
        service_id="tracecat-api",
        # NOTE: Platform routes are not org/workspace-scoped. Role still requires
        # organization_id for backwards-compat in Phase 1.
        organization_id=config.TRACECAT__DEFAULT_ORG_ID,
        is_platform_superuser=True,
    )
    # Superusers get "*" scope (all access)
    ctx_scopes.set(frozenset({"*"}))
    ctx_role.set(role)
    return role


SuperuserRole = Annotated[Role, Depends(_require_superuser)]
