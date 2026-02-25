"""HTTP-level tests for /info endpoint."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import status
from fastapi.testclient import TestClient

import tracecat.api.app as app_module


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
