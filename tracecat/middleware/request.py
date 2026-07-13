from __future__ import annotations

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware

from tracecat.contexts import ctx_client_ip


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        # Capture client IP address
        # Check X-Forwarded-For header first (for production behind proxies)
        client_ip = None
        forwarded_for = request.headers.get("X-Forwarded-For")
        if forwarded_for:
            # X-Forwarded-For format: "client, proxy1, proxy2"
            # First IP is the original client
            client_ip = forwarded_for.split(",")[0].strip()
        # Fallback to direct connection IP
        if not client_ip and request.client:
            client_ip = request.client.host

        token = ctx_client_ip.set(client_ip)

        try:
            request_logger = request.app.state.logger
            # Extract request parameters
            request_params = dict(request.query_params)

            # Log the incoming request with parameters.
            request_logger.debug(
                "Incoming request",
                method=request.method,
                scheme=request.url.scheme,
                hostname=request.url.hostname,
                path=request.url.path,
                params=request_params,
                client_ip=client_ip,
            )

            return await call_next(request)
        finally:
            ctx_client_ip.reset(token)
