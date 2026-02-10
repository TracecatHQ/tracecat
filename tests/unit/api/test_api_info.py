"""HTTP-level tests for /info endpoint organization resolution behavior."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException, status
from fastapi.testclient import TestClient

import tracecat.api.app as app_module


@pytest.mark.anyio
async def test_info_falls_back_to_default_org_when_context_missing(
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
            "resolve_auth_organization_id",
            new=AsyncMock(
                side_effect=HTTPException(
                    status_code=status.HTTP_428_PRECONDITION_REQUIRED,
                    detail="Organization selection required",
                )
            ),
        ) as resolve_org,
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
    resolve_org.assert_awaited_once()
    get_default_org.assert_awaited_once()


@pytest.mark.anyio
async def test_info_reraises_non_precondition_org_resolution_errors(
    client: TestClient,
) -> None:
    with (
        patch.object(
            app_module,
            "resolve_auth_organization_id",
            new=AsyncMock(
                side_effect=HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Invalid organization",
                )
            ),
        ) as resolve_org,
        patch.object(
            app_module,
            "get_default_organization_id",
            new=AsyncMock(return_value="00000000-0000-0000-0000-000000000001"),
        ) as get_default_org,
        patch.object(app_module, "SettingsService") as settings_service_cls,
    ):
        response = client.get("/info?org=does-not-exist")

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert response.json()["detail"] == "Invalid organization"
    resolve_org.assert_awaited_once()
    get_default_org.assert_not_awaited()
    settings_service_cls.assert_not_called()
