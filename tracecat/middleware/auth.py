"""Authorization middleware for Tracecat.

This middleware runs once per request to cache workspace memberships,
avoiding redundant database queries in downstream handlers.

Security Considerations:
- Cache is request-scoped and cleaned up after each request
- Cache validates user ID to prevent cross-user data leakage
- Cache has size limits to prevent memory exhaustion attacks
- Cache is only used for the current request's user
"""

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

from tracecat.logger import logger


class AuthorizationCacheMiddleware(BaseHTTPMiddleware):
    """Middleware that provides request-scoped caching for authorization data.

    This middleware initializes a cache on each request that can be used by
    the authorization system to avoid redundant database queries.

    The actual authorization logic remains in the dependency injection system,
    but can now use this cache to store/retrieve membership data.

    Security Features:
    - Cache is bound to a specific user ID to prevent cross-user contamination
    - Cache has size limits (see MAX_CACHED_MEMBERSHIPS in auth.credentials)
    - Cache is automatically cleaned up after each request
    - Cache validates membership ownership before returning cached data
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        """Process request with authorization cache."""
        # Initialize authorization cache for this request
        request.state.auth_cache = {
            "memberships": {},  # workspace_id -> membership
            "membership_checked": False,  # Whether we've loaded all memberships
            "all_memberships": [],  # All memberships for the user (loaded once)
            "user_id": None,  # The user ID this cache is for
        }

        logger.debug(
            "Authorization cache initialized",
            path=request.url.path,
            method=request.method,
        )

        try:
            response = await call_next(request)

            # Log cache usage for security auditing
            auth_cache = getattr(request.state, "auth_cache", {})
            if auth_cache.get("membership_checked"):
                logger.info(
                    "Authorization cache usage",
                    path=request.url.path,
                    user_id=auth_cache.get("user_id"),
                    memberships_cached=len(auth_cache.get("memberships", {})),
                    cache_used=True,
                )

            return response
        finally:
            # Clean up to prevent memory leaks and data persistence
            if hasattr(request.state, "auth_cache"):
                delattr(request.state, "auth_cache")
