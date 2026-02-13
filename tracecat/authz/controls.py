import asyncio
import functools
import inspect
import re
from collections.abc import Callable, Coroutine
from fnmatch import fnmatch
from typing import Any, Protocol, TypeVar, cast, runtime_checkable

from tracecat.auth.types import Role
from tracecat.contexts import ctx_role
from tracecat.exceptions import ScopeDeniedError
from tracecat.logger import logger

T = TypeVar("T", bound=Callable[..., Coroutine[Any, Any, Any] | Any])

# Regex for validating scope strings: lowercase alphanumeric with : _ . - and *
# Only * is allowed as wildcard, no ? or [] patterns
SCOPE_PATTERN = re.compile(r"^[a-z0-9:_.*-]+$")


def _attach_wrapped_signature(
    wrapper: Callable[..., Any], wrapped: Callable[..., Any]
) -> None:
    """Attach a concrete signature so FastAPI can resolve endpoint annotations."""
    try:
        signature = inspect.signature(wrapped, eval_str=True)
    except (NameError, TypeError, ValueError):
        signature = inspect.signature(wrapped)
    cast(Any, wrapper).__signature__ = signature


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


# =============================================================================
# Scope-based Authorization Decorator
# =============================================================================


def require_action_scope(action_key: str) -> None:
    """Check if the current user has permission to execute a specific action.

    This function checks the context scopes against the required action scope.
    The required scope is `action:{action_key}:execute`.

    Scope matching supports wildcards:
    - `action:*:execute` → any action (Admin)
    - `action:core.*:execute` → core actions (Editor)
    - `action:tools.okta.*:execute` → okta actions (custom role)
    - `action:tools.okta.list_users:execute` → specific action

    Args:
        action_key: The action key (e.g., "core.http_request", "tools.okta.list_users")

    Raises:
        ScopeDeniedError: If the user doesn't have permission to execute the action
    """
    role = ctx_role.get()
    if role is None:
        raise ScopeDeniedError(
            required_scopes=[f"action:{action_key}:execute"],
            missing_scopes=[f"action:{action_key}:execute"],
        )
    user_scopes = role.scopes

    # Platform superuser has "*" scope - bypass all checks
    if "*" in user_scopes:
        return

    required_scope = f"action:{action_key}:execute"

    if not has_scope(user_scopes, required_scope):
        logger.warning(
            "Action scope check failed",
            action_key=action_key,
            required_scope=required_scope,
        )
        raise ScopeDeniedError(
            required_scopes=[required_scope],
            missing_scopes=[required_scope],
        )

    logger.debug(
        "Action scope check passed",
        action_key=action_key,
        required_scope=required_scope,
    )


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

    def check_scopes(method_role: Role | None = None) -> None:
        # Empty required scopes means no restrictions
        if not required:
            return

        role = method_role or ctx_role.get()
        if role is None:
            raise ScopeDeniedError(
                required_scopes=list(required), missing_scopes=list(required)
            )

        user_scopes = role.scopes

        # For service-layer checks, allow legacy/internal service calls that pass a
        # role object without resolved scopes. Router-level checks still use ctx_role.
        if method_role is not None and not user_scopes:
            logger.debug("Skipping service scope check; role has no resolved scopes")
            return

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
                method_role = (
                    args[0].role if args and isinstance(args[0], HasRole) else None
                )
                check_scopes(method_role=method_role)
                return await fn(*args, **kwargs)

            _attach_wrapped_signature(async_wrapper, fn)
            return cast(T, async_wrapper)

        else:

            @functools.wraps(fn)
            def wrapper(*args, **kwargs):
                method_role = (
                    args[0].role if args and isinstance(args[0], HasRole) else None
                )
                check_scopes(method_role=method_role)
                return fn(*args, **kwargs)

            _attach_wrapped_signature(wrapper, fn)
            return cast(T, wrapper)

    return decorator
