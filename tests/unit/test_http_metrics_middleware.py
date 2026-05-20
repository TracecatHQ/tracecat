from fastapi import FastAPI, HTTPException, Response
from fastapi.testclient import TestClient

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
    assert 'component="unit-api"' in metrics_body
    assert 'route="/api/items/{item_id}"' in metrics_body
    assert 'route="/api/blocked"' in metrics_body
    assert 'code="200"' in metrics_body
    assert 'code="403"' in metrics_body
    assert "status_code=" not in metrics_body
    assert "status_class=" not in metrics_body
    assert 'route="unmatched"' not in metrics_body
    assert 'route="/api/metrics"' not in metrics_body
