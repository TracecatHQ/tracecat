"""Tests for AgentManagementService credential context behavior."""

import uuid
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

import pytest
from tracecat_registry._internal import secrets as registry_secrets

import tracecat.agent.service as agent_service
from tracecat.agent.preset.service import AgentPresetService
from tracecat.agent.service import AgentManagementService
from tracecat.agent.types import AgentConfig
from tracecat.auth.types import Role
from tracecat.integrations.aws_assume_role import build_workspace_external_id
from tracecat.secrets import secrets_manager


@pytest.fixture
def role() -> Role:
    return Role(
        type="service",
        service_id="tracecat-api",
        workspace_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        scopes=frozenset({"agent:read", "org:secret:read"}),
    )


@pytest.mark.anyio
async def test_with_model_config_sets_registry_and_env_context(role: Role) -> None:
    service = AgentManagementService(AsyncMock(), role=role)
    service.get_default_model = AsyncMock(return_value="claude-opus-4-5-20251101")
    service.get_provider_credentials = AsyncMock(
        return_value={"ANTHROPIC_API_KEY": "test-key"}
    )

    assert registry_secrets.get_or_default("ANTHROPIC_API_KEY") is None
    assert secrets_manager.get("ANTHROPIC_API_KEY") is None

    async with service.with_model_config(use_workspace_credentials=False):
        assert registry_secrets.get("ANTHROPIC_API_KEY") == "test-key"
        assert secrets_manager.get("ANTHROPIC_API_KEY") == "test-key"

    assert registry_secrets.get_or_default("ANTHROPIC_API_KEY") is None
    assert secrets_manager.get("ANTHROPIC_API_KEY") is None


@pytest.mark.anyio
async def test_with_preset_config_sets_registry_and_env_context(role: Role) -> None:
    service = AgentManagementService(AsyncMock(), role=role)
    service.presets = cast(
        AgentPresetService,
        SimpleNamespace(
            resolve_agent_preset_config=AsyncMock(
                return_value=AgentConfig(
                    model_name="claude-opus-4-5-20251101",
                    model_provider="anthropic",
                )
            )
        ),
    )
    service.get_workspace_provider_credentials = AsyncMock(
        return_value={"ANTHROPIC_API_KEY": "workspace-key"}
    )

    assert registry_secrets.get_or_default("ANTHROPIC_API_KEY") is None
    assert secrets_manager.get("ANTHROPIC_API_KEY") is None

    async with service.with_preset_config(
        preset_id=uuid.uuid4(),
        use_workspace_credentials=True,
    ):
        assert registry_secrets.get("ANTHROPIC_API_KEY") == "workspace-key"
        assert secrets_manager.get("ANTHROPIC_API_KEY") == "workspace-key"

    assert registry_secrets.get_or_default("ANTHROPIC_API_KEY") is None
    assert secrets_manager.get("ANTHROPIC_API_KEY") is None


@pytest.mark.anyio
async def test_with_preset_config_loads_custom_passthrough_base_url_from_workspace_secret(
    role: Role,
) -> None:
    service = AgentManagementService(AsyncMock(), role=role)
    service.presets = cast(
        AgentPresetService,
        SimpleNamespace(
            resolve_agent_preset_config=AsyncMock(
                return_value=AgentConfig(
                    model_name="customer-alias",
                    model_provider="custom-model-provider",
                    base_url=None,
                )
            )
        ),
    )
    service.get_workspace_provider_credentials = AsyncMock(
        return_value={
            "CUSTOM_MODEL_PROVIDER_BASE_URL": "https://litellm.customer.example",
            "CUSTOM_MODEL_PROVIDER_MODEL_NAME": "customer-routed-model",
            "CUSTOM_MODEL_PROVIDER_PASSTHROUGH": "true",
        }
    )

    async with service.with_preset_config(
        preset_id=uuid.uuid4(),
        use_workspace_credentials=True,
    ) as config:
        assert config.model_provider == "custom-model-provider"
        assert config.base_url == "https://litellm.customer.example"
        assert config.model_name == "customer-routed-model"
        assert config.passthrough is True

    service.get_workspace_provider_credentials.assert_awaited_once_with(
        "custom-model-provider"
    )


@pytest.mark.anyio
async def test_list_providers_excludes_removed_litellm_provider(role: Role) -> None:
    service = AgentManagementService(AsyncMock(), role=role)

    providers = await service.list_providers()

    assert "litellm" not in providers


@pytest.mark.anyio
async def test_get_runtime_provider_credentials_injects_bedrock_external_id(
    role: Role,
) -> None:
    service = AgentManagementService(AsyncMock(), role=role)
    service.get_workspace_provider_credentials = AsyncMock(
        return_value={
            "AWS_ROLE_ARN": "arn:aws:iam::123456789012:role/customer-role",
            "AWS_REGION": "us-east-1",
            "AWS_INFERENCE_PROFILE_ID": "us.anthropic.claude-sonnet-4-20250514-v1:0",
        }
    )

    credentials = await service.get_runtime_provider_credentials("bedrock")
    assert role.workspace_id is not None

    assert credentials is not None
    assert credentials["TRACECAT_AWS_EXTERNAL_ID"] == build_workspace_external_id(
        role.workspace_id
    )


@pytest.mark.anyio
async def test_with_model_config_injects_bedrock_external_id(role: Role) -> None:
    service = AgentManagementService(AsyncMock(), role=role)
    service.get_default_model = AsyncMock(return_value="bedrock")
    service.get_provider_credentials = AsyncMock(
        return_value={
            "AWS_ROLE_ARN": "arn:aws:iam::123456789012:role/customer-role",
            "AWS_REGION": "us-east-1",
            "AWS_INFERENCE_PROFILE_ID": "us.anthropic.claude-sonnet-4-20250514-v1:0",
        }
    )
    assert role.workspace_id is not None

    assert registry_secrets.get_or_default("TRACECAT_AWS_EXTERNAL_ID") is None
    assert secrets_manager.get("TRACECAT_AWS_EXTERNAL_ID") is None

    async with service.with_model_config(
        use_workspace_credentials=False
    ) as model_config:
        assert model_config.name == "us.anthropic.claude-sonnet-4-20250514-v1:0"
        assert registry_secrets.get("TRACECAT_AWS_EXTERNAL_ID") == (
            build_workspace_external_id(role.workspace_id)
        )
        assert secrets_manager.get("TRACECAT_AWS_EXTERNAL_ID") == (
            build_workspace_external_id(role.workspace_id)
        )

    assert registry_secrets.get_or_default("TRACECAT_AWS_EXTERNAL_ID") is None
    assert secrets_manager.get("TRACECAT_AWS_EXTERNAL_ID") is None


@pytest.mark.anyio
async def test_get_runtime_provider_credentials_resolves_azure_openai_client_credentials(
    role: Role,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = AgentManagementService(AsyncMock(), role=role)
    service.get_workspace_provider_credentials = AsyncMock(
        return_value={
            "AZURE_API_BASE": "https://example.openai.azure.com",
            "AZURE_API_VERSION": "2024-02-15-preview",
            "AZURE_DEPLOYMENT_NAME": "gpt-4o",
            "AZURE_TENANT_ID": "tenant-id",
            "AZURE_CLIENT_ID": "client-id",
            "AZURE_CLIENT_SECRET": "client-secret",
        }
    )

    async def mock_resolve_azure_ad_token(
        credentials: dict[str, str],
    ) -> dict[str, str]:
        return credentials | {"AZURE_AD_TOKEN": "entra-token"}

    monkeypatch.setattr(
        agent_service,
        "_resolve_azure_ad_token",
        mock_resolve_azure_ad_token,
    )

    credentials = await service.get_runtime_provider_credentials("azure_openai")

    assert credentials is not None
    assert credentials["AZURE_AD_TOKEN"] == "entra-token"


@pytest.mark.anyio
async def test_get_runtime_provider_credentials_rejects_incomplete_azure_client_credentials(
    role: Role,
) -> None:
    service = AgentManagementService(AsyncMock(), role=role)
    service.get_workspace_provider_credentials = AsyncMock(
        return_value={
            "AZURE_API_BASE": "https://example.services.ai.azure.com/anthropic",
            "AZURE_AI_MODEL_NAME": "claude-sonnet-4-5",
            "AZURE_TENANT_ID": "tenant-id",
            "AZURE_CLIENT_ID": "client-id",
        }
    )

    with pytest.raises(
        ValueError,
        match="Azure Entra client credentials require AZURE_TENANT_ID, "
        "AZURE_CLIENT_ID, and AZURE_CLIENT_SECRET",
    ):
        await service.get_runtime_provider_credentials("azure_ai")
