from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from fastapi import FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from tracecat.executor.action_gateway.app import create_app
from tracecat.logger import logger


def _route_keys(app: FastAPI) -> set[tuple[str, str]]:
    return {
        (route.path, method)
        for route in app.routes
        if isinstance(route, APIRoute)
        for method in route.methods
    }


def _internal_route_keys(app: FastAPI) -> set[tuple[str, str]]:
    return {
        route_key
        for route_key in _route_keys(app)
        if route_key[0].startswith("/internal")
    }


@contextmanager
def _capture_logs(*, level: str) -> Iterator[list[Any]]:
    records: list[Any] = []
    sink_id = logger.add(
        lambda message: records.append(message.record),
        level=level,
    )
    try:
        yield records
    finally:
        logger.remove(sink_id)


def test_action_gateway_mounts_internal_routes() -> None:
    from tracecat.api.app import create_app as create_api_app

    api_routes = _internal_route_keys(create_api_app())
    app = create_app()
    gateway_routes = _internal_route_keys(app)

    assert api_routes <= gateway_routes
    assert ("/internal/health", "GET") in gateway_routes
    assert not any(
        path.startswith("/internal/capabilities") for path, _ in gateway_routes
    )


def test_action_gateway_logs_request_completion() -> None:
    app = create_app()
    with _capture_logs(level="DEBUG") as records:
        with TestClient(app) as client:
            response = client.get("/internal/health?probe=1")

    assert response.status_code == 200
    record = next(
        record
        for record in records
        if record["message"] == "Action Gateway request completed"
    )
    assert record["extra"]["method"] == "GET"
    assert record["extra"]["uri"] == "/internal/health?probe=1"
    assert record["extra"]["status_code"] == 200
    assert record["extra"]["elapsed_ms"] >= 0
    assert "request_size_bytes" in record["extra"]
