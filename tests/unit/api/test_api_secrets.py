"""HTTP-level tests for secrets API endpoints."""

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from cryptography.fernet import InvalidToken
from fastapi import status
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy.exc import IntegrityError

from tracecat.auth.types import Role
from tracecat.db.models import OrganizationSecret, Secret, Workspace
from tracecat.exceptions import TracecatNotFoundError
from tracecat.secrets import router as secrets_router
from tracecat.secrets.enums import SecretType
from tracecat.secrets.schemas import SecretDefinition, SecretKeyValue


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
async def test_list_secrets_success(
    client: TestClient,
    test_admin_role: Role,
    mock_secret: Secret,
) -> None:
    """Test GET /secrets returns list of secrets."""
    with patch.object(secrets_router, "SecretsService") as MockService:
        mock_svc = AsyncMock()
        mock_svc.list_secrets.return_value = [mock_secret]
        mock_svc.decrypt_keys = MagicMock(
            return_value=[SecretKeyValue(key="api_key", value=SecretStr("***"))]
        )
        MockService.return_value = mock_svc

        # Make request
        response = client.get(
            "/secrets",
            params={"workspace_id": str(test_admin_role.workspace_id)},
        )

        # Assertions
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert len(data) == 1
        assert data[0]["name"] == "test_secret"
        assert data[0]["type"] == "custom"


@pytest.mark.anyio
async def test_list_secrets_with_type_filter(
    client: TestClient,
    test_admin_role: Role,
    mock_secret: Secret,
) -> None:
    """Test GET /secrets with type filter."""
    with patch.object(secrets_router, "SecretsService") as MockService:
        mock_svc = AsyncMock()
        mock_svc.list_secrets.return_value = [mock_secret]
        mock_svc.decrypt_keys = MagicMock(
            return_value=[SecretKeyValue(key="api_key", value=SecretStr("***"))]
        )
        MockService.return_value = mock_svc

        # Make request
        response = client.get(
            "/secrets",
            params={
                "workspace_id": str(test_admin_role.workspace_id),
                "type": "custom",
            },
        )

        # Assertions
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert len(data) == 1


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
async def test_list_secret_definitions_success(
    client: TestClient,
    test_admin_role: Role,
) -> None:
    """Test GET /secrets/definitions returns registry secret definitions."""
    with patch.object(secrets_router, "RegistryActionsService") as MockService:
        mock_svc = AsyncMock()
        mock_svc.get_aggregated_secrets.return_value = [
            SecretDefinition(
                name="alpha",
                keys=["KEY1"],
                optional_keys=None,
                optional=False,
                actions=["tools.alpha.action_one"],
                action_count=1,
            )
        ]
        MockService.return_value = mock_svc

        response = client.get(
            "/secrets/definitions",
            params={"workspace_id": str(test_admin_role.workspace_id)},
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.json() == [
            {
                "name": "alpha",
                "keys": ["KEY1"],
                "optional_keys": None,
                "optional": False,
                "actions": ["tools.alpha.action_one"],
                "action_count": 1,
            }
        ]


@pytest.mark.anyio
async def test_search_secrets_success(
    client: TestClient,
    test_admin_role: Role,
    mock_secret: Secret,
) -> None:
    """Test GET /secrets/search with filters."""
    with patch.object(secrets_router, "SecretsService") as MockService:
        mock_svc = AsyncMock()
        mock_svc.search_secrets.return_value = [mock_secret]
        mock_svc.decrypt_keys = MagicMock(
            return_value=[SecretKeyValue(key="api_key", value=SecretStr("***"))]
        )
        MockService.return_value = mock_svc

        # Make request
        response = client.get(
            "/secrets/search",
            params={
                "workspace_id": str(test_admin_role.workspace_id),
                "environment": "default",
                "name": "test_secret",
            },
        )

        # Assertions
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert len(data) == 1
        assert data[0]["name"] == "test_secret"


@pytest.mark.anyio
async def test_get_secret_by_name_success(
    client: TestClient,
    test_admin_role: Role,
    mock_secret: Secret,
) -> None:
    """Test GET /secrets/{secret_name} returns secret details."""
    with patch.object(secrets_router, "SecretsService") as MockService:
        mock_svc = AsyncMock()
        mock_svc.get_secret_by_name.return_value = mock_secret
        MockService.return_value = mock_svc

        # Make request
        response = client.get(
            "/secrets/test_secret",
            params={"workspace_id": str(test_admin_role.workspace_id)},
        )

        # Assertions
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["name"] == "test_secret"
        assert data["type"] == "custom"


@pytest.mark.anyio
async def test_get_secret_by_name_not_found(
    client: TestClient,
    test_admin_role: Role,
) -> None:
    """Test GET /secrets/{secret_name} with non-existent name returns 404."""
    with patch.object(secrets_router, "SecretsService") as MockService:
        mock_svc = AsyncMock()
        mock_svc.get_secret_by_name.side_effect = TracecatNotFoundError(
            "Secret not found"
        )
        MockService.return_value = mock_svc

        # Make request
        response = client.get(
            "/secrets/nonexistent",
            params={"workspace_id": str(test_admin_role.workspace_id)},
        )

        # Should return 404
        assert response.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.anyio
async def test_create_secret_success(
    client: TestClient,
    test_admin_role: Role,
) -> None:
    """Test POST /secrets creates a new secret."""
    with patch.object(secrets_router, "SecretsService") as MockService:
        mock_svc = AsyncMock()
        mock_svc.create_secret.return_value = None
        MockService.return_value = mock_svc

        # Make request
        response = client.post(
            "/secrets",
            params={"workspace_id": str(test_admin_role.workspace_id)},
            json={
                "name": "new_secret",
                "type": "custom",
                "description": "New test secret",
                "keys": [{"key": "api_key", "value": "secret_value"}],
                "environment": "default",
            },
        )

        # Assertions
        assert response.status_code == status.HTTP_201_CREATED


@pytest.mark.anyio
async def test_create_secret_conflict(
    client: TestClient,
    test_admin_role: Role,
) -> None:
    """Test POST /secrets with duplicate name returns 409."""
    with patch.object(secrets_router, "SecretsService") as MockService:
        mock_svc = AsyncMock()
        mock_svc.create_secret.side_effect = IntegrityError(
            "", {}, Exception("Duplicate")
        )
        MockService.return_value = mock_svc

        # Make request
        response = client.post(
            "/secrets",
            params={"workspace_id": str(test_admin_role.workspace_id)},
            json={
                "name": "duplicate_secret",
                "type": "custom",
                "keys": [{"key": "api_key", "value": "secret_value"}],
                "environment": "default",
            },
        )

        # Should return 409
        assert response.status_code == status.HTTP_409_CONFLICT


@pytest.mark.anyio
async def test_update_secret_by_id_success(
    client: TestClient,
    test_admin_role: Role,
    mock_secret: Secret,
) -> None:
    """Test POST /secrets/{secret_id} updates secret."""
    with patch.object(secrets_router, "SecretsService") as MockService:
        mock_svc = AsyncMock()
        mock_svc.get_secret.return_value = mock_secret
        mock_svc.update_secret.return_value = None
        MockService.return_value = mock_svc

        # Make request
        secret_id = str(mock_secret.id)
        response = client.post(
            f"/secrets/{secret_id}",
            params={"workspace_id": str(test_admin_role.workspace_id)},
            json={
                "description": "Updated description",
                "keys": [{"key": "api_key", "value": "new_value"}],
            },
        )

        # Assertions
        assert response.status_code == status.HTTP_204_NO_CONTENT


@pytest.mark.anyio
async def test_update_secret_by_id_not_found(
    client: TestClient,
    test_admin_role: Role,
) -> None:
    """Test POST /secrets/{secret_id} with non-existent ID returns 404."""
    with patch.object(secrets_router, "SecretsService") as MockService:
        mock_svc = AsyncMock()
        mock_svc.get_secret.side_effect = TracecatNotFoundError("Secret not found")
        MockService.return_value = mock_svc

        # Make request
        fake_id = "00000000-0000-4000-8000-000000000000"
        response = client.post(
            f"/secrets/{fake_id}",
            params={"workspace_id": str(test_admin_role.workspace_id)},
            json={"description": "Updated"},
        )

        # Should return 404
        assert response.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.anyio
async def test_delete_secret_by_id_success(
    client: TestClient,
    test_admin_role: Role,
    mock_secret: Secret,
) -> None:
    """Test DELETE /secrets/{secret_id} deletes secret."""
    with patch.object(secrets_router, "SecretsService") as MockService:
        mock_svc = AsyncMock()
        mock_svc.get_secret.return_value = mock_secret
        mock_svc.delete_secret.return_value = None
        MockService.return_value = mock_svc

        # Make request
        secret_id = str(mock_secret.id)
        response = client.delete(
            f"/secrets/{secret_id}",
            params={"workspace_id": str(test_admin_role.workspace_id)},
        )

        # Assertions
        assert response.status_code == status.HTTP_204_NO_CONTENT


@pytest.mark.anyio
async def test_delete_secret_by_id_not_found(
    client: TestClient,
    test_admin_role: Role,
) -> None:
    """Test DELETE /secrets/{secret_id} with non-existent ID returns 404."""
    with patch.object(secrets_router, "SecretsService") as MockService:
        mock_svc = AsyncMock()
        mock_svc.get_secret.side_effect = TracecatNotFoundError("Secret not found")
        MockService.return_value = mock_svc

        # Make request
        fake_id = "00000000-0000-4000-8000-000000000000"
        response = client.delete(
            f"/secrets/{fake_id}",
            params={"workspace_id": str(test_admin_role.workspace_id)},
        )

        # Should return 404
        assert response.status_code == status.HTTP_404_NOT_FOUND


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
async def test_list_org_secrets_success(
    client: TestClient,
    test_admin_role: Role,
    mock_org_secret: OrganizationSecret,
) -> None:
    """Test GET /organization/secrets returns org secrets."""
    with patch.object(secrets_router, "SecretsService") as MockService:
        mock_svc = AsyncMock()
        mock_svc.list_org_secrets.return_value = [mock_org_secret]
        mock_svc.decrypt_keys = MagicMock(
            return_value=[SecretKeyValue(key="api_key", value=SecretStr("***"))]
        )
        MockService.return_value = mock_svc

        # Make request
        response = client.get("/organization/secrets")

        # Assertions
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert len(data) == 1
        assert data[0]["name"] == "org_secret"


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


@pytest.mark.anyio
async def test_create_org_secret_success(
    client: TestClient,
    test_admin_role: Role,
) -> None:
    """Test POST /organization/secrets creates org secret."""
    with patch.object(secrets_router, "SecretsService") as MockService:
        mock_svc = AsyncMock()
        mock_svc.create_org_secret.return_value = None
        MockService.return_value = mock_svc

        # Make request
        response = client.post(
            "/organization/secrets",
            json={
                "name": "new_org_secret",
                "type": "custom",
                "description": "New org secret",
                "keys": [{"key": "api_key", "value": "secret_value"}],
                "environment": "default",
            },
        )

        # Assertions
        assert response.status_code == status.HTTP_201_CREATED


@pytest.mark.anyio
async def test_get_org_secret_by_name_success(
    client: TestClient,
    test_admin_role: Role,
    mock_org_secret: OrganizationSecret,
) -> None:
    """Test GET /organization/secrets/{secret_name} returns org secret."""
    with patch.object(secrets_router, "SecretsService") as MockService:
        mock_svc = AsyncMock()
        mock_svc.get_org_secret_by_name.return_value = mock_org_secret
        MockService.return_value = mock_svc

        # Make request
        response = client.get("/organization/secrets/org_secret")

        # Assertions
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["name"] == "org_secret"


@pytest.mark.anyio
async def test_update_org_secret_by_id_success(
    client: TestClient,
    test_admin_role: Role,
    mock_org_secret: OrganizationSecret,
) -> None:
    """Test POST /organization/secrets/{secret_id} updates org secret."""
    with patch.object(secrets_router, "SecretsService") as MockService:
        mock_svc = AsyncMock()
        mock_svc.get_org_secret.return_value = mock_org_secret
        mock_svc.update_org_secret.return_value = None
        MockService.return_value = mock_svc

        # Make request
        secret_id = str(mock_org_secret.id)
        response = client.post(
            f"/organization/secrets/{secret_id}",
            json={"description": "Updated org secret"},
        )

        # Assertions
        assert response.status_code == status.HTTP_204_NO_CONTENT


@pytest.mark.anyio
async def test_delete_org_secret_by_id_success(
    client: TestClient,
    test_admin_role: Role,
    mock_org_secret: OrganizationSecret,
) -> None:
    """Test DELETE /organization/secrets/{secret_id} deletes org secret."""
    with patch.object(secrets_router, "SecretsService") as MockService:
        mock_svc = AsyncMock()
        mock_svc.get_org_secret.return_value = mock_org_secret
        mock_svc.delete_org_secret.return_value = None
        MockService.return_value = mock_svc

        # Make request
        secret_id = str(mock_org_secret.id)
        response = client.delete(f"/organization/secrets/{secret_id}")

        # Assertions
        assert response.status_code == status.HTTP_204_NO_CONTENT
