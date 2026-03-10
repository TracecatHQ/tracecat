"""HTTP-level tests for agent management API endpoints."""

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import status
from fastapi.testclient import TestClient

from tracecat.agent import router as agent_router
from tracecat.agent.schemas import (
    BuiltInProviderRead,
    DefaultModelInventoryRead,
    DefaultModelSelection,
    ModelCatalogEntry,
    ProviderCredentialConfig,
    ProviderCredentialField,
)
from tracecat.agent.types import ModelDiscoveryStatus, ModelSourceType
from tracecat.auth.types import Role
from tracecat.exceptions import TracecatNotFoundError


def _agent_route_exists(path: str, method: str) -> bool:
    return any(
        getattr(route, "path", None) == path
        and method in getattr(route, "methods", set())
        for route in agent_router.router.routes
    )


class _RouteAgnosticService:
    def __init__(self, payload: Any) -> None:
        self.payload = payload

    def __getattr__(self, _name: str) -> AsyncMock:
        return AsyncMock(return_value=self.payload)


@pytest.mark.anyio
async def test_list_models_success(
    client: TestClient,
    test_admin_role: Role,
) -> None:
    """Test GET /agent/models returns enabled catalog entries."""
    with patch.object(agent_router, "AgentManagementService") as mock_service_cls:
        mock_svc = AsyncMock()
        mock_svc.list_models.return_value = [
            ModelCatalogEntry(
                catalog_ref="default_sidecar:default:a:gpt-5",
                model_name="gpt-5",
                model_provider="openai",
                runtime_provider="default_sidecar",
                display_name="GPT-5",
                source_type=ModelSourceType.DEFAULT_SIDECAR,
                source_name="Default models",
                enabled=True,
            )
        ]
        mock_service_cls.return_value = mock_svc

        response = client.get("/agent/models")

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert len(data) == 1
        assert data[0]["catalog_ref"] == "default_sidecar:default:a:gpt-5"
        assert data[0]["source_type"] == ModelSourceType.DEFAULT_SIDECAR.value


@pytest.mark.anyio
async def test_list_discovered_models_success(
    client: TestClient,
    test_admin_role: Role,
) -> None:
    """Test GET /agent/catalog/discovered returns discovered catalog entries."""
    with patch.object(agent_router, "AgentManagementService") as mock_service_cls:
        mock_svc = AsyncMock()
        mock_svc.list_discovered_models.return_value = [
            ModelCatalogEntry(
                catalog_ref="openai:openai:gpt-5",
                model_name="gpt-5",
                model_provider="openai",
                runtime_provider="openai",
                display_name="GPT-5",
                source_type=ModelSourceType.OPENAI,
                source_name="OpenAI",
                enabled=False,
            )
        ]
        mock_service_cls.return_value = mock_svc

        response = client.get("/agent/catalog/discovered")

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert len(data) == 1
        assert data[0]["catalog_ref"] == "openai:openai:gpt-5"
        assert data[0]["enabled"] is False
        assert data[0]["source_type"] == ModelSourceType.OPENAI.value


@pytest.mark.anyio
async def test_list_providers_success(
    client: TestClient,
    test_admin_role: Role,
) -> None:
    """Test GET /agent/providers returns built-in provider cards."""
    with patch.object(agent_router, "AgentManagementService") as mock_service_cls:
        mock_svc = AsyncMock()
        mock_svc.list_providers.return_value = [
            BuiltInProviderRead(
                provider="openai",
                label="OpenAI",
                source_type=ModelSourceType.OPENAI,
                credentials_configured=True,
                discovery_status=ModelDiscoveryStatus.READY,
            )
        ]
        mock_service_cls.return_value = mock_svc

        response = client.get("/agent/providers")

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert len(data) == 1
        assert data[0]["provider"] == "openai"
        assert data[0]["source_type"] == ModelSourceType.OPENAI.value
        assert data[0]["credentials_configured"] is True


@pytest.mark.anyio
async def test_get_default_model_inventory_success(
    client: TestClient,
    test_admin_role: Role,
) -> None:
    """Test GET /agent/default-models returns sidecar inventory state."""
    with patch.object(agent_router, "AgentManagementService") as mock_service_cls:
        mock_svc = AsyncMock()
        mock_svc.get_default_sidecar_inventory.return_value = DefaultModelInventoryRead(
            discovery_status=ModelDiscoveryStatus.READY,
            last_refreshed_at=datetime.now(UTC),
            discovered_models=[],
        )
        mock_service_cls.return_value = mock_svc

        response = client.get("/agent/default-models")

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["source_type"] == ModelSourceType.DEFAULT_SIDECAR.value
        assert data["discovery_status"] == ModelDiscoveryStatus.READY.value


@pytest.mark.anyio
async def test_refresh_default_model_inventory_success(
    client: TestClient,
    test_admin_role: Role,
) -> None:
    """Test POST /agent/default-models/refresh refreshes sidecar inventory."""
    with patch.object(agent_router, "AgentManagementService") as mock_service_cls:
        mock_svc = AsyncMock()
        mock_svc.refresh_default_sidecar_inventory.return_value = (
            DefaultModelInventoryRead(
                discovery_status=ModelDiscoveryStatus.READY,
                last_refreshed_at=datetime.now(UTC),
                discovered_models=[],
            )
        )
        mock_service_cls.return_value = mock_svc

        response = client.post("/agent/default-models/refresh")

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["discovery_status"] == ModelDiscoveryStatus.READY.value


@pytest.mark.skipif(
    not _agent_route_exists("/agent/catalog/builtins", "GET"),
    reason="Built-in catalog endpoint not wired yet",
)
@pytest.mark.anyio
async def test_list_builtin_catalog_success(
    client: TestClient,
    test_admin_role: Role,
) -> None:
    payload = {
        "source_type": "builtin_catalog",
        "source_name": "Built-in catalog",
        "discovery_status": ModelDiscoveryStatus.READY.value,
        "last_refreshed_at": datetime.now(UTC).isoformat(),
        "last_error": None,
        "models": [
            {
                "catalog_ref": "builtin_catalog:openai:abc123:gpt-5",
                "model_name": "gpt-5",
                "model_provider": "openai",
                "runtime_provider": "openai",
                "display_name": "GPT-5",
                "source_type": ModelSourceType.OPENAI.value,
                "source_name": "OpenAI",
                "enabled": False,
                "credential_provider": "openai",
                "credential_label": "OpenAI",
                "credential_fields": [
                    {
                        "key": "OPENAI_API_KEY",
                        "label": "API key",
                        "type": "password",
                        "description": "OpenAI API key",
                        "required": True,
                    }
                ],
                "credentials_configured": False,
                "discovered": False,
                "ready": False,
                "enableable": False,
                "runtime_target_configured": True,
                "readiness_message": "Configure OpenAI credentials to enable this model.",
            }
        ],
    }
    with patch.object(
        agent_router,
        "AgentManagementService",
        return_value=_RouteAgnosticService(payload),
    ):
        response = client.get("/agent/catalog/builtins")

    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["source_type"] == "builtin_catalog"
    assert data["models"][0]["catalog_ref"] == "builtin_catalog:openai:abc123:gpt-5"
    assert data["models"][0]["credential_provider"] == "openai"
    assert data["models"][0]["enableable"] is False


@pytest.mark.skipif(
    not _agent_route_exists("/agent/catalog/builtins/refresh", "POST"),
    reason="Built-in catalog refresh endpoint not wired yet",
)
@pytest.mark.anyio
async def test_refresh_builtin_catalog_success(
    client: TestClient,
    test_admin_role: Role,
) -> None:
    payload = {
        "source_type": "builtin_catalog",
        "source_name": "Built-in catalog",
        "discovery_status": ModelDiscoveryStatus.READY.value,
        "last_refreshed_at": datetime.now(UTC).isoformat(),
        "last_error": None,
        "models": [
            {
                "catalog_ref": "builtin_catalog:anthropic:def456:claude-sonnet-4-5",
                "model_name": "claude-sonnet-4-5",
                "model_provider": "anthropic",
                "runtime_provider": "anthropic",
                "display_name": "Claude Sonnet 4.5",
                "source_type": ModelSourceType.ANTHROPIC.value,
                "source_name": "Anthropic",
                "enabled": True,
                "credential_provider": "anthropic",
                "credential_label": "Anthropic",
                "credential_fields": [
                    {
                        "key": "ANTHROPIC_API_KEY",
                        "label": "API key",
                        "type": "password",
                        "description": "Anthropic API key",
                        "required": True,
                    }
                ],
                "credentials_configured": True,
                "discovered": True,
                "ready": True,
                "enableable": True,
                "runtime_target_configured": True,
                "readiness_message": None,
            }
        ],
    }
    with patch.object(
        agent_router,
        "AgentManagementService",
        return_value=_RouteAgnosticService(payload),
    ):
        response = client.post("/agent/catalog/builtins/refresh")

    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["discovery_status"] == ModelDiscoveryStatus.READY.value
    assert data["models"][0]["credential_provider"] == "anthropic"
    assert data["models"][0]["enabled"] is True


@pytest.mark.anyio
async def test_get_providers_status_success(
    client: TestClient,
    test_admin_role: Role,
) -> None:
    """Test GET /agent/providers/status returns credential status."""
    with patch.object(agent_router, "AgentManagementService") as mock_service_cls:
        mock_svc = AsyncMock()
        mock_status = {
            "openai": True,
            "anthropic": False,
        }
        mock_svc.get_providers_status.return_value = mock_status
        mock_service_cls.return_value = mock_svc

        response = client.get("/agent/providers/status")

        assert response.status_code == status.HTTP_200_OK
        assert response.json() == mock_status


@pytest.mark.anyio
async def test_workspace_provider_status_route_is_removed(
    client: TestClient,
    test_admin_role: Role,
) -> None:
    response = client.get("/agent/workspace/providers/status")

    assert response.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.anyio
async def test_list_provider_credential_configs_success(
    client: TestClient,
    test_admin_role: Role,
) -> None:
    """Test GET /agent/providers/configs returns provider field configs."""
    with patch.object(agent_router, "AgentManagementService") as mock_service_cls:
        mock_svc = AsyncMock()
        mock_svc.list_provider_credential_configs.return_value = [
            ProviderCredentialConfig(
                provider="openai",
                label="OpenAI",
                fields=[
                    ProviderCredentialField(
                        key="OPENAI_API_KEY",
                        label="API key",
                        type="password",
                        description="OpenAI API key",
                    )
                ],
            )
        ]
        mock_service_cls.return_value = mock_svc

        response = client.get("/agent/providers/configs")

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
    with patch.object(agent_router, "AgentManagementService") as mock_service_cls:
        mock_svc = AsyncMock()
        mock_svc.get_provider_credential_config.return_value = ProviderCredentialConfig(
            provider="openai",
            label="OpenAI",
            fields=[
                ProviderCredentialField(
                    key="OPENAI_API_KEY",
                    label="API key",
                    type="password",
                    description="OpenAI API key",
                )
            ],
        )
        mock_service_cls.return_value = mock_svc

        response = client.get("/agent/providers/openai/config")

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["provider"] == "openai"


@pytest.mark.anyio
async def test_get_provider_credential_config_not_found(
    client: TestClient,
    test_admin_role: Role,
) -> None:
    """Test GET /agent/providers/{provider}/config returns 404 when missing."""
    with patch.object(agent_router, "AgentManagementService") as mock_service_cls:
        mock_svc = AsyncMock()
        mock_svc.get_provider_credential_config.side_effect = TracecatNotFoundError(
            "Provider not found"
        )
        mock_service_cls.return_value = mock_svc

        response = client.get("/agent/providers/invalid/config")

        assert response.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.anyio
async def test_refresh_provider_inventory_success(
    client: TestClient,
    test_admin_role: Role,
) -> None:
    """Test POST /agent/providers/{provider}/refresh refreshes built-in provider inventory."""
    with patch.object(agent_router, "AgentManagementService") as mock_service_cls:
        mock_svc = AsyncMock()
        mock_svc.refresh_provider_inventory.return_value = BuiltInProviderRead(
            provider="openai",
            label="OpenAI",
            source_type=ModelSourceType.OPENAI,
            credentials_configured=True,
            discovery_status=ModelDiscoveryStatus.READY,
        )
        mock_service_cls.return_value = mock_svc

        response = client.post("/agent/providers/openai/refresh")

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["provider"] == "openai"


@pytest.mark.anyio
async def test_create_provider_credentials_success(
    client: TestClient,
    test_admin_role: Role,
) -> None:
    """Test POST /agent/credentials creates provider credentials."""
    with patch.object(agent_router, "AgentManagementService") as mock_service_cls:
        mock_svc = AsyncMock()
        mock_svc.create_provider_credentials.return_value = None
        mock_service_cls.return_value = mock_svc

        response = client.post(
            "/agent/credentials",
            json={
                "provider": "openai",
                "credentials": {"OPENAI_API_KEY": "sk-test-key"},
            },
        )

        assert response.status_code == status.HTTP_201_CREATED
        assert "openai" in response.json()["message"]


@pytest.mark.anyio
async def test_update_provider_credentials_success(
    client: TestClient,
    test_admin_role: Role,
) -> None:
    """Test PUT /agent/credentials/{provider} updates credentials."""
    with patch.object(agent_router, "AgentManagementService") as mock_service_cls:
        mock_svc = AsyncMock()
        mock_svc.update_provider_credentials.return_value = None
        mock_service_cls.return_value = mock_svc

        response = client.put(
            "/agent/credentials/openai",
            json={"credentials": {"OPENAI_API_KEY": "sk-new-key"}},
        )

        assert response.status_code == status.HTTP_200_OK
        assert "openai" in response.json()["message"]


@pytest.mark.anyio
async def test_delete_provider_credentials_success(
    client: TestClient,
    test_admin_role: Role,
) -> None:
    """Test DELETE /agent/credentials/{provider} deletes credentials."""
    with patch.object(agent_router, "AgentManagementService") as mock_service_cls:
        mock_svc = AsyncMock()
        mock_svc.delete_provider_credentials.return_value = None
        mock_service_cls.return_value = mock_svc

        response = client.delete("/agent/credentials/openai")

        assert response.status_code == status.HTTP_200_OK
        assert "openai" in response.json()["message"]


@pytest.mark.anyio
async def test_get_default_model_success(
    client: TestClient,
    test_admin_role: Role,
) -> None:
    """Test GET /agent/default-model returns the default selection."""
    with patch.object(agent_router, "AgentManagementService") as mock_service_cls:
        mock_svc = AsyncMock()
        mock_svc.get_default_model.return_value = DefaultModelSelection(
            catalog_ref="default_sidecar:default:a:gpt-5",
            model_name="gpt-5",
            model_provider="openai",
            display_name="GPT-5",
        )
        mock_service_cls.return_value = mock_svc

        response = client.get("/agent/default-model")

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["catalog_ref"] == "default_sidecar:default:a:gpt-5"


@pytest.mark.anyio
async def test_get_default_model_none(
    client: TestClient,
    test_admin_role: Role,
) -> None:
    """Test GET /agent/default-model when no default is set."""
    with patch.object(agent_router, "AgentManagementService") as mock_service_cls:
        mock_svc = AsyncMock()
        mock_svc.get_default_model.return_value = None
        mock_service_cls.return_value = mock_svc

        response = client.get("/agent/default-model")

        assert response.status_code == status.HTTP_200_OK
        assert response.json() is None


@pytest.mark.anyio
async def test_set_default_model_success(
    client: TestClient,
    test_admin_role: Role,
) -> None:
    """Test PUT /agent/default-model sets the default catalog ref."""
    with patch.object(agent_router, "AgentManagementService") as mock_service_cls:
        mock_svc = AsyncMock()
        mock_svc.set_default_model_ref.return_value = DefaultModelSelection(
            catalog_ref="default_sidecar:default:a:gpt-5",
            model_name="gpt-5",
            model_provider="openai",
            display_name="GPT-5",
        )
        mock_service_cls.return_value = mock_svc

        response = client.put(
            "/agent/default-model",
            json={"catalog_ref": "default_sidecar:default:a:gpt-5"},
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["catalog_ref"] == "default_sidecar:default:a:gpt-5"
        mock_svc.set_default_model_ref.assert_awaited_once_with(
            "default_sidecar:default:a:gpt-5"
        )


@pytest.mark.anyio
async def test_set_default_model_not_found(
    client: TestClient,
    test_admin_role: Role,
) -> None:
    """Test PUT /agent/default-model returns 404 for missing catalog ref."""
    with patch.object(agent_router, "AgentManagementService") as mock_service_cls:
        mock_svc = AsyncMock()
        mock_svc.set_default_model_ref.side_effect = TracecatNotFoundError(
            "Model not found"
        )
        mock_service_cls.return_value = mock_svc

        response = client.put(
            "/agent/default-model",
            json={"catalog_ref": "missing"},
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND
