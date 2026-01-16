"""HTTP-level tests for admin settings API endpoints."""

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import status
from fastapi.testclient import TestClient
from tracecat_ee.admin.settings import router as settings_router
from tracecat_ee.admin.settings.schemas import PlatformRegistrySettingsRead

from tracecat.auth.types import Role


@pytest.mark.anyio
async def test_get_registry_settings_success(
    client: TestClient, test_admin_role: Role
) -> None:
    settings = PlatformRegistrySettingsRead(
        git_repo_url="https://example.com/repo.git",
        git_repo_package_name="tracecat_registry",
        git_allowed_domains={"example.com"},
    )

    with patch.object(settings_router, "AdminSettingsService") as MockService:
        mock_svc = AsyncMock()
        mock_svc.get_registry_settings.return_value = settings
        MockService.return_value = mock_svc

        response = client.get("/admin/settings/registry")

    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["git_repo_url"] == "https://example.com/repo.git"


@pytest.mark.anyio
async def test_update_registry_settings_success(
    client: TestClient, test_admin_role: Role
) -> None:
    settings = PlatformRegistrySettingsRead(
        git_repo_url="https://example.com/new.git",
        git_repo_package_name="tracecat_registry",
        git_allowed_domains={"example.com"},
    )

    with patch.object(settings_router, "AdminSettingsService") as MockService:
        mock_svc = AsyncMock()
        mock_svc.update_registry_settings.return_value = settings
        MockService.return_value = mock_svc

        response = client.patch(
            "/admin/settings/registry",
            json={"git_repo_url": "https://example.com/new.git"},
        )

    assert response.status_code == status.HTTP_200_OK
    assert response.json()["git_repo_url"] == "https://example.com/new.git"
