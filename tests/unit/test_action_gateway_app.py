from __future__ import annotations

import uuid

import pytest
from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.testclient import TestClient

from tests._route_utils import iter_effective_api_routes
from tracecat import config
from tracecat.auth.executor_tokens import (
    ExecutorTokenPayload,
    RunPythonOrigin,
    mint_executor_token,
)
from tracecat.executor.action_gateway import app as action_gateway_app
from tracecat.executor.action_gateway.app import (
    create_app,
    request_logging_middleware,
    validation_exception_handler,
)
from tracecat.executor.action_gateway.capabilities import (
    GATEWAY_CAPABILITIES,
    GATEWAY_DENIED_ROUTES,
    GATEWAY_EXEMPT_ROUTES,
    GatewayActionRequirement,
    GatewayCapability,
    GatewayRouteKey,
    _agent_gateway_action_allowed,
    enforce_agent_action_capability,
    enforce_run_python_agent_config,
    gateway_route_key,
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


def _gateway_route_keys(app: FastAPI) -> set[GatewayRouteKey]:
    keys: set[GatewayRouteKey] = set()
    for route in iter_effective_api_routes(app):
        for method in route.methods:
            key = gateway_route_key(method, route.path)
            assert key is not None, f"Unsupported Action Gateway method: {method}"
            keys.add(key)
    return keys


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


def test_agent_run_python_denies_unconfigured_gateway_action(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config, "TRACECAT__SERVICE_KEY", "test-service-key")
    app = create_app()
    token = mint_executor_token(
        workspace_id=uuid.uuid4(),
        user_id=None,
        scopes=frozenset({"*"}),
        allowed_actions=frozenset({"core.script.run_python"}),
        action="core.script.run_python",
        wf_id="wf-1",
        wf_exec_id="run-1",
    )

    with TestClient(app) as client:
        response = client.get(
            "/internal/cases",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 403
    assert response.json()["detail"]["error"]["code"] == "action_not_allowed"


def test_agent_gateway_grant_is_upper_bound_of_caller_scopes() -> None:
    superuser = ExecutorTokenPayload(
        workspace_id=uuid.uuid4(),
        user_id=None,
        scopes=frozenset({"*"}),
        allowed_actions=frozenset({"action.a", "action.b"}),
        action="core.script.run_python",
        wf_id="wf-1",
        wf_exec_id="run-1",
    )
    caller_with_only_a = superuser.model_copy(
        update={"scopes": frozenset({"action:action.a:execute"})}
    )
    action_a = GatewayActionRequirement(any_of=frozenset({"action.a"}))
    action_b = GatewayActionRequirement(any_of=frozenset({"action.b"}))

    assert _agent_gateway_action_allowed(superuser, action_a)
    assert _agent_gateway_action_allowed(superuser, action_b)
    assert _agent_gateway_action_allowed(caller_with_only_a, action_a)
    assert not _agent_gateway_action_allowed(caller_with_only_a, action_b)


def test_agent_run_python_allows_configured_gateway_action(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config, "TRACECAT__SERVICE_KEY", "test-service-key")
    app = FastAPI(dependencies=[Depends(enforce_agent_action_capability)])

    async def list_cases() -> dict[str, bool]:
        return {"ok": True}

    app.add_api_route("/internal/cases", list_cases, methods=["GET"])
    monkeypatch.setitem(
        GATEWAY_CAPABILITIES,
        GatewayRouteKey("GET", "/internal/cases"),
        GatewayCapability(
            method="GET",
            path="/internal/cases",
            actions=frozenset({"core.cases.list_cases"}),
        ),
    )

    token = mint_executor_token(
        workspace_id=uuid.uuid4(),
        user_id=None,
        scopes=frozenset({"*"}),
        allowed_actions=frozenset({"core.script.run_python", "core.cases.list_cases"}),
        action="core.script.run_python",
        wf_id="wf-1",
        wf_exec_id="run-1",
    )

    with TestClient(app) as client:
        response = client.get(
            "/internal/cases",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 200
    assert response.json() == {"ok": True}


def test_registry_template_run_python_skips_agent_ceiling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A registry-locked template's run_python step is exempt from the ceiling.

    An agent-provenance token with the same (missing) grant is rejected; the
    template-provenance token reaches the route because its internal SDK calls
    are trusted by the registry lock (caller-scope RBAC still governs it).
    """
    monkeypatch.setattr(config, "TRACECAT__SERVICE_KEY", "test-service-key")
    app = FastAPI(dependencies=[Depends(enforce_agent_action_capability)])

    async def list_cases() -> dict[str, bool]:
        return {"ok": True}

    app.add_api_route("/internal/cases", list_cases, methods=["GET"])
    monkeypatch.setitem(
        GATEWAY_CAPABILITIES,
        GatewayRouteKey("GET", "/internal/cases"),
        GatewayCapability(
            method="GET",
            path="/internal/cases",
            actions=frozenset({"core.cases.list_cases"}),
        ),
    )

    # Grant intentionally omits core.cases.list_cases: an agent script would be
    # denied, but a locked template step is trusted.
    def _mint(origin: RunPythonOrigin) -> str:
        return mint_executor_token(
            workspace_id=uuid.uuid4(),
            user_id=None,
            scopes=frozenset({"*"}),
            allowed_actions=frozenset({"core.script.run_python", "tools.example.tmpl"}),
            action="core.script.run_python",
            run_python_origin=origin,
            wf_id="wf-1",
            wf_exec_id="run-1",
        )

    agent_token = _mint("agent")
    template_token = _mint("registry_template")

    with TestClient(app) as client:
        agent_response = client.get(
            "/internal/cases", headers={"Authorization": f"Bearer {agent_token}"}
        )
        template_response = client.get(
            "/internal/cases",
            headers={"Authorization": f"Bearer {template_token}"},
        )

    assert agent_response.status_code == 403
    assert template_response.status_code == 200
    assert template_response.json() == {"ok": True}


def test_agent_route_capability_does_not_follow_endpoint_aliases(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config, "TRACECAT__SERVICE_KEY", "test-service-key")
    app = FastAPI(dependencies=[Depends(enforce_agent_action_capability)])

    async def list_cases() -> dict[str, bool]:
        return {"ok": True}

    app.add_api_route("/internal/cases", list_cases, methods=["GET"])
    app.add_api_route("/internal/cases", list_cases, methods=["POST"])
    app.add_api_route("/internal/cases-alias", list_cases, methods=["GET"])
    monkeypatch.setitem(
        GATEWAY_CAPABILITIES,
        GatewayRouteKey("GET", "/internal/cases"),
        GatewayCapability(
            method="GET",
            path="/internal/cases",
            actions=frozenset({"core.cases.list_cases"}),
        ),
    )
    token = mint_executor_token(
        workspace_id=uuid.uuid4(),
        user_id=None,
        scopes=frozenset({"*"}),
        allowed_actions=frozenset({"core.script.run_python", "core.cases.list_cases"}),
        action="core.script.run_python",
        wf_id="wf-1",
        wf_exec_id="run-1",
    )
    headers = {"Authorization": f"Bearer {token}"}

    with TestClient(app) as client:
        assert client.get("/internal/cases", headers=headers).status_code == 200
        assert client.post("/internal/cases", headers=headers).status_code == 403
        assert client.get("/internal/cases-alias", headers=headers).status_code == 403


def test_agent_run_python_denies_unmapped_gateway_route(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config, "TRACECAT__SERVICE_KEY", "test-service-key")
    app = FastAPI(dependencies=[Depends(enforce_agent_action_capability)])

    async def unmapped() -> dict[str, bool]:
        return {"ok": True}

    app.add_api_route("/internal/unmapped", unmapped, methods=["GET"])

    token = mint_executor_token(
        workspace_id=uuid.uuid4(),
        user_id=None,
        scopes=frozenset({"*"}),
        allowed_actions=frozenset({"core.script.run_python", "core.cases.list_cases"}),
        action="core.script.run_python",
        wf_id="wf-1",
        wf_exec_id="run-1",
    )

    with TestClient(app) as client:
        response = client.get(
            "/internal/unmapped",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 403


def _preset_probe_app(monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    """App mounting a stand-in agent-run route that enforces a resolved toolset.

    Mirrors ``run_agent_endpoint``: the gateway dependency authorizes the outer
    ``ai.preset_agent`` operation, then the route re-checks a resolved config.
    """
    monkeypatch.setattr(config, "TRACECAT__SERVICE_KEY", "test-service-key")
    app = FastAPI(dependencies=[Depends(enforce_agent_action_capability)])

    async def run_agent(request: Request) -> dict[str, bool]:
        body = await request.json()
        enforce_run_python_agent_config(
            request,
            actions=body.get("resolved_actions"),
            base_url=body.get("resolved_base_url"),
            mcp_servers=body.get("resolved_mcp_servers"),
        )
        return {"ran": True}

    app.add_api_route("/internal/agent/run", run_agent, methods=["POST"])
    return app


def _preset_run_token(*allowed: str, action: str = "core.script.run_python") -> str:
    return mint_executor_token(
        workspace_id=uuid.uuid4(),
        user_id=None,
        scopes=frozenset({"*"}),
        allowed_actions=frozenset({"core.script.run_python", *allowed}),
        action=action,
        wf_id="wf-1",
        wf_exec_id="run-1",
    )


def test_preset_run_denies_laundered_action(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A preset resolving to an ungranted action is rejected at the run endpoint."""
    app = _preset_probe_app(monkeypatch)
    # Outer grant covers the preset run, but not the laundered inner action.
    token = _preset_run_token("ai.preset_agent")

    with TestClient(app) as client:
        response = client.post(
            "/internal/agent/run",
            headers={"Authorization": f"Bearer {token}"},
            json={"preset_slug": "p", "resolved_actions": ["core.cases.delete_case"]},
        )

    assert response.status_code == 403
    assert response.json()["detail"]["error"]["code"] == "action_not_allowed"


def test_preset_run_denies_resolved_mcp_servers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A preset resolving to MCP servers is rejected for a nested run_python run."""
    app = _preset_probe_app(monkeypatch)
    token = _preset_run_token("ai.preset_agent")

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

    assert response.status_code == 403
    assert response.json()["detail"]["error"]["code"] == "action_not_allowed"


def test_preset_run_mcp_servers_allowed_for_non_run_python(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A non-run_python caller (ordinary workflow) keeps preset MCP integrations."""
    app = _preset_probe_app(monkeypatch)
    token = _preset_run_token("ai.preset_agent", action="ai.preset_agent")

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


def test_preset_run_denies_laundered_base_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A preset resolving to a custom base URL is rejected at the run endpoint."""
    app = _preset_probe_app(monkeypatch)
    token = _preset_run_token("ai.preset_agent")

    with TestClient(app) as client:
        response = client.post(
            "/internal/agent/run",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "preset_slug": "p",
                "resolved_actions": [],
                "resolved_base_url": "https://evil.test",
            },
        )

    assert response.status_code == 403


def test_preset_run_allows_granted_actions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A preset whose resolved actions are all granted reaches the runtime."""
    app = _preset_probe_app(monkeypatch)
    token = _preset_run_token("ai.preset_agent", "core.cases.get_case")

    with TestClient(app) as client:
        response = client.post(
            "/internal/agent/run",
            headers={"Authorization": f"Bearer {token}"},
            json={"preset_slug": "p", "resolved_actions": ["core.cases.get_case"]},
        )

    assert response.status_code == 200
    assert response.json() == {"ran": True}


def test_preset_run_legacy_token_is_not_enforced(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A pre-grant (allowed_actions=None) token keeps its legacy behavior."""
    app = _preset_probe_app(monkeypatch)
    token = mint_executor_token(
        workspace_id=uuid.uuid4(),
        user_id=None,
        scopes=frozenset({"*"}),
        allowed_actions=None,
        action="core.script.run_python",
        wf_id="wf-1",
        wf_exec_id="run-1",
    )

    with TestClient(app) as client:
        response = client.post(
            "/internal/agent/run",
            headers={"Authorization": f"Bearer {token}"},
            json={"preset_slug": "p", "resolved_actions": ["core.cases.delete_case"]},
        )

    assert response.status_code == 200


def test_preset_run_non_run_python_token_is_not_enforced(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A non-run_python executor token (trusted workflow config) is unaffected."""
    app = _preset_probe_app(monkeypatch)
    token = _preset_run_token("ai.preset_agent", action="ai.preset_agent")

    with TestClient(app) as client:
        response = client.post(
            "/internal/agent/run",
            headers={"Authorization": f"Bearer {token}"},
            json={"preset_slug": "p", "resolved_actions": ["core.cases.delete_case"]},
        )

    assert response.status_code == 200


def test_gateway_action_declarations_cover_mounted_routes() -> None:
    app = create_app()
    effective_routes = _gateway_route_keys(app)
    mapped_routes = GATEWAY_CAPABILITIES.keys()

    assert mapped_routes.isdisjoint(GATEWAY_DENIED_ROUTES)
    assert mapped_routes.isdisjoint(GATEWAY_EXEMPT_ROUTES)
    assert GATEWAY_DENIED_ROUTES.isdisjoint(GATEWAY_EXEMPT_ROUTES)
    assert (
        mapped_routes | GATEWAY_DENIED_ROUTES | GATEWAY_EXEMPT_ROUTES
        == effective_routes
    )


def test_all_action_gateway_routes_enforce_agent_capabilities() -> None:
    app = create_app()

    for route in iter_effective_api_routes(app):
        dependency_calls = {
            dependency.call for dependency in route.dependant.dependencies
        }
        assert enforce_agent_action_capability in dependency_calls, route.path
