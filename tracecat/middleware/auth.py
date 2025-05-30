"""Authorization middleware for Tracecat.

This middleware runs once per request to cache workspace memberships,
avoiding redundant database queries in downstream handlers.
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
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        # Initialize authorization cache for this request
        # This will be used by _role_dependency to cache membership lookups
        request.state.auth_cache = {
            "memberships": {},  # workspace_id -> membership
            "membership_checked": False,  # Whether we've loaded all memberships
            "all_memberships": [],  # All memberships for the user (loaded once)
        }

        logger.debug(
            "Authorization cache initialized",
            path=request.url.path,
            method=request.method,
        )

        try:
            # Process the request
            response = await call_next(request)
            return response
        finally:
            # Clean up to avoid memory leaks
            if hasattr(request.state, "auth_cache"):
                delattr(request.state, "auth_cache")
