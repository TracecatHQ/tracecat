import asyncio
import functools
from collections.abc import Callable, Coroutine
from typing import Any, Protocol, TypeVar, cast, runtime_checkable

from tracecat.auth.types import AccessLevel, Role
from tracecat.authz.enums import OrgRole, WorkspaceRole
from tracecat.exceptions import TracecatAuthorizationError
from tracecat.logger import logger

T = TypeVar("T", bound=Callable[..., Coroutine[Any, Any, Any] | Any])


@runtime_checkable
class HasRole(Protocol):
    """Protocol for services that have a role attribute."""

    role: Role


def require_access_level(level: AccessLevel) -> Callable[[T], T]:
    """Decorator that protects a `Service` method with a minimum access level requirement.

    If the caller does not have at least the required access level, a TracecatAuthorizationError is raised.
    """

    def check(self: HasRole):
        if not hasattr(self, "role"):
            raise AttributeError("Service must have a 'role' attribute")

        if not isinstance(self.role, Role):
            raise ValueError("Invalid role type")

        user_role = self.role
        if user_role.access_level < level:
            # Also grant ADMIN access if user is an org owner/admin
            # This allows org owners/admins to perform org-level admin operations
            # (like creating workspaces) without requiring platform admin privileges
            is_org_admin = user_role.org_role in (OrgRole.OWNER, OrgRole.ADMIN)
            if not (level == AccessLevel.ADMIN and is_org_admin):
                raise TracecatAuthorizationError(
                    f"User does not have required access level: {level.name}"
                )
        logger.debug("Access level ok", user_id=user_role.user_id, level=level.name)

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
