"""HTTP-level tests for admin settings API endpoints."""

import uuid
from collections.abc import Iterator
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI, status
from fastapi.testclient import TestClient
from tracecat_ee.admin.router import router as admin_router
from tracecat_ee.admin.settings import router as settings_router
from tracecat_ee.admin.settings.schemas import (
    PlatformRegistrySettingsRead,
)

from tracecat.auth.credentials import require_superuser, require_superuser_step_up
from tracecat.auth.types import PlatformRole, Role
from tracecat.auth.users import current_active_user
from tracecat.db.engine import get_async_session_bypass_rls
from tracecat.db.models import User
from tracecat.exceptions import TracecatNotFoundError, TracecatValidationError
from tracecat.managed_resources.source import ManagedSourceNotConfiguredError

MANAGED_SOURCE_PATH = "/admin/settings/managed-resources/source"


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


async def _mock_session() -> AsyncMock:
    return AsyncMock(name="mock_async_session")


def _admin_app() -> FastAPI:
    app = FastAPI()
    app.include_router(admin_router)
    app.dependency_overrides[get_async_session_bypass_rls] = _mock_session
    return app


@pytest.fixture
def non_superuser_client() -> Iterator[TestClient]:
    app = _admin_app()
    app.dependency_overrides[current_active_user] = lambda: User(
        id=uuid.uuid4(), is_superuser=False
    )
    yield TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def superuser_client() -> Iterator[TestClient]:
    app = _admin_app()
    role = PlatformRole(type="user", user_id=uuid.uuid4(), service_id="tracecat-api")
    app.dependency_overrides[require_superuser] = lambda: role
    app.dependency_overrides[require_superuser_step_up] = lambda: role
    yield TestClient(app, raise_server_exceptions=False)


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", MANAGED_SOURCE_PATH),
        ("PUT", MANAGED_SOURCE_PATH),
        ("GET", f"{MANAGED_SOURCE_PATH}/tags"),
    ],
)
def test_managed_source_routes_reject_non_superusers(
    non_superuser_client: TestClient, method: str, path: str
) -> None:
    with patch.object(settings_router, "AdminSettingsService") as MockService:
        response = non_superuser_client.request(
            method, path, json={"workspace_id": str(uuid.uuid4())}
        )

    assert response.status_code == status.HTTP_403_FORBIDDEN
    MockService.assert_not_called()


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (TracecatNotFoundError("Workspace not found"), status.HTTP_404_NOT_FOUND),
        (TracecatValidationError("no Git binding"), status.HTTP_400_BAD_REQUEST),
    ],
)
def test_update_managed_source_maps_validation_errors(
    superuser_client: TestClient, error: Exception, expected: int
) -> None:
    with patch.object(settings_router, "AdminSettingsService") as MockService:
        MockService.return_value.update_managed_resources_source = AsyncMock(
            side_effect=error
        )
        response = superuser_client.put(
            MANAGED_SOURCE_PATH, json={"workspace_id": str(uuid.uuid4())}
        )

    assert response.status_code == expected
    assert response.json()["detail"] == str(error)


def test_list_tags_without_source_is_a_conflict(
    superuser_client: TestClient,
) -> None:
    list_tags = AsyncMock(
        side_effect=ManagedSourceNotConfiguredError(
            "Managed resources source workspace is not configured"
        )
    )
    with patch.object(settings_router, "AdminSettingsService") as MockService:
        MockService.return_value.list_managed_resources_source_tags = list_tags
        response = superuser_client.get(f"{MANAGED_SOURCE_PATH}/tags?limit=5")

    assert response.status_code == status.HTTP_409_CONFLICT
    assert response.json()["detail"] == (
        "Managed resources source workspace is not configured"
    )
    list_tags.assert_awaited_once_with(limit=5)
