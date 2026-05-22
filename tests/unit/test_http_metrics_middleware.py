import uuid

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.testclient import TestClient

from tracecat.auth.types import Role
from tracecat.middleware.metrics import (
    HTTPMetricsMiddleware,
    prometheus_metrics_response,
)


def test_http_metrics_middleware_records_normalized_route_and_code() -> None:
    app = FastAPI(root_path="/api")
    app.add_middleware(HTTPMetricsMiddleware, component="unit-api")

    @app.get("/items/{item_id}")
    async def get_item(  # pyright: ignore[reportUnusedFunction] - route handler
        item_id: str,
    ) -> dict[str, str]:
        return {"item_id": item_id}

    @app.get("/blocked")
    async def blocked() -> None:  # pyright: ignore[reportUnusedFunction] - route handler
        raise HTTPException(status_code=403)

    @app.get("/metrics")
    async def metrics() -> Response:  # pyright: ignore[reportUnusedFunction] - route handler
        return prometheus_metrics_response()

    client = TestClient(app)

    assert client.get("/items/abc").status_code == 200
    assert client.get("/blocked").status_code == 403
    assert client.get("/metrics").status_code == 200
    assert client.get("/api/metrics").status_code == 200

    metrics_body = client.get("/metrics").text

    assert "http_request_total" in metrics_body
    assert "http_request_duration_seconds" in metrics_body
    assert 'component="unit-api"' in metrics_body
    assert 'route="/api/items/{item_id}"' in metrics_body
    assert 'route="/api/blocked"' in metrics_body
    assert 'code="200"' in metrics_body
    assert 'code="403"' in metrics_body
    assert "status_code=" not in metrics_body
    assert "status_class=" not in metrics_body
    assert 'route="unmatched"' not in metrics_body
    assert 'route="/api/metrics"' not in metrics_body


def test_http_metrics_middleware_skips_custom_root_path_probes() -> None:
    app = FastAPI(root_path="/custom")
    app.add_middleware(HTTPMetricsMiddleware, component="unit-custom-root")

    @app.get("/metrics")
    async def metrics() -> Response:  # pyright: ignore[reportUnusedFunction] - route handler
        return prometheus_metrics_response()

    @app.get("/health")
    async def health() -> dict[str, str]:  # pyright: ignore[reportUnusedFunction] - route handler
        return {"status": "ok"}

    client = TestClient(app)

    assert client.get("/custom/metrics").status_code == 200
    assert client.get("/custom/health").status_code == 200

    metrics_body = client.get("/metrics").text

    assert 'component="unit-custom-root"' not in metrics_body


def test_http_metrics_middleware_records_error_tenant_labels_from_request_state() -> (
    None
):
    app = FastAPI(root_path="/api")
    app.add_middleware(HTTPMetricsMiddleware, component="unit-api-role")
    organization_id = uuid.uuid4()
    workspace_id = uuid.uuid4()

    @app.get("/boom")
    async def boom(  # pyright: ignore[reportUnusedFunction] - route handler
        request: Request,
    ) -> None:
        request.state.role = Role(
            type="user",
            organization_id=organization_id,
            workspace_id=workspace_id,
            user_id=uuid.uuid4(),
            service_id="tracecat-api",
        )
        raise HTTPException(status_code=500)

    @app.get("/metrics")
    async def metrics() -> Response:  # pyright: ignore[reportUnusedFunction] - route handler
        return prometheus_metrics_response()

    client = TestClient(app)

    assert client.get("/boom").status_code == 500

    metrics_body = client.get("/metrics").text

    assert "http_request_error_total" in metrics_body
    assert 'component="unit-api-role"' in metrics_body
    assert 'route="/api/boom"' in metrics_body
    assert 'code="500"' in metrics_body
    assert f'organization_id="{organization_id}"' in metrics_body
    assert f'workspace_id="{workspace_id}"' in metrics_body


def test_http_metrics_middleware_records_selected_4xx_tenant_labels() -> None:
    app = FastAPI(root_path="/api")
    app.add_middleware(HTTPMetricsMiddleware, component="unit-api-422")
    organization_id = uuid.uuid4()
    workspace_id = uuid.uuid4()

    @app.get("/bad-request")
    async def bad_request(  # pyright: ignore[reportUnusedFunction] - route handler
        request: Request,
    ) -> None:
        request.state.role = Role(
            type="user",
            organization_id=organization_id,
            workspace_id=workspace_id,
            user_id=uuid.uuid4(),
            service_id="tracecat-api",
        )
        raise HTTPException(status_code=422)

    @app.get("/metrics")
    async def metrics() -> Response:  # pyright: ignore[reportUnusedFunction] - route handler
        return prometheus_metrics_response()

    client = TestClient(app)

    assert client.get("/bad-request").status_code == 422

    metrics_body = client.get("/metrics").text

    assert "http_request_error_total" in metrics_body
    assert 'component="unit-api-422"' in metrics_body
    assert 'route="/api/bad-request"' in metrics_body
    assert 'code="422"' in metrics_body
    assert f'organization_id="{organization_id}"' in metrics_body
    assert f'workspace_id="{workspace_id}"' in metrics_body


def test_http_metrics_middleware_skips_unselected_4xx_tenant_labels() -> None:
    app = FastAPI(root_path="/api")
    app.add_middleware(HTTPMetricsMiddleware, component="unit-api-404")
    organization_id = uuid.uuid4()
    workspace_id = uuid.uuid4()

    @app.get("/not-found")
    async def not_found(  # pyright: ignore[reportUnusedFunction] - route handler
        request: Request,
    ) -> None:
        request.state.role = Role(
            type="user",
            organization_id=organization_id,
            workspace_id=workspace_id,
            user_id=uuid.uuid4(),
            service_id="tracecat-api",
        )
        raise HTTPException(status_code=404)

    @app.get("/metrics")
    async def metrics() -> Response:  # pyright: ignore[reportUnusedFunction] - route handler
        return prometheus_metrics_response()

    client = TestClient(app)

    assert client.get("/not-found").status_code == 404

    metrics_body = client.get("/metrics").text

    assert "http_request_total" in metrics_body
    assert 'component="unit-api-404"' in metrics_body
    assert 'route="/api/not-found"' in metrics_body
    assert 'code="404"' in metrics_body
    error_lines = [
        line
        for line in metrics_body.splitlines()
        if line.startswith("http_request_error_total{")
    ]
    assert not any('component="unit-api-404"' in line for line in error_lines)
    assert f'organization_id="{organization_id}"' not in metrics_body
    assert f'workspace_id="{workspace_id}"' not in metrics_body


def test_http_metrics_middleware_skips_error_tenant_metric_without_tenant() -> None:
    app = FastAPI(root_path="/api")
    app.add_middleware(HTTPMetricsMiddleware, component="unit-api-missing-tenant")

    @app.get("/boom")
    async def boom() -> None:  # pyright: ignore[reportUnusedFunction] - route handler
        raise HTTPException(status_code=500)

    @app.get("/metrics")
    async def metrics() -> Response:  # pyright: ignore[reportUnusedFunction] - route handler
        return prometheus_metrics_response()

    client = TestClient(app)

    assert client.get("/boom").status_code == 500

    metrics_body = client.get("/metrics").text

    assert "http_request_total" in metrics_body
    assert 'component="unit-api-missing-tenant"' in metrics_body
    assert 'route="/api/boom"' in metrics_body
    assert 'code="500"' in metrics_body
    error_lines = [
        line
        for line in metrics_body.splitlines()
        if line.startswith("http_request_error_total{")
    ]
    assert not any(
        'component="unit-api-missing-tenant"' in line for line in error_lines
    )
