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
from tracecat.authz.scopes import ORG_ROLE_SCOPES, PRESET_ROLE_SCOPES
from tracecat.authz.service import MembershipService, MembershipWithOrg
from tracecat.contexts import ctx_role
from tracecat.db.dependencies import AsyncDBSession
from tracecat.db.engine import get_async_session_context_manager
from tracecat.db.models import Organization, OrganizationMembership, User, Workspace
from tracecat.identifiers import InternalServiceID
from tracecat.logger import logger
from tracecat.organization.management import get_default_organization_id

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


def compute_effective_scopes(role: Role) -> frozenset[str]:
    """Compute the effective scopes for a role.

    Scope computation follows this hierarchy:
    1. Platform superusers get "*" (all scopes)
    2. Org OWNER/ADMIN get their org-level scopes (includes full workspace access)
    3. Org MEMBER gets base org scopes + workspace membership scopes (if in workspace)
    4. Service roles inherit scopes based on the user they're acting on behalf of

    For workspace-scoped requests:
    - Org OWNER/ADMIN: org-level scopes (they can access all workspaces)
    - Workspace members: workspace role scopes from PRESET_ROLE_SCOPES

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
        if not role.is_org_admin:
            scope_set |= PRESET_ROLE_SCOPES.get(role.workspace_role, set())

    # Note: Group-based scopes (from group_assignment table) will be added in PR 4
    # via RBACService.get_group_scopes()

    return frozenset(scope_set)


def get_role_from_user(
    user: User,
    organization_id: uuid.UUID,
    workspace_id: uuid.UUID | None = None,
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
    organization_id = (
        uuid.UUID(org_id)
        if (org_id := request.headers.get("x-tracecat-role-organization-id"))
        is not None
        else None
    )
    # Backward compatibility: derive org from workspace when older callers
    # don't propagate x-tracecat-role-organization-id yet.
    if organization_id is None and workspace_id is not None:
        organization_id = await _get_workspace_org_id(workspace_id)
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
    # Parse scopes from header if present (for inter-service calls)
    scopes: frozenset[str] = frozenset()
    if scopes_header := request.headers.get("x-tracecat-role-scopes"):
        scopes = frozenset(s.strip() for s in scopes_header.split(",") if s.strip())
    service_id: InternalServiceID = service_role_id  # type: ignore[assignment]
    return Role(
        type="service",
        service_id=service_id,
        access_level=AccessLevel[request.headers["x-tracecat-role-access-level"]],
        user_id=user_id,
        workspace_id=workspace_id,
        organization_id=organization_id,
        workspace_role=workspace_role,
        org_role=org_role,
        scopes=scopes,
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


# --- Helper Functions for Auth ---


async def _get_membership_with_cache(
    *,
    request: Request,
    session: AsyncSession,
    workspace_id: uuid.UUID,
    user: User,
) -> MembershipWithOrg:
    """Resolve workspace membership using cache when available.

    Uses request-scoped cache from middleware if present, otherwise falls back
    to direct database query.

    Raises:
        HTTPException(403): If user is not a member of the workspace.
    """
    membership_with_org: MembershipWithOrg | None = None
    auth_cache = getattr(request.state, "auth_cache", None)

    if auth_cache is not None:
        cached_membership = auth_cache["memberships"].get(str(workspace_id))
        # Validate cached membership belongs to requesting user
        if cached_membership is not None and cached_membership.user_id == user.id:
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
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")

    return membership_with_org


async def _resolve_org_for_superuser(
    request: Request,
    session: AsyncSession,
) -> uuid.UUID:
    """Resolve organization context for a superuser from cookie.

    Superusers must explicitly select an organization via the ORG_OVERRIDE_COOKIE.

    Raises:
        HTTPException(428): If no valid org cookie is set.
    """
    if not config.TRACECAT__EE_MULTI_TENANT:
        default_org_id = await get_default_organization_id(session)
        logger.debug(
            "Multi-tenant disabled; using default organization",
            organization_id=str(default_org_id),
        )
        return default_org_id

    if org_override := request.cookies.get(ORG_OVERRIDE_COOKIE):
        try:
            candidate_org_id = uuid.UUID(org_override)
            # Validate that the organization actually exists
            org_exists_stmt = select(Organization.id).where(
                Organization.id == candidate_org_id
            )
            org_exists_result = await session.execute(org_exists_stmt)
            if org_exists_result.scalar_one_or_none() is not None:
                return candidate_org_id
            logger.warning(
                "Organization from cookie does not exist",
                org_id=candidate_org_id,
            )
        except ValueError:
            logger.warning(
                "Invalid org override cookie format",
                org_override=org_override,
            )

    # No cookie, invalid cookie, or org doesn't exist - prompt for org selection
    raise HTTPException(
        status_code=status.HTTP_428_PRECONDITION_REQUIRED,
        detail="Organization selection required",
    )


async def _resolve_org_for_regular_user(
    session: AsyncSession,
    user: User,
) -> uuid.UUID:
    """Resolve organization context for a regular user from their memberships.

    Raises:
        HTTPException(400): If user has no org memberships or multiple orgs.
    """
    org_mem_stmt = select(OrganizationMembership.organization_id).where(
        OrganizationMembership.user_id == user.id
    )
    org_membership_result = await session.execute(org_mem_stmt)
    org_ids = {row[0] for row in org_membership_result.all()}

    if len(org_ids) == 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User has no organization memberships",
        )
    if len(org_ids) == 1:
        return next(iter(org_ids))
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="Multiple organizations found. Provide workspace_id to select an organization.",
    )


async def _get_org_role(
    session: AsyncSession,
    user_id: uuid.UUID,
    organization_id: uuid.UUID,
) -> OrgRole | None:
    """Fetch the user's organization-level role."""
    org_mem_stmt = select(OrganizationMembership).where(
        OrganizationMembership.user_id == user_id,
        OrganizationMembership.organization_id == organization_id,
    )
    org_membership_result = await session.execute(org_mem_stmt)
    if org_mem := org_membership_result.scalar_one_or_none():
        return org_mem.role
    return None


async def _authenticate_user(
    *,
    request: Request,
    session: AsyncSession,
    user: User,
    workspace_id: uuid.UUID | None,
    require_workspace_roles: list[WorkspaceRole] | None,
) -> Role:
    """Authenticate user, resolve workspace/org context, and return Role.

    Handles:
    1. Workspace membership validation (if workspace_id provided)
    2. Organization context resolution (superuser vs regular user)
    3. Org role fetching (org owners/admins bypass workspace membership checks)
    4. Role construction
    """
    workspace_role: WorkspaceRole | None = None
    organization_id: uuid.UUID
    org_role: OrgRole | None = None

    if is_unprivileged(user) and workspace_id is not None:
        # Unprivileged user targeting a workspace
        # First resolve org from workspace to check org-level permissions
        resolved_org_id = await _get_workspace_org_id(workspace_id)
        if resolved_org_id is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Workspace not found",
            )
        organization_id = resolved_org_id

        # Check if user is an org owner/admin - they can access all workspaces
        org_role = await _get_org_role(session, user.id, organization_id)
        is_org_admin = org_role in (
            OrgRole.OWNER,
            OrgRole.ADMIN,
        )  # Can't use Role.is_org_admin yet - Role not built

        if is_org_admin:
            # Org owners/admins bypass workspace membership checks
            logger.debug(
                "Org admin bypassing workspace membership check",
                user_id=user.id,
                workspace_id=workspace_id,
                org_role=org_role,
            )
        else:
            # Regular user - validate workspace membership
            membership_with_org = await _get_membership_with_cache(
                request=request,
                session=session,
                workspace_id=workspace_id,
                user=user,
            )

            # Check workspace role requirements
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

            workspace_role = membership_with_org.membership.role
    else:
        # No workspace specified or privileged user; determine org context
        if user.is_superuser:
            organization_id = await _resolve_org_for_superuser(request, session)
        else:
            organization_id = await _resolve_org_for_regular_user(session, user)

    # Fetch org-level role if not already fetched
    if org_role is None:
        org_role = await _get_org_role(session, user.id, organization_id)

    return get_role_from_user(
        user,
        workspace_id=workspace_id,
        workspace_role=workspace_role,
        organization_id=organization_id,
        org_role=org_role,
    )


async def _authenticate_executor(
    *,
    request: Request,
    session: AsyncSession,
    workspace_id: uuid.UUID | None,
    require_workspace: Literal["yes", "no", "optional"],
) -> Role:
    """Authenticate executor via JWT bearer token and return Role.

    Derives access_level from DB lookup on user_id to prevent privilege escalation.
    """
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
            access_level = USER_ROLE_TO_ACCESS_LEVEL.get(user_role, AccessLevel.BASIC)

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

    # Validate workspace requirements for executor
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

    return role


def _validate_role(
    role: Role,
    *,
    require_workspace: Literal["yes", "no", "optional"],
    min_access_level: AccessLevel | None,
    require_org_roles: list[OrgRole] | None = None,
) -> Role:
    """Validate structural requirements on the authenticated role.

    Raises:
        HTTPException(401): If role is None.
        HTTPException(403): If workspace required but missing, org role requirement not met,
            or access level insufficient.
    """

    if require_workspace == "yes" and role.workspace_id is None:
        logger.warning("User does not have access to this workspace", role=role)
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")

    # Check org role requirement
    if require_org_roles is not None:
        if role.org_role not in require_org_roles and not role.is_platform_superuser:
            logger.warning(
                "User does not have required org role",
                role=role,
                require_org_roles=require_org_roles,
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden"
            )

    # Compute effective scopes and create new role with scopes included
    scopes = compute_effective_scopes(role)
    logger.debug(
        "Computed effective scopes",
        scope_count=len(scopes),
    )

    return role.model_copy(update={"scopes": scopes})


# --- Main Auth Orchestrator ---


async def _role_dependency(
    *,
    request: Request,
    session: AsyncSession,
    workspace_id: uuid.UUID | None = None,
    user: User | None = None,
    api_key: str | None = None,
    allow_user: bool,
    allow_service: bool,
    allow_executor: bool = False,
    require_workspace: Literal["yes", "no", "optional"],
    min_access_level: AccessLevel | None = None,
    require_org_roles: list[OrgRole] | None = None,
    require_workspace_roles: WorkspaceRole | list[WorkspaceRole] | None = None,
) -> Role:
    """Main dependency that orchestrates authentication and authorization.

    Delegates to the appropriate auth handler based on credentials and allowed
    auth types, then validates the resulting role.
    """
    # Normalize require_workspace_roles to list
    ws_roles_list: list[WorkspaceRole] | None = None
    if isinstance(require_workspace_roles, WorkspaceRole):
        ws_roles_list = [require_workspace_roles]
    elif require_workspace_roles:
        ws_roles_list = list(require_workspace_roles)

    # Dispatch to appropriate auth handler
    role: Role | None = None

    if user and allow_user:
        role = await _authenticate_user(
            request=request,
            session=session,
            user=user,
            workspace_id=workspace_id,
            require_workspace_roles=ws_roles_list,
        )
    elif api_key and allow_service:
        role = await _authenticate_service(request, api_key)
    elif allow_executor:
        role = await _authenticate_executor(
            request=request,
            session=session,
            workspace_id=workspace_id,
            require_workspace=require_workspace,
        )
    else:
        logger.debug("Invalid authentication or authorization", user=user)
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")

    if role is None:
        logger.warning("Invalid role", role=role)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unauthorized",
        )
    # Validate structural requirements and set context
    role = _validate_role(
        role,
        require_workspace=require_workspace,
        min_access_level=min_access_level,
        require_org_roles=require_org_roles,
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
    require_org_roles: list[OrgRole] | None = None,
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
                require_org_roles=require_org_roles,
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
            workspace_id: uuid.UUID = GetWsDep(...),
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
                require_org_roles=require_org_roles,
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
            workspace_id: uuid.UUID | None = Query(None),
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
                require_org_roles=require_org_roles,
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
                require_org_roles=require_org_roles,
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
    scopes = compute_effective_scopes(role)
    role = role.model_copy(update={"scopes": scopes})
    ctx_role.set(role)
    return role


AuthenticatedUserOnly = Annotated[Role, Depends(_authenticated_user_only)]
"""Dependency for an authenticated user without organization context.

Use this for endpoints where the user is authenticated but may not
belong to any organization (e.g., accepting invitations).

Sets the `ctx_role` context variable.
"""
