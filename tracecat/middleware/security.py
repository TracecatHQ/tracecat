"""Security headers middleware.
"""

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware


class SecurityMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        
        # Base CSP directives
        csp_directives = [
            "default-src 'self' blob: data:",
            "frame-ancestors 'none'"
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
