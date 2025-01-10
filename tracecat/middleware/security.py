import os

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        if os.getenv("POSTHOG_KEY"):
            csp_directives = [
                "connect-src 'self' https://*.posthog.com",
                "default-src 'self'",
                "worker-src 'self' blob:",
                "frame-ancestors 'none'",
                "img-src 'self' data:",
                "object-src 'none'",
                "script-src 'self' 'unsafe-inline' https://*.posthog.com",
                "style-src 'self' 'unsafe-inline'",
            ]
        else:
            csp_directives = [
                "connect-src 'self'",
                "default-src 'self'",
                "worker-src 'self' blob:",
                "frame-ancestors 'none'",
                "img-src 'self' data:",
                "object-src 'none'",
                "script-src 'self' 'unsafe-inline'",
                "style-src 'self' 'unsafe-inline'",
            ]
        headers = {
            "Strict-Transport-Security": "max-age=7776000; includeSubDomains",
            "Content-Security-Policy": "; ".join(csp_directives),
            "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "strict-origin-when-cross-origin",
            "Permissions-Policy": "document-domain=()"
        }
        response.headers.update(headers)
        return response
