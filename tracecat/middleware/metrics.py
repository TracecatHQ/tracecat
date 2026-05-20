from collections.abc import Awaitable, Callable

from fastapi import Request
from prometheus_client import CONTENT_TYPE_LATEST, REGISTRY, Counter, generate_latest
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response
from starlette.types import ASGIApp

type CallNext = Callable[[Request], Awaitable[Response]]

HTTP_REQUEST_TOTAL = Counter(
    "http_request_total",
    "Total HTTP requests handled by Tracecat services.",
    labelnames=("component", "route", "method", "code"),
)

_EXCLUDED_METRICS_PATHS = frozenset(
    {
        "/metrics",
        "/api/metrics",
        "/health",
        "/ready",
        "/api/health",
        "/api/ready",
    }
)


class HTTPMetricsMiddleware(BaseHTTPMiddleware):
    """Record low-cardinality HTTP request counters for Prometheus alerts."""

    def __init__(self, app: ASGIApp, component: str) -> None:
        super().__init__(app)
        self.component = component

    async def dispatch(self, request: Request, call_next: CallNext) -> Response:
        if _should_skip_request(request):
            return await call_next(request)

        status_code = 500
        try:
            response = await call_next(request)
            status_code = response.status_code
            return response
        finally:
            _record_http_request(
                component=self.component,
                route=_route_label(request),
                method=request.method,
                status_code=status_code,
            )


def prometheus_metrics_response() -> Response:
    data = generate_latest(REGISTRY)
    return Response(content=data, headers={"Content-Type": str(CONTENT_TYPE_LATEST)})


def _should_skip_request(request: Request) -> bool:
    route = _route_label(request)
    return (
        request.url.path in _EXCLUDED_METRICS_PATHS or route in _EXCLUDED_METRICS_PATHS
    )


def _route_label(request: Request) -> str:
    route = request.scope.get("route")
    route_path = getattr(route, "path", None)
    if isinstance(route_path, str) and route_path:
        root_path = request.scope.get("root_path")
        if isinstance(root_path, str) and root_path:
            if route_path == "/":
                return root_path.rstrip("/") or "/"
            return f"{root_path.rstrip('/')}/{route_path.lstrip('/')}"
        return route_path
    return "unmatched"


def _record_http_request(
    *, component: str, route: str, method: str, status_code: int
) -> None:
    HTTP_REQUEST_TOTAL.labels(
        component=component,
        route=route,
        method=method,
        code=str(status_code),
    ).inc()
