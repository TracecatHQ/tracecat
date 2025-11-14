"""HTTP-level tests for agent management API endpoints."""

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import status
from fastapi.testclient import TestClient

from tracecat.agent.schemas import (
    ModelConfig,
    ProviderCredentialConfig,
    ProviderCredentialField,
)
from tracecat.auth.types import Role


@pytest.mark.anyio
async def test_list_models_success(
    client: TestClient,
    test_admin_role: Role,
) -> None:
    """Test GET /agent/models returns available models."""
    with patch("tracecat.agent.router.AgentManagementService") as MockService:
        mock_svc = AsyncMock()
        mock_models = {
            "gpt-4": ModelConfig(
                provider="openai",
                name="gpt-4",
                org_secret_name="agent-openai-credentials",
                secrets={"required": ["openai"]},
            ),
            "claude-3": ModelConfig(
                provider="anthropic",
                name="claude-3",
                org_secret_name="agent-anthropic-credentials",
                secrets={"required": ["anthropic"]},
            ),
        }
        mock_svc.list_models.return_value = mock_models
        MockService.return_value = mock_svc

        # Make request
        response = client.get("/agent/models")

        # Assertions
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert "gpt-4" in data
        assert "claude-3" in data
        assert data["gpt-4"]["provider"] == "openai"
        assert data["claude-3"]["provider"] == "anthropic"


@pytest.mark.anyio
async def test_list_providers_success(
    client: TestClient,
    test_admin_role: Role,
) -> None:
    """Test GET /agent/providers returns available providers."""
    with patch("tracecat.agent.router.AgentManagementService") as MockService:
        mock_svc = AsyncMock()
        mock_providers = ["openai", "anthropic", "google"]
        mock_svc.list_providers.return_value = mock_providers
        MockService.return_value = mock_svc

        # Make request
        response = client.get("/agent/providers")

        # Assertions
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data == mock_providers
        assert "openai" in data
        assert "anthropic" in data


@pytest.mark.anyio
async def test_get_providers_status_success(
    client: TestClient,
    test_admin_role: Role,
) -> None:
    """Test GET /agent/providers/status returns credential status."""
    with patch("tracecat.agent.router.AgentManagementService") as MockService:
        mock_svc = AsyncMock()
        mock_status = {
            "openai": True,
            "anthropic": False,
            "google": True,
        }
        mock_svc.get_providers_status.return_value = mock_status
        MockService.return_value = mock_svc

        # Make request
        response = client.get("/agent/providers/status")

        # Assertions
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data == mock_status
        assert data["openai"] is True
        assert data["anthropic"] is False


@pytest.mark.anyio
async def test_list_provider_credential_configs_success(
    client: TestClient,
    test_admin_role: Role,
) -> None:
    """Test GET /agent/providers/configs returns credential configs."""
    with patch("tracecat.agent.router.AgentManagementService") as MockService:
        mock_svc = AsyncMock()
        mock_configs = [
            ProviderCredentialConfig(
                provider="openai",
                label="OpenAI",
                fields=[
                    ProviderCredentialField(
                        key="api_key",
                        label="API Key",
                        type="password",
                        description="OpenAI API key",
                    )
                ],
            ),
        ]
        mock_svc.list_provider_credential_configs.return_value = mock_configs
        MockService.return_value = mock_svc

        # Make request
        response = client.get("/agent/providers/configs")

        # Assertions
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert len(data) == 1
        assert data[0]["provider"] == "openai"


@pytest.mark.anyio
async def test_get_provider_credential_config_success(
    client: TestClient,
    test_admin_role: Role,
) -> None:
    """Test GET /agent/providers/{provider}/config returns provider config."""
    with patch("tracecat.agent.router.AgentManagementService") as MockService:
        mock_svc = AsyncMock()
        mock_config = ProviderCredentialConfig(
            provider="openai",
            label="OpenAI",
            fields=[
                ProviderCredentialField(
                    key="api_key",
                    label="API Key",
                    type="password",
                    description="OpenAI API key",
                )
            ],
        )
        mock_svc.get_provider_credential_config.return_value = mock_config
        MockService.return_value = mock_svc

        # Make request
        response = client.get("/agent/providers/openai/config")

        # Assertions
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["provider"] == "openai"
        assert "fields" in data


@pytest.mark.anyio
async def test_get_provider_credential_config_not_found(
    client: TestClient,
    test_admin_role: Role,
) -> None:
    """Test GET /agent/providers/{provider}/config with invalid provider returns 404."""
    with patch("tracecat.agent.router.AgentManagementService") as MockService:
        from tracecat.exceptions import TracecatNotFoundError

        mock_svc = AsyncMock()
        mock_svc.get_provider_credential_config.side_effect = TracecatNotFoundError(
            "Provider not found"
        )
        MockService.return_value = mock_svc

        # Make request
        response = client.get("/agent/providers/invalid/config")

        # Should return 404
        assert response.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.anyio
async def test_create_provider_credentials_success(
    client: TestClient,
    test_admin_role: Role,
) -> None:
    """Test POST /agent/credentials creates provider credentials."""
    with patch("tracecat.agent.router.AgentManagementService") as MockService:
        mock_svc = AsyncMock()
        mock_svc.create_provider_credentials.return_value = None
        MockService.return_value = mock_svc

        # Make request
        response = client.post(
            "/agent/credentials",
            json={
                "provider": "openai",
                "credentials": {"api_key": "sk-test-key"},
            },
        )

        # Assertions
        assert response.status_code == status.HTTP_201_CREATED
        data = response.json()
        assert "message" in data
        assert "openai" in data["message"]


@pytest.mark.anyio
async def test_create_provider_credentials_error(
    client: TestClient,
    test_admin_role: Role,
) -> None:
    """Test POST /agent/credentials with error returns 400."""
    with patch("tracecat.agent.router.AgentManagementService") as MockService:
        mock_svc = AsyncMock()
        mock_svc.create_provider_credentials.side_effect = Exception(
            "Invalid credentials"
        )
        MockService.return_value = mock_svc

        # Make request
        response = client.post(
            "/agent/credentials",
            json={
                "provider": "openai",
                "credentials": {"api_key": "invalid"},
            },
        )

        # Should return 400
        assert response.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.anyio
async def test_update_provider_credentials_success(
    client: TestClient,
    test_admin_role: Role,
) -> None:
    """Test PUT /agent/credentials/{provider} updates credentials."""
    with patch("tracecat.agent.router.AgentManagementService") as MockService:
        mock_svc = AsyncMock()
        mock_svc.update_provider_credentials.return_value = None
        MockService.return_value = mock_svc

        # Make request
        response = client.put(
            "/agent/credentials/openai",
            json={"credentials": {"api_key": "sk-new-key"}},
        )

        # Assertions
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert "message" in data
        assert "openai" in data["message"]


@pytest.mark.anyio
async def test_update_provider_credentials_not_found(
    client: TestClient,
    test_admin_role: Role,
) -> None:
    """Test PUT /agent/credentials/{provider} with non-existent provider returns 404."""
    with patch("tracecat.agent.router.AgentManagementService") as MockService:
        from tracecat.exceptions import TracecatNotFoundError

        mock_svc = AsyncMock()
        mock_svc.update_provider_credentials.side_effect = TracecatNotFoundError(
            "Credentials not found"
        )
        MockService.return_value = mock_svc

        # Make request
        response = client.put(
            "/agent/credentials/invalid",
            json={"credentials": {"api_key": "test"}},
        )

        # Should return 404
        assert response.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.anyio
async def test_delete_provider_credentials_success(
    client: TestClient,
    test_admin_role: Role,
) -> None:
    """Test DELETE /agent/credentials/{provider} deletes credentials."""
    with patch("tracecat.agent.router.AgentManagementService") as MockService:
        mock_svc = AsyncMock()
        mock_svc.delete_provider_credentials.return_value = None
        MockService.return_value = mock_svc

        # Make request
        response = client.delete("/agent/credentials/openai")

        # Assertions
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert "message" in data
        assert "openai" in data["message"]


@pytest.mark.anyio
async def test_get_default_model_success(
    client: TestClient,
    test_admin_role: Role,
) -> None:
    """Test GET /agent/default-model returns default model."""
    with patch("tracecat.agent.router.AgentManagementService") as MockService:
        mock_svc = AsyncMock()
        mock_svc.get_default_model.return_value = "gpt-4"
        MockService.return_value = mock_svc

        # Make request
        response = client.get("/agent/default-model")

        # Assertions
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data == "gpt-4"


@pytest.mark.anyio
async def test_get_default_model_none(
    client: TestClient,
    test_admin_role: Role,
) -> None:
    """Test GET /agent/default-model when no default is set."""
    with patch("tracecat.agent.router.AgentManagementService") as MockService:
        mock_svc = AsyncMock()
        mock_svc.get_default_model.return_value = None
        MockService.return_value = mock_svc

        # Make request
        response = client.get("/agent/default-model")

        # Assertions
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data is None


@pytest.mark.anyio
async def test_set_default_model_success(
    client: TestClient,
    test_admin_role: Role,
) -> None:
    """Test PUT /agent/default-model sets default model."""
    with patch("tracecat.agent.router.AgentManagementService") as MockService:
        mock_svc = AsyncMock()
        mock_svc.set_default_model.return_value = None
        MockService.return_value = mock_svc

        # Make request
        response = client.put("/agent/default-model?model_name=gpt-4")

        # Assertions
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert "message" in data
        assert "gpt-4" in data["message"]


@pytest.mark.anyio
async def test_set_default_model_not_found(
    client: TestClient,
    test_admin_role: Role,
) -> None:
    """Test PUT /agent/default-model with invalid model returns 404."""
    with patch("tracecat.agent.router.AgentManagementService") as MockService:
        from tracecat.exceptions import TracecatNotFoundError

        mock_svc = AsyncMock()
        mock_svc.set_default_model.side_effect = TracecatNotFoundError(
            "Model not found"
        )
        MockService.return_value = mock_svc

        # Make request
        response = client.put("/agent/default-model?model_name=invalid-model")

        # Should return 404
        assert response.status_code == status.HTTP_404_NOT_FOUND
