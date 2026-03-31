"""HTTP-level tests for /info endpoint."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import starlette.routing
from fastapi import status
from fastapi.testclient import TestClient

import tracecat.api.app as app_module
from tracecat.auth.enums import AuthType


@pytest.mark.anyio
async def test_info_returns_saml_settings_from_default_org(
    client: TestClient,
) -> None:
    settings_service = MagicMock()
    settings_service.list_org_settings = AsyncMock(
        return_value=[
            SimpleNamespace(key="saml_enabled"),
            SimpleNamespace(key="saml_enforced"),
        ]
    )
    settings_service.get_value.side_effect = lambda setting: (
        setting.key == "saml_enabled"
    )

    with (
        patch.object(
            app_module,
            "get_default_organization_id",
            new=AsyncMock(return_value="00000000-0000-0000-0000-000000000001"),
        ) as get_default_org,
        patch.object(app_module, "bootstrap_role", return_value=MagicMock()),
        patch.object(app_module, "SettingsService", return_value=settings_service),
        patch.object(app_module, "get_setting_override", return_value=None),
    ):
        response = client.get("/info")

    assert response.status_code == status.HTTP_200_OK
    assert response.json()["saml_enabled"] is True
    assert response.json()["saml_enforced"] is False
    get_default_org.assert_awaited_once()


@pytest.mark.anyio
async def test_info_filters_auth_types_to_explicit_mode(
    client: TestClient,
) -> None:
    settings_service = MagicMock()
    settings_service.list_org_settings = AsyncMock(
        return_value=[
            SimpleNamespace(key="saml_enabled"),
            SimpleNamespace(key="saml_enforced"),
        ]
    )
    settings_service.get_value.return_value = True

    with (
        patch.object(
            app_module,
            "get_default_organization_id",
            new=AsyncMock(return_value="00000000-0000-0000-0000-000000000001"),
        ),
        patch.object(app_module, "bootstrap_role", return_value=MagicMock()),
        patch.object(app_module, "SettingsService", return_value=settings_service),
        patch.object(app_module, "get_setting_override", return_value=None),
        patch.object(app_module.config, "MCP_AUTH_MODE", "saml"),
        patch.object(
            app_module.config,
            "TRACECAT__AUTH_TYPES",
            {AuthType.BASIC, AuthType.OIDC, AuthType.SAML},
        ),
    ):
        response = client.get("/info")

    assert response.status_code == status.HTTP_200_OK
    assert response.json()["auth_allowed_types"] == ["saml"]


def test_create_app_skips_basic_and_oidc_routes_when_explicit_saml_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(app_module.config, "MCP_AUTH_MODE", "saml")
    monkeypatch.setattr(
        app_module.config,
        "TRACECAT__AUTH_TYPES",
        {AuthType.BASIC, AuthType.OIDC, AuthType.SAML},
    )
    monkeypatch.setattr(app_module, "oidc_login_configured", lambda: True)

    app = app_module.create_app()
    route_paths = {
        route.path for route in app.routes if isinstance(route, starlette.routing.Route)
    }

    assert "/auth/login" not in route_paths
    assert "/auth/register" not in route_paths
    assert "/auth/oauth/authorize" not in route_paths
    assert "/auth/saml/login" in route_paths
    assert "/auth/discover" in route_paths
