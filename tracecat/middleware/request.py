from __future__ import annotations

from ipaddress import ip_address

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware

from tracecat.contexts import ctx_client_ip, ctx_user_agent
from tracecat.sanitization import redact_sensitive_text

_MAX_USER_AGENT_LENGTH = 512


def _normalize_client_ip(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return str(ip_address(value))
    except ValueError:
        return None


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        # Capture client IP address
        # Check X-Forwarded-For header first (for production behind proxies)
        forwarded_for = request.headers.get("X-Forwarded-For")
        forwarded_ip = forwarded_for.split(",")[0].strip() if forwarded_for else None
        client_ip = _normalize_client_ip(forwarded_ip)
        # Fallback to direct connection IP
        if client_ip is None and request.client:
            client_ip = _normalize_client_ip(request.client.host)

        raw_user_agent = request.headers.get("User-Agent")
        user_agent = (
            redact_sensitive_text(raw_user_agent, redact_emails=True)[
                :_MAX_USER_AGENT_LENGTH
            ]
            if raw_user_agent
            else None
        )

        client_ip_token = ctx_client_ip.set(client_ip)
        user_agent_token = ctx_user_agent.set(user_agent)

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
            ctx_user_agent.reset(user_agent_token)
            ctx_client_ip.reset(client_ip_token)
