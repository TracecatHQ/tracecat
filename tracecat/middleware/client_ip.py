"""Client IP extraction middleware for FastAPI.

This middleware extracts the real client IP from proxy headers and attaches it to the request state.
For local MinIO (default), IP checking is disabled.
For production S3, extracts real client IP from standard proxy headers.
"""

from fastapi import Request
from starlette.types import ASGIApp, Receive, Scope, Send

from tracecat.config import TRACECAT__DISABLE_PRESIGNED_URL_IP_CHECKING


class ClientIPMiddleware:
    """Middleware to extract and store client IP in request state."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """ASGI callable that processes the request."""
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        # Create a Request object to access headers and client info
        request = Request(scope, receive)

        # Extract client IP based on configuration
        if TRACECAT__DISABLE_PRESIGNED_URL_IP_CHECKING:
            # For local MinIO, disable IP checking
            client_ip = None
        else:
            # For production S3, extract real client IP
            client_ip = self._extract_client_ip(request)

        # Attach to request state for use in routes
        scope["state"] = getattr(scope, "state", {})
        scope["state"]["client_ip"] = client_ip

        await self.app(scope, receive, send)

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
