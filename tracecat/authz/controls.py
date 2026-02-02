import asyncio
import functools
import re
import warnings
from collections.abc import Callable, Coroutine
from fnmatch import fnmatch
from typing import Any, Protocol, TypeVar, cast, runtime_checkable

from tracecat.auth.types import AccessLevel, Role
from tracecat.authz.enums import OrgRole, WorkspaceRole
from tracecat.contexts import ctx_role
from tracecat.exceptions import ScopeDeniedError, TracecatAuthorizationError
from tracecat.logger import logger

T = TypeVar("T", bound=Callable[..., Coroutine[Any, Any, Any] | Any])

# Regex for validating scope strings: lowercase alphanumeric with : _ . - and *
# Only * is allowed as wildcard, no ? or [] patterns
SCOPE_PATTERN = re.compile(r"^[a-z0-9:_.*-]+$")


def validate_scope_string(scope: str) -> bool:
    """Validate that a scope string follows the allowed format.

    Rules:
    - Lowercase alphanumeric characters
    - Allowed special characters: : _ . - *
    - Only * is allowed as wildcard (no ? or [] patterns)
    """
    return bool(SCOPE_PATTERN.match(scope))


def scope_matches(granted_scope: str, required_scope: str) -> bool:
    """Check if a granted scope (potentially with wildcards) matches a required scope.

    Uses fnmatch-style matching with only * as the wildcard character.
    * matches any sequence of characters.

    Args:
        granted_scope: A scope that was granted (may contain wildcards)
        required_scope: A scope that is required (should be exact, no wildcards)

    Returns:
        True if the granted scope matches the required scope

    Examples:
        scope_matches("workflow:*", "workflow:read") -> True
        scope_matches("workflow:read", "workflow:read") -> True
        scope_matches("action:core.*:execute", "action:core.http_request:execute") -> True
        scope_matches("action:*:execute", "action:tools.okta.list_users:execute") -> True
        scope_matches("*", "anything:here") -> True
    """
    if granted_scope == "*":
        # Global wildcard matches everything
        return True

    if "*" not in granted_scope:
        # No wildcard - exact match required
        return granted_scope == required_scope

    # Use fnmatch for wildcard matching (avoids regex backtracking issues)
    return fnmatch(required_scope, granted_scope)


def has_scope(user_scopes: frozenset[str], required_scope: str) -> bool:
    """Check if a user has a required scope.

    Args:
        user_scopes: The set of scopes granted to the user
        required_scope: The scope required for the operation

    Returns:
        True if any granted scope matches the required scope
    """
    return any(scope_matches(granted, required_scope) for granted in user_scopes)


def has_all_scopes(user_scopes: frozenset[str], required_scopes: set[str]) -> bool:
    """Check if a user has all required scopes.

    Args:
        user_scopes: The set of scopes granted to the user
        required_scopes: The scopes required for the operation

    Returns:
        True if all required scopes are satisfied
    """
    return all(has_scope(user_scopes, scope) for scope in required_scopes)


def has_any_scope(user_scopes: frozenset[str], required_scopes: set[str]) -> bool:
    """Check if a user has any of the required scopes.

    Args:
        user_scopes: The set of scopes granted to the user
        required_scopes: The scopes, any of which satisfies the requirement

    Returns:
        True if at least one required scope is satisfied
    """
    return any(has_scope(user_scopes, scope) for scope in required_scopes)


def get_missing_scopes(
    user_scopes: frozenset[str], required_scopes: set[str]
) -> set[str]:
    """Get the scopes that are required but not granted.

    Args:
        user_scopes: The set of scopes granted to the user
        required_scopes: The scopes required for the operation

    Returns:
        Set of scopes that are not satisfied
    """
    return {scope for scope in required_scopes if not has_scope(user_scopes, scope)}


@runtime_checkable
class HasRole(Protocol):
    """Protocol for services that have a role attribute."""

    role: Role


def require_access_level(level: AccessLevel) -> Callable[[T], T]:
    """Decorator that protects a `Service` method with a minimum access level requirement.

    If the caller does not have at least the required access level, a TracecatAuthorizationError is raised.

    .. deprecated::
        Use `@require_scope` instead. This decorator will be removed in a future version.
    """
    warnings.warn(
        "require_access_level is deprecated, use require_scope instead",
        DeprecationWarning,
        stacklevel=2,
    )

    def check(self: HasRole):
        if not hasattr(self, "role"):
            raise AttributeError("Service must have a 'role' attribute")

        if not isinstance(self.role, Role):
            raise ValueError("Invalid role type")

        user_role = self.role
        if user_role.access_level < level:
            raise TracecatAuthorizationError(
                f"User does not have required access level: {level.name}"
            )

    def decorator(fn: T) -> T:
        if asyncio.iscoroutinefunction(fn):

            @functools.wraps(fn)
            async def async_wrapper(self: HasRole, *args, **kwargs):
                check(self)
                return await fn(self, *args, **kwargs)

            return cast(T, async_wrapper)
        else:

            @functools.wraps(fn)
            def sync_wrapper(self: HasRole, *args, **kwargs):
                check(self)
                return fn(self, *args, **kwargs)

            return cast(T, sync_wrapper)

    return decorator


def require_org_role(*roles: OrgRole) -> Callable[[T], T]:
    """Decorator that protects a Service method with an org role requirement.

    If the caller does not have a required org role, a TracecatAuthorizationError is raised.
    Platform superusers bypass this check.
    """

    def check(self: HasRole):
        if not hasattr(self, "role"):
            raise AttributeError("Service must have a 'role' attribute")
        if not isinstance(self.role, Role):
            raise ValueError("Invalid role type")

        user_role = self.role
        # Platform superusers bypass org role checks
        if user_role.is_platform_superuser:
            logger.debug(
                "Platform superuser bypassing org role check",
                user_id=user_role.user_id,
            )
            return

        if user_role.org_role not in roles:
            raise TracecatAuthorizationError(
                f"User does not have required org role: {roles}"
            )
        logger.debug(
            "Org role check ok",
            user_id=user_role.user_id,
            org_role=user_role.org_role,
        )

    def decorator(fn: T) -> T:
        if asyncio.iscoroutinefunction(fn):

            @functools.wraps(fn)
            async def async_wrapper(self: HasRole, *args, **kwargs):
                check(self)
                return await fn(self, *args, **kwargs)

            return cast(T, async_wrapper)

        else:

            @functools.wraps(fn)
            def wrapper(self: HasRole, *args, **kwargs):
                check(self)
                return fn(self, *args, **kwargs)

            return cast(T, wrapper)

    return decorator


def require_workspace_role(*roles: WorkspaceRole) -> Callable[[T], T]:
    """Decorator that protects a `Service` method with a minimum access level requirement.

    If the caller does not have at least the required access level, a TracecatAuthorizationError is raised.
    """

    def check(self: HasRole):
        logger.debug("Checking workspace role", role=self.role)
        if not hasattr(self, "role"):
            raise AttributeError("Service must have a 'role' attribute")

        if not isinstance(self.role, Role):
            raise ValueError("Invalid role type")

        user_role = self.role
        # Platform admins and org owners/admins bypass workspace role checks
        if user_role.is_privileged:
            logger.info(
                "Privileged user bypassing workspace role check",
                user_id=user_role.user_id,
                workspace_role=user_role.workspace_role,
                is_org_admin=user_role.is_org_admin,
            )
            return

        if user_role.workspace_role not in roles:
            raise TracecatAuthorizationError(
                f"User does not have required workspace role: {roles}"
            )
        logger.debug(
            "Workspace role check ok",
            user_id=user_role.user_id,
            workspace_role=user_role.workspace_role,
        )

    def decorator(fn: T) -> T:
        if asyncio.iscoroutinefunction(fn):

            @functools.wraps(fn)
            async def async_wrapper(self, *args, **kwargs):
                check(self)
                return await fn(self, *args, **kwargs)

            return cast(T, async_wrapper)

        else:

            @functools.wraps(fn)
            def wrapper(self, *args, **kwargs):
                check(self)
                return fn(self, *args, **kwargs)

            return cast(T, wrapper)

    return decorator


# =============================================================================
# Scope-based Authorization Decorator
# =============================================================================


def require_scope(*scopes: str, require_all: bool = True) -> Callable[[T], T]:
    """Decorator that requires specific scopes to access an endpoint or method.

    This decorator checks the current request's scopes (from ctx_role.get().scopes)
    against the required scopes. Platform superusers with the "*" scope bypass all checks.

    Args:
        *scopes: The scope(s) required for access (e.g., "workflow:read", "org:member:invite")
        require_all: If True (default), all scopes must be present.
                    If False, any one of the scopes is sufficient.

    Raises:
        ScopeDeniedError: If the user doesn't have the required scope(s)

    Examples:
        # Single scope required
        @require_scope("workflow:create")
        async def create_workflow(...):
            ...

        # Multiple scopes, all required
        @require_scope("workflow:read", "workflow:execute")
        async def execute_workflow(...):
            ...

        # Multiple scopes, any one sufficient
        @require_scope("org:admin", "workspace:admin", require_all=False)
        async def admin_operation(...):
            ...
    """
    required = set(scopes)

    def check_scopes():
        # Empty required scopes means no restrictions
        if not required:
            return

        role = ctx_role.get()
        if role is None:
            raise ScopeDeniedError(
                required_scopes=list(required), missing_scopes=list(required)
            )

        user_scopes = role.scopes

        # Platform superuser has "*" scope - bypass all checks
        if "*" in user_scopes:
            return

        if require_all:
            missing = get_missing_scopes(user_scopes, required)
            if missing:
                logger.warning(
                    "Scope check failed - missing required scopes",
                    required_scopes=list(required),
                    missing_scopes=list(missing),
                )
                raise ScopeDeniedError(
                    required_scopes=list(required),
                    missing_scopes=list(missing),
                )
        else:
            if not has_any_scope(user_scopes, required):
                logger.warning(
                    "Scope check failed - none of required scopes present",
                    required_scopes=list(required),
                )
                raise ScopeDeniedError(
                    required_scopes=list(required),
                    missing_scopes=list(required),
                )

        logger.debug("Scope check passed", required_scopes=list(required))

    def decorator(fn: T) -> T:
        if asyncio.iscoroutinefunction(fn):

            @functools.wraps(fn)
            async def async_wrapper(*args, **kwargs):
                check_scopes()
                return await fn(*args, **kwargs)

            return cast(T, async_wrapper)

        else:

            @functools.wraps(fn)
            def wrapper(*args, **kwargs):
                check_scopes()
                return fn(*args, **kwargs)

            return cast(T, wrapper)

    return decorator
