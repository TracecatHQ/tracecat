"""Focused tests for the agent credentials service."""

import uuid
from unittest.mock import AsyncMock, Mock

import pytest

from tracecat.agent.credentials.service import AgentCredentialsService
from tracecat.agent.schemas import ModelCredentialCreate, ModelCredentialUpdate
from tracecat.agent.types import ModelDiscoveryStatus
from tracecat.auth.types import Role
from tracecat.db.models import AgentCatalog
from tracecat.exceptions import TracecatNotFoundError


@pytest.fixture
def role() -> Role:
    return Role(
        type="service",
        service_id="tracecat-api",
        workspace_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        scopes=frozenset(
            {
                "agent:read",
                "agent:update",
                "org:secret:read",
            }
        ),
    )


@pytest.mark.anyio
async def test_list_provider_credential_configs_returns_builtin_order(
    role: Role,
) -> None:
    service = AgentCredentialsService(AsyncMock(), role=role)

    configs = await service.list_provider_credential_configs()

    assert [config.provider for config in configs] == [
        "openai",
        "anthropic",
        "gemini",
        "vertex_ai",
        "bedrock",
        "azure_openai",
        "azure_ai",
    ]


@pytest.mark.anyio
async def test_get_provider_credential_config_rejects_unknown_provider(
    role: Role,
) -> None:
    service = AgentCredentialsService(AsyncMock(), role=role)

    with pytest.raises(TracecatNotFoundError, match="Provider invalid not found"):
        await service.get_provider_credential_config("invalid")


@pytest.mark.anyio
async def test_create_provider_credentials_creates_secret(
    role: Role,
) -> None:
    service = AgentCredentialsService(AsyncMock(), role=role)
    created_secret = Mock()
    service.secrets_service.get_org_secret_by_name = AsyncMock(
        side_effect=[TracecatNotFoundError("missing"), created_secret]
    )
    service.secrets_service.create_org_secret = AsyncMock()

    secret = await service.create_provider_credentials(
        ModelCredentialCreate(
            provider="openai",
            credentials={"OPENAI_API_KEY": "sk-test"},
        )
    )

    assert secret is created_secret
    service.secrets_service.create_org_secret.assert_awaited_once()


@pytest.mark.anyio
async def test_update_provider_credentials_updates_secret(
    role: Role,
) -> None:
    service = AgentCredentialsService(AsyncMock(), role=role)
    secret = Mock()
    service.secrets_service.get_org_secret_by_name = AsyncMock(return_value=secret)
    service.secrets_service.update_org_secret = AsyncMock()

    updated = await service.update_provider_credentials(
        "openai",
        ModelCredentialUpdate(credentials={"OPENAI_API_KEY": "sk-test"}),
    )

    assert updated is secret
    service.secrets_service.update_org_secret.assert_awaited_once()


@pytest.mark.anyio
async def test_get_providers_status_uses_internal_lookup(role: Role) -> None:
    service = AgentCredentialsService(AsyncMock(), role=role)
    service._load_provider_credentials = AsyncMock(
        side_effect=lambda provider: (
            {"OPENAI_API_KEY": "org-key"} if provider == "openai" else None
        )
    )

    status = await service.get_providers_status()

    assert status["openai"] is True
    assert status["anthropic"] is False
    service._load_provider_credentials.assert_any_await("openai")
    service._load_provider_credentials.assert_any_await("anthropic")


@pytest.mark.anyio
async def test_list_providers_defaults_to_configured_only(role: Role) -> None:
    service = AgentCredentialsService(AsyncMock(), role=role)
    service._get_builtin_catalog_state = AsyncMock(
        return_value=(ModelDiscoveryStatus.READY, None, None)
    )
    service._load_provider_credentials = AsyncMock(
        side_effect=lambda provider: (
            {"OPENAI_API_KEY": "org-key"} if provider == "openai" else None
        )
    )
    service._list_builtin_catalog_rows = AsyncMock()
    service._list_enabled_catalog_ids = AsyncMock()

    providers = await service.list_providers()

    assert [provider.provider for provider in providers] == ["openai"]
    service._list_builtin_catalog_rows.assert_not_awaited()
    service._list_enabled_catalog_ids.assert_not_awaited()


@pytest.mark.anyio
async def test_list_providers_can_include_unconfigured_and_discovered_models(
    role: Role,
) -> None:
    service = AgentCredentialsService(AsyncMock(), role=role)
    catalog_row = AgentCatalog(
        id=uuid.uuid4(),
        organization_id=None,
        source_id=None,
        model_provider="openai",
        model_name="gpt-5.2",
        model_metadata={"tier": "preview"},
    )
    service._get_builtin_catalog_state = AsyncMock(
        return_value=(ModelDiscoveryStatus.READY, None, None)
    )
    service._list_builtin_catalog_rows = AsyncMock(return_value=[catalog_row])
    service._list_enabled_catalog_ids = AsyncMock(return_value={catalog_row.id})
    service._load_provider_credentials = AsyncMock(
        side_effect=lambda provider: (
            {"OPENAI_API_KEY": "org-key"} if provider == "openai" else None
        )
    )

    providers = await service.list_providers(
        configured_only=False,
        include_discovered_models=True,
    )

    openai = next(provider for provider in providers if provider.provider == "openai")
    anthropic = next(
        provider for provider in providers if provider.provider == "anthropic"
    )
    assert openai.credentials_configured is True
    assert openai.discovered_models[0].enabled is True
    assert anthropic.credentials_configured is False
    assert anthropic.discovered_models == []
