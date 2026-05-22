import time
from collections.abc import Awaitable, Callable

from fastapi import Request
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    REGISTRY,
    Counter,
    Histogram,
    generate_latest,
)
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response
from starlette.types import ASGIApp

from tracecat.auth.types import Role

type CallNext = Callable[[Request], Awaitable[Response]]

HTTP_REQUEST_TOTAL = Counter(
    "http_request_total",
    "Total HTTP requests handled by Tracecat services.",
    labelnames=("component", "route", "method", "code"),
)

HTTP_REQUEST_ERROR_TOTAL = Counter(
    "http_request_error_total",
    "Total HTTP error responses handled by Tracecat services with tenant context.",
    labelnames=(
        "component",
        "route",
        "method",
        "code",
        "organization_id",
        "workspace_id",
    ),
)

HTTP_REQUEST_DURATION_SECONDS = Histogram(
    "http_request_duration_seconds",
    "HTTP request latency for Tracecat services.",
    labelnames=("component", "route", "method", "code"),
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60),
)

WORKFLOW_DISPATCH_FAILURE_TOTAL = Counter(
    "workflow_dispatch_failure_total",
    "Workflow starts that failed before Temporal accepted the execution.",
    labelnames=("trigger_type", "execution_type", "mode"),
)

_EXCLUDED_METRICS_PATHS = frozenset({"/metrics", "/health", "/ready"})

_TENANT_ERROR_STATUS_CODES = frozenset({400, 401, 403, 422, 429})


class HTTPMetricsMiddleware(BaseHTTPMiddleware):
    """Record low-cardinality HTTP request counters for Prometheus alerts."""

    def __init__(self, app: ASGIApp, component: str) -> None:
        super().__init__(app)
        self.component = component

    async def dispatch(self, request: Request, call_next: CallNext) -> Response:
        if _should_skip_request(request):
            return await call_next(request)

        status_code = 500
        started_at = time.monotonic()
        try:
            response = await call_next(request)
            status_code = response.status_code
            return response
        finally:
            route = _route_label(request)
            HTTP_REQUEST_TOTAL.labels(
                component=self.component,
                route=route,
                method=request.method,
                code=str(status_code),
            ).inc()
            HTTP_REQUEST_DURATION_SECONDS.labels(
                component=self.component,
                route=route,
                method=request.method,
                code=str(status_code),
            ).observe(time.monotonic() - started_at)

            if status_code >= 500 or status_code in _TENANT_ERROR_STATUS_CODES:
                if tenant_labels := _tenant_labels(request):
                    organization_id, workspace_id = tenant_labels
                    HTTP_REQUEST_ERROR_TOTAL.labels(
                        component=self.component,
                        route=route,
                        method=request.method,
                        code=str(status_code),
                        organization_id=organization_id,
                        workspace_id=workspace_id,
                    ).inc()


def prometheus_metrics_response() -> Response:
    data = generate_latest(REGISTRY)
    return Response(content=data, headers={"Content-Type": str(CONTENT_TYPE_LATEST)})


def record_workflow_dispatch_failure(
    *, trigger_type: str, execution_type: str, mode: str
) -> None:
    WORKFLOW_DISPATCH_FAILURE_TOTAL.labels(
        trigger_type=trigger_type,
        execution_type=execution_type,
        mode=mode,
    ).inc()


def _should_skip_request(request: Request) -> bool:
    route = _route_label(request)
    path = request.scope.get("path")
    request_path = path if isinstance(path, str) else request.url.path
    root_path = request.scope.get("root_path")

    if isinstance(root_path, str) and root_path not in {"", "/"}:
        normalized_root = root_path.rstrip("/")
        if request_path == normalized_root:
            request_path = "/"
        elif request_path.startswith(f"{normalized_root}/"):
            request_path = request_path[len(normalized_root) :] or "/"

    return request_path in _EXCLUDED_METRICS_PATHS or route in _EXCLUDED_METRICS_PATHS


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


def _tenant_labels(request: Request) -> tuple[str, str] | None:
    """Resolve tenant labels for diagnostic error metrics without database work."""
    role = getattr(request.state, "role", None)
    if not isinstance(role, Role):
        return None
    if role.organization_id is None or role.workspace_id is None:
        return None
    return str(role.organization_id), str(role.workspace_id)
