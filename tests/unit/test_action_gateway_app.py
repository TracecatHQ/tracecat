from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from tracecat.executor.action_gateway import app as action_gateway_app
from tracecat.executor.action_gateway.app import (
    create_app,
    request_logging_middleware,
    validation_exception_handler,
)


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


def test_action_gateway_request_log_omits_query_string(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    records: list[tuple[str, dict[str, object]]] = []

    def fake_info(message: str, **kwargs: object) -> None:
        records.append((message, kwargs))

    app = FastAPI()
    app.middleware("http")(request_logging_middleware)

    async def health() -> dict[str, str]:
        return {"status": "ok"}

    app.add_api_route("/internal/health", health, methods=["GET"])

    monkeypatch.setattr(action_gateway_app.logger, "info", fake_info)

    with TestClient(app) as client:
        response = client.get("/internal/health?token=secret")

    assert response.status_code == 200
    assert records == [
        (
            "Action Gateway request",
            {
                "method": "GET",
                "path": "/internal/health",
                "status_code": 200,
                "elapsed_ms": records[0][1]["elapsed_ms"],
            },
        )
    ]
    assert "token" not in str(records[0][1])


def test_action_gateway_validation_errors_are_json_safe() -> None:
    app = FastAPI()
    app.add_exception_handler(RequestValidationError, validation_exception_handler)

    async def boom() -> None:
        raise RequestValidationError(
            [
                {
                    "type": "value_error",
                    "loc": ("body", "field"),
                    "msg": "Value error, bad value",
                    "input": "x",
                    "ctx": {"error": ValueError("bad value")},
                }
            ]
        )

    app.add_api_route("/boom", boom, methods=["GET"])

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/boom")

    assert response.status_code == 422
    assert response.json()["detail"][0]["ctx"]["error"] == "bad value"
