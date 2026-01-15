"""HTTP-level tests for admin registry API endpoints."""

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import status
from fastapi.testclient import TestClient
from tracecat_ee.admin.registry import router as registry_router
from tracecat_ee.admin.registry.schemas import (
    RegistryStatusResponse,
    RegistrySyncResponse,
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

    with patch.object(registry_router, "AdminRegistryService") as MockService:
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

    with patch.object(registry_router, "AdminRegistryService") as MockService:
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

    with patch.object(registry_router, "AdminRegistryService") as MockService:
        mock_svc = AsyncMock()
        mock_svc.list_versions.return_value = payload
        MockService.return_value = mock_svc

        response = client.get("/admin/registry/versions")

    assert response.status_code == status.HTTP_200_OK
    assert response.json()[0]["version"] == "1.0.0"
