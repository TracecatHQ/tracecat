from __future__ import annotations

import uuid

import pytest
from fastapi import Depends, FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.testclient import TestClient

from tests._route_utils import iter_effective_api_routes
from tracecat import config
from tracecat.auth.executor_tokens import ExecutionOrigin, mint_executor_token
from tracecat.dsl.enums import PlatformAction
from tracecat.executor.action_gateway import app as action_gateway_app
from tracecat.executor.action_gateway.app import (
    create_app,
    request_logging_middleware,
    validation_exception_handler,
)
from tracecat.executor.action_gateway.policy import (
    enforce_agent_script_gateway_access,
)


def _route_keys(app: FastAPI) -> set[tuple[str, str]]:
    return {
        (route.path, method)
        for route in iter_effective_api_routes(app)
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

    assert api_routes.isdisjoint(gateway_routes)
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


@pytest.mark.parametrize(
    ("method", "path"),
    [
        pytest.param("GET", "/internal/cases", id="registry-action"),
        pytest.param("POST", "/internal/workflows/run", id="sdk-operation"),
        pytest.param("POST", "/internal/agent/run", id="nested-agent-run"),
    ],
)
def test_agent_script_cannot_reach_gateway(
    monkeypatch: pytest.MonkeyPatch,
    method: str,
    path: str,
) -> None:
    monkeypatch.setattr(config, "TRACECAT__SERVICE_KEY", "test-service-key")
    app = FastAPI(dependencies=[Depends(enforce_agent_script_gateway_access)])

    async def operation() -> dict[str, bool]:
        return {"ok": True}

    app.add_api_route(path, operation, methods=[method])
    token = mint_executor_token(
        workspace_id=uuid.uuid4(),
        user_id=None,
        execution_origin="agent",
        root_action=PlatformAction.RUN_PYTHON,
        wf_id="wf-1",
        wf_exec_id="run-1",
    )

    with TestClient(app) as client:
        response = client.request(
            method,
            path,
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 403
    assert response.json()["detail"]["error"]["code"] == (
        "agent_script_gateway_disabled"
    )


@pytest.mark.parametrize(
    ("execution_origin", "root_action"),
    [
        pytest.param(None, None, id="legacy-unattested"),
        pytest.param("agent", None, id="legacy-agent-token"),
        pytest.param("agent", "core.cases.list_cases", id="agent-registry-action"),
        pytest.param(
            "workflow",
            PlatformAction.RUN_PYTHON,
            id="workflow-authored-python",
        ),
    ],
)
def test_other_executions_keep_normal_gateway_access(
    monkeypatch: pytest.MonkeyPatch,
    execution_origin: ExecutionOrigin | None,
    root_action: str | None,
) -> None:
    monkeypatch.setattr(config, "TRACECAT__SERVICE_KEY", "test-service-key")
    app = FastAPI(dependencies=[Depends(enforce_agent_script_gateway_access)])

    async def operation() -> dict[str, bool]:
        return {"ok": True}

    app.add_api_route("/internal/cases", operation, methods=["GET"])
    token = mint_executor_token(
        workspace_id=uuid.uuid4(),
        user_id=None,
        execution_origin=execution_origin,
        root_action=root_action,
        wf_id="wf-1",
        wf_exec_id="run-1",
    )

    with TestClient(app) as client:
        response = client.get(
            "/internal/cases",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 200


def test_agent_script_can_reach_gateway_health(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config, "TRACECAT__SERVICE_KEY", "test-service-key")
    app = FastAPI(dependencies=[Depends(enforce_agent_script_gateway_access)])

    async def health() -> dict[str, str]:
        return {"status": "ok"}

    app.add_api_route("/internal/health", health, methods=["GET"])
    token = mint_executor_token(
        workspace_id=uuid.uuid4(),
        user_id=None,
        execution_origin="agent",
        root_action=PlatformAction.RUN_PYTHON,
        wf_id="wf-1",
        wf_exec_id="run-1",
    )

    with TestClient(app) as client:
        response = client.get(
            "/internal/health",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 200


def test_all_action_gateway_routes_enforce_agent_script_policy() -> None:
    app = create_app()

    for route in iter_effective_api_routes(app):
        dependency_calls = {
            dependency.call for dependency in route.dependant.dependencies
        }
        assert enforce_agent_script_gateway_access in dependency_calls, route.path
