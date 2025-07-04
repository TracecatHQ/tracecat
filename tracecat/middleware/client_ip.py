"""Client IP extraction middleware for FastAPI.

This middleware extracts the real client IP from proxy headers and attaches it to the request state.
For local MinIO (default), IP checking is disabled.
For production S3, extracts real client IP from standard proxy headers.
"""

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

from tracecat.config import TRACECAT__DISABLE_PRESIGNED_URL_IP_CHECKING


class ClientIPMiddleware(BaseHTTPMiddleware):
    """Middleware to extract and store client IP in request state."""

    async def dispatch(self, request: Request, call_next) -> Response:
        """Extract client IP and attach to request.state.client_ip."""

        # Extract client IP based on configuration
        if TRACECAT__DISABLE_PRESIGNED_URL_IP_CHECKING:
            # For local MinIO, disable IP checking
            client_ip = None
        else:
            # For production S3, extract real client IP
            client_ip = self._extract_client_ip(request)

        # Attach to request state for use in routes
        request.state.client_ip = client_ip

        # Continue processing
        response = await call_next(request)
        return response

    def _extract_client_ip(self, request: Request) -> str | None:
        """Extract client IP from proxy headers.

        Handles the common proxy header patterns:
        - X-Forwarded-For (most load balancers)
        - X-Real-IP (nginx, some others)
        - Direct connection fallback
        """
        # Most load balancers use X-Forwarded-For
        if forwarded := request.headers.get("x-forwarded-for"):
            return forwarded.split(",")[0].strip()

        # Some use X-Real-IP
        if real_ip := request.headers.get("x-real-ip"):
            return real_ip.strip()

        # Fallback to direct connection
        return request.client.host if request.client else None
