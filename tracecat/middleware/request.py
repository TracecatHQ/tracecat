from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        # Extract request parameters
        request_params = dict(request.query_params)
        request_body = (
            await request.json()
            if request.headers.get("Content-Type") == "application/json"
            else {}
        )

        # Log the incoming request with parameters
        request.app.logger.debug(
            "Incoming request",
            method=request.method,
            scheme=request.url.scheme,
            hostname=request.url.hostname,
            path=request.url.path,
            params=request_params,
            body=request_body,
            headers=request.headers,
        )

        return await call_next(request)
