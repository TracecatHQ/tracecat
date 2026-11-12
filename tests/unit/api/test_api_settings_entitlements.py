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


@pytest.mark.anyio
async def test_update_git_settings_removes_replaced_repository(
    client: TestClient, test_admin_role: Role
) -> None:
    old_url = "git+ssh://git@github.com/acme/old.git"
    new_url = "git+ssh://git@github.com/acme/new.git"
    with (
        patch.object(
            settings_router_module, "check_entitlement", new_callable=AsyncMock
        ),
        patch.object(
            settings_router_module,
            "get_setting",
            new=AsyncMock(return_value=old_url),
        ),
        patch.object(
            settings_router_module.SettingsService,
            "update_git_settings",
            new_callable=AsyncMock,
        ),
        patch.object(
            settings_router_module,
            "remove_replaced_custom_repository",
            new_callable=AsyncMock,
        ) as mock_remove,
    ):
        response = client.patch("/settings/git", json={"git_repo_url": new_url})

    assert response.status_code == status.HTTP_204_NO_CONTENT
    mock_remove.assert_awaited_once()
    assert mock_remove.await_args is not None
    assert mock_remove.await_args.kwargs == {
        "previous_url": old_url,
        "new_url": new_url,
    }


@pytest.mark.anyio
async def test_update_git_settings_without_url_keeps_repository(
    client: TestClient, test_admin_role: Role
) -> None:
    with (
        patch.object(
            settings_router_module, "check_entitlement", new_callable=AsyncMock
        ),
        patch.object(
            settings_router_module,
            "get_setting",
            new=AsyncMock(return_value="git+ssh://git@github.com/acme/old.git"),
        ),
        patch.object(
            settings_router_module.SettingsService,
            "update_git_settings",
            new_callable=AsyncMock,
        ),
        patch.object(
            settings_router_module,
            "remove_replaced_custom_repository",
            new_callable=AsyncMock,
        ) as mock_remove,
    ):
        response = client.patch(
            "/settings/git", json={"git_allowed_domains": ["github.com"]}
        )

    assert response.status_code == status.HTTP_204_NO_CONTENT
    mock_remove.assert_not_awaited()
