from __future__ import annotations

import re
from ipaddress import ip_address

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware

from tracecat.contexts import RequestAuditContext, ctx_request_audit

_AUDIT_USER_AGENT_PATTERN = re.compile(
    r"^(?P<product>Mozilla|TracecatClient|curl|python-httpx|Claude-Code|Codex)"
    r"/(?P<version>\d{1,4}(?:\.\d{1,4}){0,3})\b",
    re.IGNORECASE,
)
_AUDIT_USER_AGENT_FAMILIES = {
    "claude-code": "claude-code",
    "codex": "codex",
    "curl": "curl",
    "mozilla": "browser",
    "python-httpx": "httpx",
    "tracecatclient": "tracecat",
}


def _normalize_client_ip(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return str(ip_address(value))
    except ValueError:
        return None


def _normalize_audit_user_agent(value: str | None) -> str | None:
    """Return a bounded client family/version without forwarding raw metadata."""
    if not value:
        return None
    if match := _AUDIT_USER_AGENT_PATTERN.match(value):
        family = _AUDIT_USER_AGENT_FAMILIES[match.group("product").lower()]
        return f"{family}/{match.group('version')}"
    return "other"


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        # Leftmost X-Forwarded-For hop, falling back to the socket peer.
        # Client-controlled: informational attribution, not a security control.
        client_ip = None
        if forwarded_for := request.headers.get("X-Forwarded-For"):
            client_ip = _normalize_client_ip(forwarded_for.split(",", 1)[0].strip())
        if client_ip is None:
            client_ip = _normalize_client_ip(
                request.client.host if request.client is not None else None
            )

        user_agent = _normalize_audit_user_agent(request.headers.get("User-Agent"))

        audit_token = ctx_request_audit.set(
            RequestAuditContext(client_ip=client_ip, user_agent=user_agent)
        )

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
            ctx_request_audit.reset(audit_token)
