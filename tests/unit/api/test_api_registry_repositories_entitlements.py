"""HTTP-level tests for custom-registry entitlement gating."""

from collections.abc import Generator
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from fastapi import status
from fastapi.testclient import TestClient

import tracecat.auth.credentials as auth_credentials_module
import tracecat.registry.repositories.router as repos_router_module
from tracecat.auth.types import Role
from tracecat.exceptions import EntitlementRequired


@pytest.fixture
def mock_role_acl_dependency(
    test_admin_role: Role,
) -> Generator[AsyncMock, None, None]:
    """Bypass RoleACL auth for route-level entitlement tests."""
    with patch.object(
        auth_credentials_module, "_role_dependency", new_callable=AsyncMock
    ) as mock_role_dependency:
        mock_role_dependency.return_value = test_admin_role
        yield mock_role_dependency


@pytest.mark.anyio
async def test_sync_custom_repository_requires_entitlement(
    client: TestClient, test_admin_role: Role, mock_role_acl_dependency: AsyncMock
) -> None:
    repository_id = uuid4()
    repository = SimpleNamespace(
        id=repository_id,
        origin="git+ssh://git@github.com/acme/custom-registry.git",
        current_version_id=None,
    )

    with (
        patch.object(repos_router_module, "RegistryReposService") as MockReposService,
        patch.object(
            repos_router_module,
            "check_entitlement",
            new_callable=AsyncMock,
        ) as mock_check_entitlement,
    ):
        mock_repos_service = AsyncMock()
        mock_repos_service.get_repository_by_id.return_value = repository
        MockReposService.return_value = mock_repos_service
        mock_check_entitlement.side_effect = EntitlementRequired("custom_registry")

        response = client.post(f"/registry/repos/{repository_id}/sync")

    assert response.status_code == status.HTTP_403_FORBIDDEN
    payload = response.json()
    assert payload["type"] == "EntitlementRequired"
    assert payload["detail"]["entitlement"] == "custom_registry"


@pytest.mark.anyio
async def test_update_custom_repository_requires_entitlement(
    client: TestClient, test_admin_role: Role, mock_role_acl_dependency: AsyncMock
) -> None:
    repository_id = uuid4()
    repository = SimpleNamespace(
        id=repository_id,
        origin="git+ssh://git@github.com/acme/custom-registry.git",
        current_version_id=None,
    )

    with (
        patch.object(repos_router_module, "RegistryReposService") as MockReposService,
        patch.object(
            repos_router_module, "RegistryActionsService"
        ) as MockActionsService,
        patch.object(
            repos_router_module,
            "check_entitlement",
            new_callable=AsyncMock,
        ) as mock_check_entitlement,
    ):
        mock_repos_service = AsyncMock()
        mock_repos_service.get_repository_by_id.return_value = repository
        MockReposService.return_value = mock_repos_service
        MockActionsService.return_value = AsyncMock()
        mock_check_entitlement.side_effect = EntitlementRequired("custom_registry")

        response = client.patch(
            f"/registry/repos/{repository_id}",
            json={"commit_sha": "a" * 40},
        )

    assert response.status_code == status.HTTP_403_FORBIDDEN
    payload = response.json()
    assert payload["type"] == "EntitlementRequired"
    assert payload["detail"]["entitlement"] == "custom_registry"


@pytest.mark.anyio
async def test_delete_custom_repository_requires_entitlement(
    client: TestClient, test_admin_role: Role, mock_role_acl_dependency: AsyncMock
) -> None:
    repository_id = uuid4()
    repository = SimpleNamespace(
        id=repository_id,
        origin="git+ssh://git@github.com/acme/custom-registry.git",
    )

    with (
        patch.object(repos_router_module, "RegistryReposService") as MockReposService,
        patch.object(
            repos_router_module,
            "check_entitlement",
            new_callable=AsyncMock,
        ) as mock_check_entitlement,
    ):
        mock_repos_service = AsyncMock()
        mock_repos_service.get_repository_by_id.return_value = repository
        MockReposService.return_value = mock_repos_service
        mock_check_entitlement.side_effect = EntitlementRequired("custom_registry")

        response = client.delete(f"/registry/repos/{repository_id}")

    assert response.status_code == status.HTTP_403_FORBIDDEN
    payload = response.json()
    assert payload["type"] == "EntitlementRequired"
    assert payload["detail"]["entitlement"] == "custom_registry"


@pytest.mark.anyio
async def test_promote_custom_repository_requires_entitlement(
    client: TestClient, test_admin_role: Role, mock_role_acl_dependency: AsyncMock
) -> None:
    repository_id = uuid4()
    version_id = uuid4()
    repository = SimpleNamespace(
        id=repository_id,
        origin="git+ssh://git@github.com/acme/custom-registry.git",
        current_version_id=None,
    )

    with (
        patch.object(repos_router_module, "RegistryReposService") as MockReposService,
        patch.object(
            repos_router_module,
            "check_entitlement",
            new_callable=AsyncMock,
        ) as mock_check_entitlement,
    ):
        mock_repos_service = AsyncMock()
        mock_repos_service.get_repository_by_id.return_value = repository
        MockReposService.return_value = mock_repos_service
        mock_check_entitlement.side_effect = EntitlementRequired("custom_registry")

        response = client.post(
            f"/registry/repos/{repository_id}/versions/{version_id}/promote"
        )

    assert response.status_code == status.HTTP_403_FORBIDDEN
    payload = response.json()
    assert payload["type"] == "EntitlementRequired"
    assert payload["detail"]["entitlement"] == "custom_registry"
