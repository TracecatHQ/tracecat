from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import jwt
import pytest
from fastapi import Depends, FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.testclient import TestClient

from tests._route_utils import iter_effective_api_routes
from tracecat import config
from tracecat.auth.executor_tokens import (
    EXECUTOR_TOKEN_AUDIENCE,
    EXECUTOR_TOKEN_ISSUER,
    EXECUTOR_TOKEN_SUBJECT,
    ExecutionOrigin,
    mint_executor_token,
)
from tracecat.executor.action_gateway import app as action_gateway_app
from tracecat.executor.action_gateway.app import (
    create_app,
    request_logging_middleware,
    validation_exception_handler,
)
from tracecat.executor.action_gateway.capabilities import (
    enforce_agent_action_capability,
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
        pytest.param(
            "GET",
            "/internal/cases",
            id="registry-action",
        ),
        pytest.param(
            "POST",
            "/internal/workflows/run",
            id="sdk-operation",
        ),
        pytest.param(
            "POST",
            "/internal/agent/run",
            id="nested-agent-run",
        ),
    ],
)
def test_agent_run_python_denies_previously_allowed_gateway_routes(
    monkeypatch: pytest.MonkeyPatch,
    method: str,
    path: str,
) -> None:
    monkeypatch.setattr(config, "TRACECAT__SERVICE_KEY", "test-service-key")
    app = FastAPI(dependencies=[Depends(enforce_agent_action_capability)])

    async def operation() -> dict[str, bool]:
        return {"ok": True}

    app.add_api_route(path, operation, methods=[method])
    token = mint_executor_token(
        workspace_id=uuid.uuid4(),
        user_id=None,
        scopes=frozenset({"*"}),
        action="core.script.run_python",
        execution_origin="agent",
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


def test_gateway_deny_depends_only_on_execution_origin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only tokens attesting Agent authorship receive the gateway deny."""
    monkeypatch.setattr(config, "TRACECAT__SERVICE_KEY", "test-service-key")
    app = FastAPI(dependencies=[Depends(enforce_agent_action_capability)])

    async def list_cases() -> dict[str, bool]:
        return {"ok": True}

    app.add_api_route("/internal/cases", list_cases, methods=["GET"])

    def _mint(origin: ExecutionOrigin | None) -> str:
        return mint_executor_token(
            workspace_id=uuid.uuid4(),
            user_id=None,
            scopes=frozenset({"*"}),
            action="core.script.run_python",
            execution_origin=origin,
            wf_id="wf-1",
            wf_exec_id="run-1",
        )

    agent_token = _mint("agent")
    template_token = _mint("registry_template")
    unattested_token = _mint(None)

    with TestClient(app) as client:
        agent_response = client.get(
            "/internal/cases", headers={"Authorization": f"Bearer {agent_token}"}
        )
        template_response = client.get(
            "/internal/cases",
            headers={"Authorization": f"Bearer {template_token}"},
        )
        unattested_response = client.get(
            "/internal/cases",
            headers={"Authorization": f"Bearer {unattested_token}"},
        )

    assert agent_response.status_code == 403
    assert agent_response.json()["detail"]["error"]["code"] == (
        "agent_script_gateway_disabled"
    )
    assert template_response.status_code == 200
    assert template_response.json() == {"ok": True}
    assert unattested_response.status_code == 200
    assert unattested_response.json() == {"ok": True}


def test_agent_gateway_hard_deny_does_not_depend_on_route_mapping(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config, "TRACECAT__SERVICE_KEY", "test-service-key")
    app = FastAPI(dependencies=[Depends(enforce_agent_action_capability)])

    async def list_cases() -> dict[str, bool]:
        return {"ok": True}

    app.add_api_route("/internal/cases", list_cases, methods=["GET"])
    app.add_api_route("/internal/cases", list_cases, methods=["POST"])
    app.add_api_route("/internal/cases-alias", list_cases, methods=["GET"])
    token = mint_executor_token(
        workspace_id=uuid.uuid4(),
        user_id=None,
        scopes=frozenset({"*"}),
        action="core.script.run_python",
        execution_origin="agent",
        wf_id="wf-1",
        wf_exec_id="run-1",
    )
    headers = {"Authorization": f"Bearer {token}"}

    with TestClient(app) as client:
        responses = [
            client.get("/internal/cases", headers=headers),
            client.post("/internal/cases", headers=headers),
            client.get("/internal/cases-alias", headers=headers),
        ]

    assert all(response.status_code == 403 for response in responses)
    assert all(
        response.json()["detail"]["error"]["code"] == "agent_script_gateway_disabled"
        for response in responses
    )


def test_agent_run_python_can_reach_gateway_health(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config, "TRACECAT__SERVICE_KEY", "test-service-key")
    app = FastAPI(dependencies=[Depends(enforce_agent_action_capability)])

    async def health() -> dict[str, str]:
        return {"status": "ok"}

    app.add_api_route("/internal/health", health, methods=["GET"])
    token = mint_executor_token(
        workspace_id=uuid.uuid4(),
        user_id=None,
        scopes=frozenset({"*"}),
        action="core.script.run_python",
        execution_origin="agent",
        wf_id="wf-1",
        wf_exec_id="run-1",
    )

    with TestClient(app) as client:
        response = client.get(
            "/internal/health",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def _preset_probe_app(monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    """Mount a stand-in agent route behind the wholesale gateway dependency."""
    monkeypatch.setattr(config, "TRACECAT__SERVICE_KEY", "test-service-key")
    app = FastAPI(dependencies=[Depends(enforce_agent_action_capability)])

    async def run_agent() -> dict[str, bool]:
        return {"ran": True}

    app.add_api_route("/internal/agent/run", run_agent, methods=["POST"])
    return app


def _preset_run_token(*, action: str) -> str:
    return mint_executor_token(
        workspace_id=uuid.uuid4(),
        user_id=None,
        scopes=frozenset({"*"}),
        action=action,
        wf_id="wf-1",
        wf_exec_id="run-1",
    )


def test_preset_run_mcp_servers_allowed_for_non_run_python(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An ordinary workflow caller keeps preset MCP integrations."""
    app = _preset_probe_app(monkeypatch)
    token = _preset_run_token(action="ai.preset_agent")

    with TestClient(app) as client:
        response = client.post(
            "/internal/agent/run",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "preset_slug": "p",
                "resolved_actions": [],
                "resolved_mcp_servers": [{"url": "https://mcp.test", "id": "x"}],
            },
        )

    assert response.status_code == 200


def test_preset_run_legacy_token_is_not_enforced(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A pre-grant token keeps its recorded legacy behavior."""
    app = _preset_probe_app(monkeypatch)
    now = datetime.now(UTC)
    payload = {
        "iss": EXECUTOR_TOKEN_ISSUER,
        "aud": EXECUTOR_TOKEN_AUDIENCE,
        "sub": EXECUTOR_TOKEN_SUBJECT,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=60)).timestamp()),
        "workspace_id": str(uuid.uuid4()),
        "user_id": None,
        "scopes": ["*"],
        "allowed_actions": ["core.script.run_python"],
        "action": "core.script.run_python",
        "wf_id": "wf-1",
        "wf_exec_id": "run-1",
    }
    token = jwt.encode(payload, "test-service-key", algorithm="HS256")

    with TestClient(app) as client:
        response = client.post(
            "/internal/agent/run",
            headers={"Authorization": f"Bearer {token}"},
            json={"preset_slug": "p", "resolved_actions": ["core.cases.delete_case"]},
        )

    assert response.status_code == 200


def test_agent_invoked_registry_action_token_is_not_enforced(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An Agent-invoked registry action remains governed by normal route auth."""
    app = _preset_probe_app(monkeypatch)
    token = _preset_run_token(action="tools.example.tool")

    with TestClient(app) as client:
        response = client.post(
            "/internal/agent/run",
            headers={"Authorization": f"Bearer {token}"},
            json={"preset_slug": "p", "resolved_actions": ["core.cases.delete_case"]},
        )

    assert response.status_code == 200


def test_all_action_gateway_routes_enforce_agent_capabilities() -> None:
    app = create_app()

    for route in iter_effective_api_routes(app):
        dependency_calls = {
            dependency.call for dependency in route.dependant.dependencies
        }
        assert enforce_agent_action_capability in dependency_calls, route.path
