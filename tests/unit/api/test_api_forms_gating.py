"""Every forms route is gated on the feature flag and the entitlement."""

import re
import uuid
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from tests._route_utils import EffectiveRoute, iter_effective_api_routes
from tracecat import config
from tracecat.api.app import app
from tracecat.auth.types import Role
from tracecat.feature_flags import FeatureFlag
from tracecat.forms.dependencies import (
    require_forms_enabled,
    require_forms_enabled_for_org,
)

FORMS_PREFIXES = ("/workspaces/{workspace_id}/forms", "/forms", "/intake")
GATES = {require_forms_enabled, require_forms_enabled_for_org}


def _forms_routes() -> list[EffectiveRoute]:
    return [
        route
        for route in iter_effective_api_routes(app)
        if any(
            route.path == prefix or route.path.startswith(f"{prefix}/")
            for prefix in FORMS_PREFIXES
        )
    ]


FORMS_ROUTES = _forms_routes()


def _route_id(route: EffectiveRoute) -> str:
    return f"{'|'.join(sorted(route.methods))} {route.path}"


def _request(client: TestClient, route: EffectiveRoute, role: Role):
    path = re.sub(r"\{workspace_id\}", str(role.workspace_id), route.path)
    path = re.sub(r"\{[a-z_]+\}", str(uuid.uuid4()), path)
    [method] = route.methods
    params = (
        None
        if "{workspace_id}" in route.path
        else {"workspace_id": str(role.workspace_id)}
    )
    return client.request(method, path, params=params, json={})


def test_forms_routes_are_mounted() -> None:
    paths = {route.path for route in FORMS_ROUTES}

    assert "/workspaces/{workspace_id}/forms" in paths
    assert "/workspaces/{workspace_id}/forms/{form_id}/render" in paths
    assert "/intake/forms" in paths
    assert "/intake/forms/{form_id}" in paths
    assert "/intake/submissions" in paths


@pytest.mark.parametrize("route", FORMS_ROUTES, ids=_route_id)
def test_every_forms_route_has_the_gate(route: EffectiveRoute) -> None:
    calls = {dependency.call for dependency in route.dependant.dependencies}

    assert calls & GATES


@pytest.mark.anyio
@pytest.mark.parametrize("route", FORMS_ROUTES, ids=_route_id)
async def test_flag_off_returns_404(
    client: TestClient,
    test_admin_role: Role,
    monkeypatch: pytest.MonkeyPatch,
    route: EffectiveRoute,
) -> None:
    monkeypatch.setattr(config, "TRACECAT__FEATURE_FLAGS", set())
    is_org_entitled = AsyncMock(return_value=True)

    with patch("tracecat.tiers.access.is_org_entitled", new=is_org_entitled):
        response = _request(client, route, test_admin_role)

    assert response.status_code == 404
    assert response.json() == {"detail": "Feature not enabled"}
    is_org_entitled.assert_not_awaited()


@pytest.mark.anyio
@pytest.mark.parametrize("route", FORMS_ROUTES, ids=_route_id)
async def test_entitlement_off_returns_403(
    client: TestClient,
    test_admin_role: Role,
    monkeypatch: pytest.MonkeyPatch,
    route: EffectiveRoute,
) -> None:
    monkeypatch.setattr(config, "TRACECAT__FEATURE_FLAGS", {FeatureFlag.FORMS})

    with patch(
        "tracecat.tiers.access.is_org_entitled", new=AsyncMock(return_value=False)
    ):
        response = _request(client, route, test_admin_role)

    assert response.status_code == 403
    assert response.json()["type"] == "EntitlementRequired"
