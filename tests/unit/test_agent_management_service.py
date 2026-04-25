"""Tests for AgentManagementService credential and runtime behavior."""

import uuid
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

import pytest
from cryptography.fernet import Fernet
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncSession
from tracecat_registry._internal import secrets as registry_secrets

import tracecat.agent.service as agent_service
from tracecat import config as tracecat_config
from tracecat.agent.config import PROVIDER_CREDENTIAL_CONFIGS
from tracecat.agent.preset.activities import _load_custom_model_provider_creds
from tracecat.agent.preset.service import AgentPresetService
from tracecat.agent.service import AgentManagementService
from tracecat.agent.types import AgentConfig
from tracecat.auth.types import Role
from tracecat.db.models import (
    AgentCatalog,
    AgentCustomProvider,
    AgentModelAccess,
    Organization,
    OrganizationSecret,
    Workspace,
)
from tracecat.integrations.aws_assume_role import build_workspace_external_id
from tracecat.secrets import secrets_manager
from tracecat.secrets.encryption import encrypt_keyvalues
from tracecat.secrets.enums import SecretType
from tracecat.secrets.schemas import SecretKeyValue


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


def _db_role(org: Organization, workspace: Workspace | None = None) -> Role:
    return Role(
        type="user",
        user_id=uuid.uuid4(),
        workspace_id=workspace.id if workspace is not None else None,
        organization_id=org.id,
        service_id="tracecat-api",
        scopes=frozenset({"*"}),
    )


async def _seed_org_secret(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    name: str,
    values: dict[str, str],
    encryption_key: str,
) -> None:
    encrypted_keys = encrypt_keyvalues(
        [
            SecretKeyValue(key=key, value=SecretStr(value))
            for key, value in values.items()
        ],
        key=encryption_key,
    )
    session.add(
        OrganizationSecret(
            organization_id=org_id,
            name=name,
            type=SecretType.CUSTOM.value,
            encrypted_keys=encrypted_keys,
        )
    )
    await session.flush()


async def _seed_catalog(
    session: AsyncSession,
    *,
    org_id: uuid.UUID | None,
    provider: str,
    model_name: str,
    metadata: dict[str, object] | None = None,
    encrypted_config: bytes | None = None,
    custom_provider_id: uuid.UUID | None = None,
) -> AgentCatalog:
    row = AgentCatalog(
        organization_id=org_id,
        custom_provider_id=custom_provider_id,
        model_provider=provider,
        model_name=model_name,
        model_metadata=metadata or {},
        encrypted_config=encrypted_config,
    )
    session.add(row)
    await session.flush()
    return row


async def _grant_access(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    catalog_id: uuid.UUID,
    workspace_id: uuid.UUID | None = None,
) -> None:
    session.add(
        AgentModelAccess(
            organization_id=org_id,
            workspace_id=workspace_id,
            catalog_id=catalog_id,
        )
    )
    await session.flush()


@pytest.mark.anyio
@pytest.mark.usefixtures("db")
async def test_get_catalog_credentials_respects_workspace_override_access(
    session: AsyncSession,
    svc_organization: Organization,
    svc_workspace: Workspace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    encryption_key = Fernet.generate_key().decode()
    monkeypatch.setattr(
        tracecat_config,
        "TRACECAT__DB_ENCRYPTION_KEY",
        encryption_key,
    )
    inherited_catalog = await _seed_catalog(
        session,
        org_id=None,
        provider="openai",
        model_name="gpt-4.1",
    )
    workspace_catalog = await _seed_catalog(
        session,
        org_id=None,
        provider="anthropic",
        model_name="claude-sonnet-4-5",
    )
    await _grant_access(
        session,
        org_id=svc_organization.id,
        catalog_id=inherited_catalog.id,
    )
    await _grant_access(
        session,
        org_id=svc_organization.id,
        workspace_id=svc_workspace.id,
        catalog_id=workspace_catalog.id,
    )
    await _seed_org_secret(
        session,
        org_id=svc_organization.id,
        name="agent-openai-credentials",
        values={"OPENAI_API_KEY": "live-openai"},
        encryption_key=encryption_key,
    )
    await _seed_org_secret(
        session,
        org_id=svc_organization.id,
        name="agent-anthropic-credentials",
        values={"ANTHROPIC_API_KEY": "live-anthropic"},
        encryption_key=encryption_key,
    )
    await session.commit()

    service = AgentManagementService(
        session=session,
        role=_db_role(svc_organization, svc_workspace),
    )

    assert await service.get_catalog_credentials(inherited_catalog.id) is None
    credentials = await service.get_catalog_credentials(workspace_catalog.id)
    assert credentials == {"ANTHROPIC_API_KEY": "live-anthropic"}


@pytest.mark.anyio
@pytest.mark.usefixtures("db")
async def test_get_catalog_credentials_uses_live_cloud_secret_with_catalog_metadata(
    session: AsyncSession,
    svc_organization: Organization,
    svc_workspace: Workspace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    encryption_key = Fernet.generate_key().decode()
    monkeypatch.setattr(
        tracecat_config,
        "TRACECAT__DB_ENCRYPTION_KEY",
        encryption_key,
    )
    migrated_blob = encrypt_keyvalues(
        [
            SecretKeyValue(key="AWS_ACCESS_KEY_ID", value=SecretStr("old-key")),
            SecretKeyValue(
                key="AWS_INFERENCE_PROFILE_ID",
                value=SecretStr("old-target"),
            ),
        ],
        key=encryption_key,
    )
    catalog = await _seed_catalog(
        session,
        org_id=svc_organization.id,
        provider="bedrock",
        model_name="Claude Sonnet",
        metadata={"inference_profile_id": "metadata-target"},
        encrypted_config=migrated_blob,
    )
    await _grant_access(
        session,
        org_id=svc_organization.id,
        catalog_id=catalog.id,
    )
    await _seed_org_secret(
        session,
        org_id=svc_organization.id,
        name="agent-bedrock-credentials",
        values={
            "AWS_ACCESS_KEY_ID": "live-key",
            "AWS_SECRET_ACCESS_KEY": "live-secret",
            "AWS_REGION": "us-east-1",
        },
        encryption_key=encryption_key,
    )
    await session.commit()

    service = AgentManagementService(
        session=session,
        role=_db_role(svc_organization, svc_workspace),
    )

    credentials = await service.get_catalog_credentials(catalog.id)

    assert credentials is not None
    assert credentials["AWS_ACCESS_KEY_ID"] == "live-key"
    assert credentials["AWS_SECRET_ACCESS_KEY"] == "live-secret"
    assert credentials["AWS_INFERENCE_PROFILE_ID"] == "metadata-target"


@pytest.mark.anyio
@pytest.mark.usefixtures("db")
async def test_get_catalog_credentials_uses_migrated_cloud_target_as_fallback(
    session: AsyncSession,
    svc_organization: Organization,
    svc_workspace: Workspace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    encryption_key = Fernet.generate_key().decode()
    monkeypatch.setattr(
        tracecat_config,
        "TRACECAT__DB_ENCRYPTION_KEY",
        encryption_key,
    )
    migrated_blob = encrypt_keyvalues(
        [
            SecretKeyValue(key="AWS_ACCESS_KEY_ID", value=SecretStr("old-key")),
            SecretKeyValue(
                key="AWS_MODEL_ID",
                value=SecretStr("anthropic.claude-3-haiku-20240307-v1:0"),
            ),
        ],
        key=encryption_key,
    )
    catalog = await _seed_catalog(
        session,
        org_id=svc_organization.id,
        provider="bedrock",
        model_name="Claude Haiku",
        encrypted_config=migrated_blob,
    )
    await _grant_access(
        session,
        org_id=svc_organization.id,
        catalog_id=catalog.id,
    )
    await _seed_org_secret(
        session,
        org_id=svc_organization.id,
        name="agent-bedrock-credentials",
        values={
            "AWS_ACCESS_KEY_ID": "live-key",
            "AWS_SECRET_ACCESS_KEY": "live-secret",
            "AWS_REGION": "us-east-1",
        },
        encryption_key=encryption_key,
    )
    await session.commit()

    service = AgentManagementService(
        session=session,
        role=_db_role(svc_organization, svc_workspace),
    )

    credentials = await service.get_catalog_credentials(catalog.id)

    assert credentials is not None
    assert credentials["AWS_ACCESS_KEY_ID"] == "live-key"
    assert credentials["AWS_MODEL_ID"] == "anthropic.claude-3-haiku-20240307-v1:0"


@pytest.mark.anyio
@pytest.mark.usefixtures("db")
async def test_get_catalog_credentials_decodes_migrated_custom_provider_blob(
    session: AsyncSession,
    svc_organization: Organization,
    svc_workspace: Workspace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    encryption_key = Fernet.generate_key().decode()
    monkeypatch.setattr(
        tracecat_config,
        "TRACECAT__DB_ENCRYPTION_KEY",
        encryption_key,
    )
    migrated_blob = encrypt_keyvalues(
        [
            SecretKeyValue(
                key="CUSTOM_MODEL_PROVIDER_BASE_URL",
                value=SecretStr("https://llm.example.com/v1"),
            ),
            SecretKeyValue(
                key="CUSTOM_MODEL_PROVIDER_API_KEY",
                value=SecretStr("sk-custom"),
            ),
            SecretKeyValue(
                key="CUSTOM_MODEL_PROVIDER_MODEL_NAME",
                value=SecretStr("provider/custom-model"),
            ),
            SecretKeyValue(
                key="CUSTOM_MODEL_PROVIDER_PASSTHROUGH",
                value=SecretStr("true"),
            ),
        ],
        key=encryption_key,
    )
    provider = AgentCustomProvider(
        organization_id=svc_organization.id,
        display_name="Migrated provider",
        base_url=None,
        passthrough=False,
        encrypted_config=migrated_blob,
    )
    session.add(provider)
    await session.flush()
    catalog = await _seed_catalog(
        session,
        org_id=svc_organization.id,
        provider="custom-model-provider",
        model_name="custom-model-provider",
        custom_provider_id=provider.id,
        encrypted_config=migrated_blob,
    )
    await _grant_access(
        session,
        org_id=svc_organization.id,
        catalog_id=catalog.id,
    )
    await session.commit()

    service = AgentManagementService(
        session=session,
        role=_db_role(svc_organization, svc_workspace),
    )

    credentials = await service.get_catalog_credentials(catalog.id)

    assert credentials == {
        "CUSTOM_MODEL_PROVIDER_BASE_URL": "https://llm.example.com/v1",
        "CUSTOM_MODEL_PROVIDER_API_KEY": "sk-custom",
        "CUSTOM_MODEL_PROVIDER_MODEL_NAME": "provider/custom-model",
        "CUSTOM_MODEL_PROVIDER_PASSTHROUGH": "true",
    }


@pytest.mark.anyio
@pytest.mark.usefixtures("db")
async def test_load_custom_model_provider_creds_requires_catalog_access(
    session: AsyncSession,
    svc_organization: Organization,
    svc_workspace: Workspace,
) -> None:
    provider = AgentCustomProvider(
        organization_id=svc_organization.id,
        display_name="Disabled provider",
        base_url="https://llm.example.com/v1",
        passthrough=True,
        encrypted_config=None,
    )
    session.add(provider)
    await session.flush()
    catalog = await _seed_catalog(
        session,
        org_id=svc_organization.id,
        provider="custom-model-provider",
        model_name="custom-model-provider",
        custom_provider_id=provider.id,
    )
    await session.commit()

    service = AgentManagementService(
        session=session,
        role=_db_role(svc_organization, svc_workspace),
    )

    assert (
        await _load_custom_model_provider_creds(
            service,
            catalog_id=catalog.id,
        )
        is None
    )

    await _grant_access(
        session,
        org_id=svc_organization.id,
        catalog_id=catalog.id,
    )
    await session.commit()

    credentials = await _load_custom_model_provider_creds(
        service,
        catalog_id=catalog.id,
    )

    assert credentials == {
        "CUSTOM_MODEL_PROVIDER_BASE_URL": "https://llm.example.com/v1",
        "CUSTOM_MODEL_PROVIDER_PASSTHROUGH": "true",
    }


@pytest.mark.anyio
async def test_with_model_config_sets_registry_and_env_context(role: Role) -> None:
    service = AgentManagementService(AsyncMock(), role=role)
    catalog_id = uuid.uuid4()
    service._get_default_model_catalog_id_setting = AsyncMock(return_value=catalog_id)
    service._get_default_model_name_setting = AsyncMock(
        return_value="claude-opus-4-5-20251101"
    )
    service.get_catalog_credentials = AsyncMock(
        return_value={"ANTHROPIC_API_KEY": "test-key"}
    )

    # ``with_model_config`` issues a follow-up ``SELECT`` against
    # ``agent_catalog`` to resolve the row's provider + model_name. Stub it
    # out so the test session mock doesn't have to round-trip SQL.
    catalog_row = SimpleNamespace(
        id=catalog_id,
        model_provider="anthropic",
        model_name="claude-opus-4-5-20251101",
    )
    service.session = AsyncMock()
    service.session.execute = AsyncMock(
        return_value=SimpleNamespace(scalar_one=lambda: catalog_row)
    )

    assert registry_secrets.get_or_default("ANTHROPIC_API_KEY") is None
    assert secrets_manager.get("ANTHROPIC_API_KEY") is None

    async with service.with_model_config() as model_config:
        assert model_config.name == "claude-opus-4-5-20251101"
        assert model_config.provider == "anthropic"
        assert model_config.catalog_id == catalog_id
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
    service.get_runtime_provider_credentials = AsyncMock(
        return_value={"ANTHROPIC_API_KEY": "workspace-key"}
    )

    assert registry_secrets.get_or_default("ANTHROPIC_API_KEY") is None
    assert secrets_manager.get("ANTHROPIC_API_KEY") is None

    async with service.with_preset_config(preset_id=uuid.uuid4()):
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
    service.get_runtime_provider_credentials = AsyncMock(
        return_value={
            "CUSTOM_MODEL_PROVIDER_BASE_URL": "https://litellm.customer.example",
            "CUSTOM_MODEL_PROVIDER_MODEL_NAME": "customer-routed-model",
            "CUSTOM_MODEL_PROVIDER_PASSTHROUGH": "true",
        }
    )

    async with service.with_preset_config(preset_id=uuid.uuid4()) as config:
        assert config.model_provider == "custom-model-provider"
        assert config.base_url == "https://litellm.customer.example"
        assert config.model_name == "customer-routed-model"
        assert config.passthrough is True

    service.get_runtime_provider_credentials.assert_awaited_once_with(
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
    service.get_provider_credentials = AsyncMock(
        return_value={
            "AWS_ROLE_ARN": "arn:aws:iam::123456789012:role/customer-role",
            "AWS_REGION": "us-east-1",
            "AWS_INFERENCE_PROFILE_ID": "us.anthropic.claude-sonnet-4-20250514-v1:0",
        }
    )
    assert role.workspace_id is not None

    credentials = await service.get_runtime_provider_credentials("bedrock")

    assert credentials is not None
    assert credentials["TRACECAT_AWS_EXTERNAL_ID"] == build_workspace_external_id(
        role.workspace_id
    )


@pytest.mark.anyio
async def test_with_model_config_injects_bedrock_external_id(role: Role) -> None:
    service = AgentManagementService(AsyncMock(), role=role)
    assert role.workspace_id is not None
    external_id = build_workspace_external_id(role.workspace_id)
    catalog_id = uuid.uuid4()
    service._get_default_model_catalog_id_setting = AsyncMock(return_value=catalog_id)
    service._get_default_model_name_setting = AsyncMock(
        return_value="us.anthropic.claude-sonnet-4-20250514-v1:0"
    )
    service.get_catalog_credentials = AsyncMock(
        return_value={
            "AWS_ROLE_ARN": "arn:aws:iam::123456789012:role/customer-role",
            "AWS_REGION": "us-east-1",
            "AWS_INFERENCE_PROFILE_ID": "us.anthropic.claude-sonnet-4-20250514-v1:0",
            "TRACECAT_AWS_EXTERNAL_ID": external_id,
        }
    )
    catalog_row = SimpleNamespace(
        id=catalog_id,
        model_provider="bedrock",
        model_name="us.anthropic.claude-sonnet-4-20250514-v1:0",
    )
    service.session = AsyncMock()
    service.session.execute = AsyncMock(
        return_value=SimpleNamespace(scalar_one=lambda: catalog_row)
    )

    assert registry_secrets.get_or_default("TRACECAT_AWS_EXTERNAL_ID") is None
    assert secrets_manager.get("TRACECAT_AWS_EXTERNAL_ID") is None

    async with service.with_model_config() as model_config:
        assert model_config.name == "us.anthropic.claude-sonnet-4-20250514-v1:0"
        assert registry_secrets.get("TRACECAT_AWS_EXTERNAL_ID") == external_id
        assert secrets_manager.get("TRACECAT_AWS_EXTERNAL_ID") == external_id

    assert registry_secrets.get_or_default("TRACECAT_AWS_EXTERNAL_ID") is None
    assert secrets_manager.get("TRACECAT_AWS_EXTERNAL_ID") is None


@pytest.mark.anyio
async def test_get_runtime_provider_credentials_resolves_azure_openai_client_credentials(
    role: Role,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = AgentManagementService(AsyncMock(), role=role)
    service.get_provider_credentials = AsyncMock(
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
    service.get_provider_credentials = AsyncMock(
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


@pytest.mark.anyio
async def test_get_providers_status_includes_builtin_configs_without_enabled_models(
    role: Role,
) -> None:
    service = AgentManagementService(AsyncMock(), role=role)
    service.check_provider_credentials = AsyncMock(
        side_effect=lambda provider: provider == "anthropic"
    )

    status = await service.get_providers_status()

    assert set(PROVIDER_CREDENTIAL_CONFIGS).issubset(status)
    assert status["anthropic"] is True
    assert status["openai"] is False
