"""Trigger routes over HTTP: the entitlement gate and the error responses."""

import re
import uuid
from typing import Any
from unittest.mock import AsyncMock, Mock, patch

import orjson
import pytest
from fastapi.testclient import TestClient

from tests._route_utils import EffectiveRoute, iter_effective_api_routes
from tracecat.api.app import app as api_app
from tracecat.auth.types import Role
from tracecat.triggers.dependencies import require_event_sources_enabled
from tracecat.triggers.enums import TriggerErrorCode
from tracecat.triggers.exceptions import TriggerError, trigger_error_handler

_PREFIXES = (
    "/workspaces/{workspace_id}/event-sources",
    "/workspaces/{workspace_id}/triggers",
)
ROUTES = [
    route
    for route in iter_effective_api_routes(api_app)
    if any(route.path == p or route.path.startswith(f"{p}/") for p in _PREFIXES)
]


def test_the_api_serves_the_trigger_routes() -> None:
    served = {(method, route.path) for route in ROUTES for method in route.methods}
    sources = "/workspaces/{workspace_id}/event-sources"
    triggers = "/workspaces/{workspace_id}/triggers"
    bindings = f"{triggers}/{{trigger_id}}/bindings"

    assert served == {
        ("GET", sources),
        ("POST", sources),
        ("GET", f"{sources}/{{source_id}}"),
        ("PATCH", f"{sources}/{{source_id}}"),
        ("GET", triggers),
        ("POST", triggers),
        ("GET", f"{triggers}/{{trigger_id}}"),
        ("PATCH", f"{triggers}/{{trigger_id}}"),
        ("DELETE", f"{triggers}/{{trigger_id}}"),
        ("POST", bindings),
        ("PATCH", f"{bindings}/{{binding_id}}"),
        ("DELETE", f"{bindings}/{{binding_id}}"),
    }


SOURCE_ROUTES = [route for route in ROUTES if route.path.startswith(_PREFIXES[0])]
TRIGGER_ROUTES = [route for route in ROUTES if route.path.startswith(_PREFIXES[1])]


def _path(route: EffectiveRoute, role: Role) -> str:
    path = route.path.replace("{workspace_id}", str(role.workspace_id))
    return re.sub(r"\{[a-z_]+\}", str(uuid.uuid4()), path)


@pytest.mark.anyio
@pytest.mark.parametrize(
    "route",
    SOURCE_ROUTES,
    ids=[f"{sorted(r.methods)} {r.path}" for r in SOURCE_ROUTES],
)
async def test_every_event_source_route_needs_the_entitlement(
    client: TestClient, test_admin_role: Role, route: EffectiveRoute
) -> None:
    [method] = route.methods
    is_org_entitled = AsyncMock(return_value=False)

    with patch("tracecat.tiers.access.is_org_entitled", new=is_org_entitled):
        response = client.request(method, _path(route, test_admin_role), json={})

    assert response.status_code == 403
    assert response.json()["type"] == "EntitlementRequired"
    is_org_entitled.assert_awaited()


@pytest.mark.parametrize(
    "route",
    TRIGGER_ROUTES,
    ids=[f"{sorted(r.methods)} {r.path}" for r in TRIGGER_ROUTES],
)
def test_trigger_routes_are_open_to_every_plan(route: EffectiveRoute) -> None:
    """The service checks each trigger kind's entitlement, not the router."""
    calls = {dep.call for dep in route.dependant.dependencies}

    assert require_event_sources_enabled not in calls


@pytest.mark.parametrize(
    "route",
    SOURCE_ROUTES,
    ids=[f"{sorted(r.methods)} {r.path}" for r in SOURCE_ROUTES],
)
def test_event_source_routes_carry_the_gate(route: EffectiveRoute) -> None:
    calls = {dep.call for dep in route.dependant.dependencies}

    assert require_event_sources_enabled in calls


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("code", "status_code"),
    [
        (TriggerErrorCode.NOT_FOUND, 404),
        (TriggerErrorCode.CONFLICT, 409),
        (TriggerErrorCode.INVALID, 400),
    ],
)
async def test_trigger_errors_answer_with_their_status_and_code(
    code: TriggerErrorCode, status_code: int
) -> None:
    response = await trigger_error_handler(Mock(), TriggerError(code, "Wrong"))

    assert response.status_code == status_code
    body: dict[str, Any] = orjson.loads(response.body)
    assert body == {"detail": {"code": code.value, "message": "Wrong"}}
