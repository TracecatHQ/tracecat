"""Tests for the organization Git settings routes."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import tracecat.settings.router as settings_router_module
from tracecat.auth.types import Role
from tracecat.settings.schemas import GitSettingsUpdate


@pytest.mark.anyio
async def test_get_git_settings_is_available_to_scoped_org_actor(
    test_admin_role: Role,
) -> None:
    service = MagicMock()
    service.list_org_settings = AsyncMock(return_value={"git_allowed_domains": []})
    service.get_values_with_decryption_fallback.return_value = (
        {"git_allowed_domains": []},
        [],
    )

    with patch.object(settings_router_module, "SettingsService", return_value=service):
        response = await settings_router_module.get_git_settings(
            role=test_admin_role,
            session=AsyncMock(),
        )

    assert response.git_repo_url is None
    service.list_org_settings.assert_awaited_once()


@pytest.mark.anyio
async def test_update_git_settings_is_available_to_scoped_org_actor(
    test_admin_role: Role,
) -> None:
    service = MagicMock()
    service.update_git_settings = AsyncMock()
    params = GitSettingsUpdate(git_repo_url="git+ssh://git@github.com/acme/repo.git")

    with patch.object(settings_router_module, "SettingsService", return_value=service):
        await settings_router_module.update_git_settings(
            role=test_admin_role,
            session=AsyncMock(),
            params=params,
        )

    service.update_git_settings.assert_awaited_once_with(params)
