from typing import Any

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware

from tracecat.contexts import ctx_client_ip

# Substrings that mark a key as secret-bearing. Matched case-insensitively
# against every key in a request body before it is logged. Request bodies can
# carry provider credentials (e.g. stdio MCP `env` maps, OAuth secrets), and the
# debug request log must never emit those values in plaintext.
SENSITIVE_KEY_SUBSTRINGS = (
    "token",
    "secret",
    "password",
    "passwd",
    "api_key",
    "apikey",
    "access_key",
    "private_key",
    "client_secret",
    "authorization",
    "credential",
    "env",
)

_REDACTED = "***redacted***"

# Cap on how deep and how wide we walk a body when redacting, so a pathological
# payload cannot blow up logging.
_MAX_DEPTH = 6


def _is_sensitive_key(key: str) -> bool:
    lowered = key.lower()
    return any(marker in lowered for marker in SENSITIVE_KEY_SUBSTRINGS)


def redact_request_body(value: Any, *, depth: int = 0) -> Any:
    """Recursively redact secret-bearing values from a request body for logging.

    Any dict value whose key matches a sensitive marker is replaced wholesale
    (including nested structures) so credentials never reach the logs. Non-dict
    containers are walked so secrets nested inside lists are still masked.
    """
    if depth >= _MAX_DEPTH:
        return _REDACTED
    if isinstance(value, dict):
        redacted: dict[Any, Any] = {}
        for key, item in value.items():
            if isinstance(key, str) and _is_sensitive_key(key):
                redacted[key] = _REDACTED
            else:
                redacted[key] = redact_request_body(item, depth=depth + 1)
        return redacted
    if isinstance(value, (list, tuple)):
        return [redact_request_body(item, depth=depth + 1) for item in value]
    return value


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
            # Extract request parameters
            request_params = dict(request.query_params)
            # Only try to parse JSON body for methods that typically have a body
            request_body = {}
            if (
                request.method in ("POST", "PUT", "PATCH")
                and request.headers.get("Content-Type") == "application/json"
            ):
                try:
                    request_body = await request.json()
                except Exception:
                    pass  # Ignore parse errors for logging purposes

            # Log the incoming request with parameters. Bodies can carry provider
            # credentials, so redact secret-bearing keys before logging.
            request.app.state.logger.debug(
                "Incoming request",
                method=request.method,
                scheme=request.url.scheme,
                hostname=request.url.hostname,
                path=request.url.path,
                params=request_params,
                body=redact_request_body(request_body),
                client_ip=client_ip,
            )

            return await call_next(request)
        finally:
            ctx_client_ip.reset(token)
