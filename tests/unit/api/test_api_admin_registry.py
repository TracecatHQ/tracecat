"""HTTP-level tests for admin registry API endpoints."""

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest
import tracecat_ee.admin.registry.router as registry_router_module
from fastapi import status
from fastapi.testclient import TestClient
from tracecat_ee.admin.registry.schemas import (
    RegistryStatusResponse,
    RegistrySyncResponse,
    RegistryVersionPromoteResponse,
    RegistryVersionRead,
    RepositoryStatus,
    RepositorySyncResult,
)

from tracecat.auth.types import Role


@pytest.mark.anyio
async def test_sync_all_repositories_success(
    client: TestClient, test_admin_role: Role
) -> None:
    now = datetime(2024, 1, 1, tzinfo=UTC)
    repo_id = uuid.uuid4()
    payload = RegistrySyncResponse(
        success=True,
        synced_at=now,
        repositories=[
            RepositorySyncResult(
                repository_id=repo_id,
                repository_name="test",
                success=True,
            )
        ],
    )

    with patch.object(registry_router_module, "AdminRegistryService") as MockService:
        mock_svc = AsyncMock()
        mock_svc.sync_all_repositories.return_value = payload
        MockService.return_value = mock_svc

        response = client.post("/admin/registry/sync")

    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["success"] is True
    assert data["repositories"][0]["repository_id"] == str(repo_id)


@pytest.mark.anyio
async def test_get_registry_status_success(
    client: TestClient, test_admin_role: Role
) -> None:
    now = datetime(2024, 1, 1, tzinfo=UTC)
    payload = RegistryStatusResponse(
        total_repositories=1,
        last_sync_at=now,
        repositories=[
            RepositoryStatus(
                id=uuid.uuid4(),
                name="test",
                origin="tracecat_registry",
                last_synced_at=now,
                commit_sha="abc123",
            )
        ],
    )

    with patch.object(registry_router_module, "AdminRegistryService") as MockService:
        mock_svc = AsyncMock()
        mock_svc.get_status.return_value = payload
        MockService.return_value = mock_svc

        response = client.get("/admin/registry/status")

    assert response.status_code == status.HTTP_200_OK
    assert response.json()["total_repositories"] == 1


@pytest.mark.anyio
async def test_list_registry_versions_success(
    client: TestClient, test_admin_role: Role
) -> None:
    now = datetime(2024, 1, 1, tzinfo=UTC)
    payload = [
        RegistryVersionRead(
            id=uuid.uuid4(),
            repository_id=uuid.uuid4(),
            version="1.0.0",
            commit_sha="abc123",
            tarball_uri=None,
            created_at=now,
        )
    ]

    with patch.object(registry_router_module, "AdminRegistryService") as MockService:
        mock_svc = AsyncMock()
        mock_svc.list_versions.return_value = payload
        MockService.return_value = mock_svc

        response = client.get("/admin/registry/versions")

    assert response.status_code == status.HTTP_200_OK
    assert response.json()[0]["version"] == "1.0.0"


@pytest.mark.anyio
async def test_promote_registry_version_success(
    client: TestClient, test_admin_role: Role
) -> None:
    repo_id = uuid.uuid4()
    version_id = uuid.uuid4()
    prev_version_id = uuid.uuid4()
    payload = RegistryVersionPromoteResponse(
        repository_id=repo_id,
        origin="tracecat_registry",
        previous_version_id=prev_version_id,
        current_version_id=version_id,
        version="1.0.0",
    )

    with patch.object(registry_router_module, "AdminRegistryService") as MockService:
        mock_svc = AsyncMock()
        mock_svc.promote_version.return_value = payload
        MockService.return_value = mock_svc

        response = client.post(
            f"/admin/registry/{repo_id}/versions/{version_id}/promote"
        )

    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["repository_id"] == str(repo_id)
    assert data["current_version_id"] == str(version_id)
    assert data["previous_version_id"] == str(prev_version_id)
    assert data["version"] == "1.0.0"


@pytest.mark.anyio
async def test_promote_registry_version_not_found(
    client: TestClient, test_admin_role: Role
) -> None:
    repo_id = uuid.uuid4()
    version_id = uuid.uuid4()

    with patch.object(registry_router_module, "AdminRegistryService") as MockService:
        mock_svc = AsyncMock()
        mock_svc.promote_version.side_effect = ValueError("Repository not found")
        MockService.return_value = mock_svc

        response = client.post(
            f"/admin/registry/{repo_id}/versions/{version_id}/promote"
        )

    assert response.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.anyio
async def test_sync_all_repositories_with_force(
    client: TestClient, test_admin_role: Role
) -> None:
    """Test that force=true query parameter is passed to the service."""
    now = datetime(2024, 1, 1, tzinfo=UTC)
    repo_id = uuid.uuid4()
    payload = RegistrySyncResponse(
        success=True,
        synced_at=now,
        repositories=[
            RepositorySyncResult(
                repository_id=repo_id,
                repository_name="test",
                success=True,
                version="1.0.0",
                actions_count=10,
            )
        ],
    )

    with patch.object(registry_router_module, "AdminRegistryService") as MockService:
        mock_svc = AsyncMock()
        mock_svc.sync_all_repositories.return_value = payload
        MockService.return_value = mock_svc

        response = client.post("/admin/registry/sync?force=true")

    assert response.status_code == status.HTTP_200_OK
    # Verify force=True was passed to the service
    mock_svc.sync_all_repositories.assert_called_once_with(force=True)


@pytest.mark.anyio
async def test_sync_repository_with_force(
    client: TestClient, test_admin_role: Role
) -> None:
    """Test that force=true query parameter is passed to the service for specific repo."""
    now = datetime(2024, 1, 1, tzinfo=UTC)
    repo_id = uuid.uuid4()
    payload = RegistrySyncResponse(
        success=True,
        synced_at=now,
        repositories=[
            RepositorySyncResult(
                repository_id=repo_id,
                repository_name="test",
                success=True,
                version="1.0.0",
                actions_count=10,
            )
        ],
    )

    with patch.object(registry_router_module, "AdminRegistryService") as MockService:
        mock_svc = AsyncMock()
        mock_svc.sync_repository.return_value = payload
        MockService.return_value = mock_svc

        response = client.post(f"/admin/registry/sync/{repo_id}?force=true")

    assert response.status_code == status.HTTP_200_OK
    # Verify force=True was passed to the service
    mock_svc.sync_repository.assert_called_once_with(repo_id, force=True)
