"""HTTP-level tests for admin registry API endpoints."""

import uuid
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import status
from fastapi.testclient import TestClient

import tracecat.admin.registry.router as registry_router_module
from tracecat.auth.types import Role
from tracecat.exceptions import TracecatNotFoundError, TracecatValidationError

_REPO_ID = uuid.uuid4()
_VERSION_ID = uuid.uuid4()
_BACKFILL_PATH = "/admin/registry/versions/artifacts/backfill"
_PROMOTE_PATH = f"/admin/registry/{_REPO_ID}/versions/{_VERSION_ID}/promote"


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("path", "payload", "service_method", "error", "status_code", "detail"),
    [
        pytest.param(
            _BACKFILL_PATH,
            {"version_ids": [str(_VERSION_ID)]},
            "start_artifacts_backfill",
            TracecatNotFoundError(f"Registry versions not found: {_VERSION_ID}"),
            status.HTTP_404_NOT_FOUND,
            f"Registry versions not found: {_VERSION_ID}",
            id="backfill-missing-version",
        ),
        pytest.param(
            _BACKFILL_PATH,
            {"version_ids": [str(_VERSION_ID)]},
            "start_artifacts_backfill",
            TracecatValidationError(
                f"Registry versions do not have tarballs: {_VERSION_ID}"
            ),
            status.HTTP_400_BAD_REQUEST,
            f"Registry versions do not have tarballs: {_VERSION_ID}",
            id="backfill-version-without-tarball",
        ),
        pytest.param(
            _PROMOTE_PATH,
            None,
            "promote_version",
            ValueError("Repository not found"),
            status.HTTP_404_NOT_FOUND,
            None,
            id="promote-not-found",
        ),
        pytest.param(
            _PROMOTE_PATH,
            None,
            "promote_version",
            TracecatValidationError("Version artifact is not ready"),
            status.HTTP_400_BAD_REQUEST,
            "Version artifact is not ready",
            id="promote-artifact-not-ready",
        ),
    ],
)
async def test_admin_registry_errors_map_to_http_status(
    client: TestClient,
    test_admin_role: Role,
    path: str,
    payload: dict[str, list[str]] | None,
    service_method: str,
    error: Exception,
    status_code: int,
    detail: str | None,
) -> None:
    with patch.object(registry_router_module, "AdminRegistryService") as MockService:
        mock_svc = AsyncMock()
        getattr(mock_svc, service_method).side_effect = error
        MockService.return_value = mock_svc

        response = client.post(path, json=payload)

    assert response.status_code == status_code
    if detail is not None:
        assert response.json()["detail"] == detail
