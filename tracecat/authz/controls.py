import asyncio
import functools
from collections.abc import Callable, Coroutine
from typing import Any, TypeVar, cast

from tracecat.logger import logger
from tracecat.types.auth import AccessLevel, Role
from tracecat.types.exceptions import TracecatAuthorizationError

T = TypeVar("T", bound=Callable[..., Coroutine[Any, Any, Any] | Any])


def require_access_level(level: AccessLevel) -> Callable[[T], T]:
    """Decorator that protects a `Service` method with a minimum access level requirement.

    If the caller does not have at least the required access level, a TracecatAuthorizationError is raised.
    """

    def decorator(fn: T) -> T:
        if asyncio.iscoroutinefunction(fn):

            @functools.wraps(fn)
            async def wrapper(self, *args, **kwargs):
                if not hasattr(self, "role"):
                    raise AttributeError("Service must have a 'role' attribute")

                if not isinstance(self.role, Role):
                    raise ValueError("Invalid role type")

                user_role = self.role
                if user_role.access_level < level:
                    raise TracecatAuthorizationError(
                        f"User does not have required access level: {level.name}"
                    )
                logger.debug(
                    "Access level ok", user_id=user_role.user_id, level=level.name
                )
                return await fn(self, *args, **kwargs)

        else:

            @functools.wraps(fn)
            def wrapper(self, *args, **kwargs):
                if not hasattr(self, "role"):
                    raise AttributeError("Service must have a 'role' attribute")

                if not isinstance(self.role, Role):
                    raise ValueError("Invalid role type")

                user_role = self.role
                if user_role.access_level < level:
                    raise TracecatAuthorizationError(
                        f"User does not have required access level: {level.name}"
                    )
                logger.debug(
                    "Access level ok", user_id=user_role.user_id, level=level.name
                )
                return fn(self, *args, **kwargs)

        return cast(T, wrapper)

    return decorator
