from __future__ import annotations

import re
from ipaddress import ip_address

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware

from tracecat import config
from tracecat.contexts import RequestAuditContext, ctx_request_audit

# === Client IP resolution === #


def _normalize_client_ip(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return str(ip_address(value))
    except ValueError:
        return None


def _is_trusted_proxy(value: str) -> bool:
    addr = ip_address(value)
    return any(
        addr in network for network in config.TRACECAT__AUDIT_TRUSTED_PROXY_CIDRS
    )


def _resolve_client_ip(request: Request) -> str | None:
    """Rightmost untrusted hop of X-Forwarded-For plus the socket peer.

    Each proxy appends its peer, so the chain is trustworthy only from the
    right; the leftmost entries are client-controlled. Walk right to left,
    skipping TRACECAT__AUDIT_TRUSTED_PROXY_CIDRS hops; the first address not
    on that list is the client. A fully trusted chain (internal service
    traffic) attributes to its leftmost hop.
    """
    candidates: list[str] = []
    if forwarded_for := request.headers.get("X-Forwarded-For"):
        for entry in forwarded_for.split(","):
            if normalized := _normalize_client_ip(entry.strip()):
                candidates.append(normalized)
    if peer := _normalize_client_ip(
        request.client.host if request.client is not None else None
    ):
        candidates.append(peer)
    for candidate in reversed(candidates):
        if not _is_trusted_proxy(candidate):
            return candidate
    return candidates[0] if candidates else None


# === User-agent normalization === #

_AUDIT_CLIENT_USER_AGENT_PATTERN = re.compile(
    r"^(?P<product>TracecatClient|curl|python-httpx|Claude-Code|Codex)"
    r"/(?P<version>\d{1,4}(?:\.\d{1,4}){0,3})\b",
    re.IGNORECASE,
)
_AUDIT_CLIENT_USER_AGENT_FAMILIES = {
    "claude-code": "claude-code",
    "codex": "codex",
    "curl": "curl",
    "python-httpx": "httpx",
    "tracecatclient": "tracecat",
}
# Browser UAs hide their identity mid-string; most-specific token first
# (Edge UAs contain Chrome/, Chrome UAs contain Safari/).
_AUDIT_BROWSER_USER_AGENT_PATTERNS = (
    (
        "edge",
        re.compile(
            r"\bEdg(?:A|iOS)?/(?P<version>\d{1,4}(?:\.\d{1,4}){0,3})\b",
            re.IGNORECASE,
        ),
    ),
    (
        "chrome",
        re.compile(
            r"\b(?:Chrome|CriOS)/(?P<version>\d{1,4}(?:\.\d{1,4}){0,3})\b",
            re.IGNORECASE,
        ),
    ),
    (
        "firefox",
        re.compile(
            r"\b(?:Firefox|FxiOS)/(?P<version>\d{1,4}(?:\.\d{1,4}){0,3})\b",
            re.IGNORECASE,
        ),
    ),
)
_AUDIT_SAFARI_PRODUCT_PATTERN = re.compile(r"\bSafari/", re.IGNORECASE)
_AUDIT_SAFARI_VERSION_PATTERN = re.compile(
    r"\bVersion/(?P<version>\d{1,4}(?:\.\d{1,4}){0,3})\b",
    re.IGNORECASE,
)


def _normalize_audit_user_agent(value: str | None) -> str | None:
    """Return a bounded client family/version without forwarding raw metadata."""
    if not value:
        return None
    if match := _AUDIT_CLIENT_USER_AGENT_PATTERN.match(value):
        family = _AUDIT_CLIENT_USER_AGENT_FAMILIES[match.group("product").lower()]
        return f"{family}/{match.group('version')}"
    for family, pattern in _AUDIT_BROWSER_USER_AGENT_PATTERNS:
        if match := pattern.search(value):
            return f"{family}/{match.group('version')}"
    if _AUDIT_SAFARI_PRODUCT_PATTERN.search(value) and (
        match := _AUDIT_SAFARI_VERSION_PATTERN.search(value)
    ):
        return f"safari/{match.group('version')}"
    if value.lower().startswith("mozilla/"):
        return "browser/other"
    return "other"


# === Audit context === #


def build_request_audit_context(request: Request) -> RequestAuditContext:
    """Derive audit attribution from an HTTP request.

    Informational attribution, not a security control.
    """
    client_ip = _resolve_client_ip(request)
    user_agent = _normalize_audit_user_agent(request.headers.get("User-Agent"))
    return RequestAuditContext(client_ip=client_ip, user_agent=user_agent)


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        audit_context = build_request_audit_context(request)
        client_ip = audit_context.client_ip

        audit_token = ctx_request_audit.set(audit_context)

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
