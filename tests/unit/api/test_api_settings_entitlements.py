"""HTTP-level tests for settings entitlement gating."""

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import status
from fastapi.testclient import TestClient

import tracecat.settings.router as settings_router_module
from tracecat.auth.types import Role
from tracecat.exceptions import EntitlementRequired


@pytest.mark.anyio
async def test_get_git_settings_requires_custom_registry_entitlement(
    client: TestClient, test_admin_role: Role
) -> None:
    with patch.object(
        settings_router_module,
        "check_entitlement",
        new_callable=AsyncMock,
    ) as mock_check_entitlement:
        mock_check_entitlement.side_effect = EntitlementRequired("custom_registry")

        response = client.get("/settings/git")

    assert response.status_code == status.HTTP_403_FORBIDDEN
    mock_check_entitlement.assert_awaited_once()


@pytest.mark.anyio
async def test_update_git_settings_requires_custom_registry_entitlement(
    client: TestClient, test_admin_role: Role
) -> None:
    with patch.object(
        settings_router_module,
        "check_entitlement",
        new_callable=AsyncMock,
    ) as mock_check_entitlement:
        mock_check_entitlement.side_effect = EntitlementRequired("custom_registry")

        response = client.patch(
            "/settings/git",
            json={"git_repo_url": "git+ssh://git@github.com/acme/repo.git"},
        )

    assert response.status_code == status.HTTP_403_FORBIDDEN
    mock_check_entitlement.assert_awaited_once()
