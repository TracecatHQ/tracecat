"""HTTP-level tests for secrets API endpoints."""

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from cryptography.fernet import InvalidToken
from fastapi import status
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError

from tracecat.auth.types import Role
from tracecat.db.models import OrganizationSecret, Secret, Workspace
from tracecat.exceptions import TracecatNotFoundError
from tracecat.secrets import router as secrets_router
from tracecat.secrets.enums import SecretType


@pytest.fixture
def mock_secret(test_workspace: Workspace) -> Secret:
    """Create a mock secret DB object."""
    secret = Secret(
        id=uuid.UUID("aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa"),
        workspace_id=test_workspace.id,
        name="test_secret",
        type=SecretType.CUSTOM,
        description="Test secret description",
        encrypted_keys=b"encrypted_data",
        environment="default",
        tags=None,
        created_at=datetime(2024, 1, 1, tzinfo=UTC),
        updated_at=datetime(2024, 1, 1, tzinfo=UTC),
    )
    return secret


@pytest.mark.anyio
async def test_list_secrets_with_corrupted_values(
    client: TestClient,
    test_admin_role: Role,
    mock_secret: Secret,
) -> None:
    """Test GET /secrets tolerates decryption failures for corrupted rows."""
    with patch.object(secrets_router, "SecretsService") as MockService:
        mock_svc = AsyncMock()
        mock_svc.list_secrets.return_value = [mock_secret]
        mock_svc.decrypt_keys = MagicMock(side_effect=InvalidToken())
        MockService.return_value = mock_svc

        response = client.get(
            "/secrets",
            params={"workspace_id": str(test_admin_role.workspace_id)},
        )

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert len(data) == 1
        assert data[0]["name"] == "test_secret"
        assert data[0]["keys"] == []
        assert data[0]["is_corrupted"] is True


@pytest.mark.anyio
async def test_list_secret_definitions_returns_registry_definitions_without_builtins(
    client: TestClient,
    test_admin_role: Role,
) -> None:
    """Test GET /secrets/definitions reflects registry definitions only."""
    with patch.object(secrets_router, "RegistryActionsService") as MockService:
        mock_svc = AsyncMock()
        mock_svc.get_aggregated_secrets.return_value = []
        MockService.return_value = mock_svc

        response = client.get(
            "/secrets/definitions",
            params={"workspace_id": str(test_admin_role.workspace_id)},
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.json() == []


@pytest.mark.anyio
async def test_get_aws_assume_role_access_hides_internal_config_errors(
    client: TestClient,
    test_admin_role: Role,
) -> None:
    with patch.object(
        secrets_router,
        "get_tracecat_aws_account_id",
        side_effect=ValueError(
            "TRACECAT__AWS_ASSUME_ROLE_ACCOUNT_ID is not configured"
        ),
    ):
        response = client.get(
            "/secrets/aws-assume-role",
            params={"workspace_id": str(test_admin_role.workspace_id)},
        )

    assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
    assert response.json() == {
        "detail": "AWS AssumeRole access is not available right now."
    }


_MISSING_SECRET_ID = "00000000-0000-4000-8000-000000000000"


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("method", "path", "payload", "service_method", "error", "status_code"),
    [
        pytest.param(
            "GET",
            "/secrets/nonexistent",
            None,
            "get_secret_by_name",
            TracecatNotFoundError("Secret not found"),
            status.HTTP_404_NOT_FOUND,
            id="get-by-name-not-found",
        ),
        pytest.param(
            "POST",
            "/secrets",
            {
                "name": "duplicate_secret",
                "type": "custom",
                "keys": [{"key": "api_key", "value": "secret_value"}],
                "environment": "default",
            },
            "create_secret",
            IntegrityError("", {}, Exception("Duplicate")),
            status.HTTP_409_CONFLICT,
            id="create-conflict",
        ),
        pytest.param(
            "POST",
            f"/secrets/{_MISSING_SECRET_ID}",
            {"description": "Updated"},
            "get_secret",
            TracecatNotFoundError("Secret not found"),
            status.HTTP_404_NOT_FOUND,
            id="update-not-found",
        ),
        pytest.param(
            "DELETE",
            f"/secrets/{_MISSING_SECRET_ID}",
            None,
            "get_secret",
            TracecatNotFoundError("Secret not found"),
            status.HTTP_404_NOT_FOUND,
            id="delete-not-found",
        ),
    ],
)
async def test_secret_errors_map_to_http_status(
    client: TestClient,
    test_admin_role: Role,
    method: str,
    path: str,
    payload: dict[str, object] | None,
    service_method: str,
    error: Exception,
    status_code: int,
) -> None:
    with patch.object(secrets_router, "SecretsService") as MockService:
        mock_svc = AsyncMock()
        getattr(mock_svc, service_method).side_effect = error
        MockService.return_value = mock_svc

        response = client.request(
            method,
            path,
            params={"workspace_id": str(test_admin_role.workspace_id)},
            json=payload,
        )

    assert response.status_code == status_code


# Organization secrets tests


@pytest.fixture
def mock_org_secret(mock_org_id: uuid.UUID) -> OrganizationSecret:
    """Create a mock organization secret DB object."""
    secret = OrganizationSecret(
        id=uuid.UUID("bbbbbbbb-bbbb-4bbb-bbbb-bbbbbbbbbbbb"),
        organization_id=mock_org_id,
        name="org_secret",
        type=SecretType.CUSTOM,
        description="Organization secret description",
        encrypted_keys=b"encrypted_org_data",
        environment="default",
        tags=None,
        created_at=datetime(2024, 1, 1, tzinfo=UTC),
        updated_at=datetime(2024, 1, 1, tzinfo=UTC),
    )
    return secret


@pytest.mark.anyio
async def test_list_org_secrets_with_corrupted_values(
    client: TestClient,
    test_admin_role: Role,
    mock_org_secret: OrganizationSecret,
) -> None:
    """Test GET /organization/secrets tolerates decryption failures."""
    with patch.object(secrets_router, "SecretsService") as MockService:
        mock_svc = AsyncMock()
        mock_svc.list_org_secrets.return_value = [mock_org_secret]
        mock_svc.decrypt_keys = MagicMock(side_effect=InvalidToken())
        MockService.return_value = mock_svc

        response = client.get("/organization/secrets")

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert len(data) == 1
        assert data[0]["name"] == "org_secret"
        assert data[0]["keys"] == []
        assert data[0]["is_corrupted"] is True
