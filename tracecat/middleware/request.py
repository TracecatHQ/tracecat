import time
import uuid

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware

from tracecat.contexts import ctx_client_ip, ctx_request_id


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

        request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
        client_ip_token = ctx_client_ip.set(client_ip)
        request_id_token = ctx_request_id.set(request_id)
        start = time.perf_counter()

        try:
            response = await call_next(request)
            response.headers["X-Request-ID"] = request_id
            body_bytes = int(request.headers.get("Content-Length") or 0)
            request.app.state.logger.info(
                "HTTP request completed",
                request_id=request_id,
                method=request.method,
                scheme=request.url.scheme,
                hostname=request.url.hostname,
                path=request.url.path,
                status_code=response.status_code,
                duration_ms=round((time.perf_counter() - start) * 1000, 3),
                query_param_count=len(request.query_params),
                body_present=body_bytes > 0,
                body_content_type=request.headers.get("Content-Type"),
                body_bytes=body_bytes,
            )
            return response
        finally:
            ctx_request_id.reset(request_id_token)
            ctx_client_ip.reset(client_ip_token)
