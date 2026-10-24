"""Tests for AgentManagementService credential and runtime behavior."""

import uuid
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, Mock

import pytest
import sqlalchemy as sa
from cryptography.fernet import Fernet
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncSession
from tracecat_registry._internal import secrets as registry_secrets

import tracecat.agent.service as agent_service
from tracecat import config as tracecat_config
from tracecat.agent.bedrock import BedrockDiscoveredModel, BedrockError
from tracecat.agent.catalog.service import AgentCatalogService
from tracecat.agent.config import PROVIDER_CREDENTIAL_CONFIGS
from tracecat.agent.exceptions import (
    ProviderKeyCheckError,
    ProviderKeyCheckErrorCode,
)
from tracecat.agent.preset.activities import _load_custom_model_provider_creds
from tracecat.agent.preset.service import AgentPresetService
from tracecat.agent.schemas import ModelCredentialCreate, ModelCredentialUpdate
from tracecat.agent.service import AgentManagementService
from tracecat.agent.types import AgentConfig
from tracecat.auth.types import Role
from tracecat.db.models import (
    AgentCatalog,
    AgentCustomProvider,
    AgentModelAccess,
    Organization,
    OrganizationSecret,
    OrganizationSecretStore,
    Secret,
    Workspace,
)
from tracecat.exceptions import TracecatNotFoundError
from tracecat.integrations.aws_assume_role import build_workspace_external_id
from tracecat.secrets import secrets_manager
from tracecat.secrets.encryption import encrypt_keyvalues
from tracecat.secrets.enums import SecretSource, SecretType
from tracecat.secrets.schemas import SecretKeyValue
from tracecat.secrets.types import ExternalSecretReference


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
@pytest.mark.parametrize("source", ["local", "aws"])
async def test_workspace_provider_credentials_resolve_source_and_preserve_fallback(
    role: Role, monkeypatch: pytest.MonkeyPatch, source: str
) -> None:
    encryption_key = Fernet.generate_key().decode()
    monkeypatch.setattr(tracecat_config, "TRACECAT__DB_ENCRYPTION_KEY", encryption_key)
    service = AgentManagementService(AsyncMock(), role=role)
    secret = Secret(
        id=uuid.uuid4(),
        workspace_id=role.workspace_id,
        name="openai",
        environment="default",
        source=SecretSource.LOCAL,
        encrypted_keys=encrypt_keyvalues(
            [SecretKeyValue(key="OPENAI_API_KEY", value=SecretStr("local-key"))],
            key=encryption_key,
        ),
    )
    if source.startswith("aws"):
        secret.source = SecretSource.AWS_SECRETS_MANAGER
        secret.encrypted_keys = encrypt_keyvalues([], key=encryption_key)
        secret.store = OrganizationSecretStore(
            id=uuid.uuid4(),
            organization_id=role.organization_id,
            name="test-store",
            enabled=True,
            provider="aws_secrets_manager",
            config={
                "provider": "aws_secrets_manager",
                "role_arn": "arn:aws:iam::123456789012:role/test-reader",
                "region": "us-east-1",
                "external_id": "test-external-id",
            },
        )
        secret.remote_reference = "test-provider-key"
        secret.remote_key_mapping = {"mode": "whole_string", "keys": ["OPENAI_API_KEY"]}

    monkeypatch.setattr(agent_service, "check_entitlement", AsyncMock())
    search = AsyncMock(return_value=[secret])
    monkeypatch.setattr(service.secrets_service, "search_secrets", search)

    class _StubBackend:
        """Local-source secrets must never reach a store backend."""

        async def resolve(
            self, references: list[ExternalSecretReference]
        ) -> dict[str, dict[str, str]]:
            assert source == "aws"
            assert len(references) == 1
            assert secret.store is not None
            assert references[0].store_id == secret.store.id
            return {"openai": {"OPENAI_API_KEY": "remote-key"}}

    monkeypatch.setattr(agent_service, "get_backend", lambda provider: _StubBackend())
    assert service.presets is not None
    monkeypatch.setattr(
        service.presets,
        "resolve_agent_preset_config",
        AsyncMock(
            return_value=AgentConfig(model_name="gpt-4.1", model_provider="openai")
        ),
    )
    expected = {"local": "local-key", "aws": "remote-key"}
    async with service.with_preset_config(preset_id=uuid.uuid4()):
        assert registry_secrets.get("OPENAI_API_KEY") == expected[source]


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


@pytest.fixture
def encryption_key(monkeypatch: pytest.MonkeyPatch) -> str:
    key = Fernet.generate_key().decode()
    monkeypatch.setattr(tracecat_config, "TRACECAT__DB_ENCRYPTION_KEY", key)
    return key


def _encrypt(values: dict[str, str], key: str) -> bytes:
    return encrypt_keyvalues(
        [SecretKeyValue(key=k, value=SecretStr(v)) for k, v in values.items()],
        key=key,
    )


async def _custom_provider(
    session: AsyncSession,
    org_id: uuid.UUID,
    *,
    base_url: str | None = "https://llm.example.com/v1",
    passthrough: bool = False,
    encrypted_config: bytes | None = None,
) -> AgentCustomProvider:
    provider = AgentCustomProvider(
        organization_id=org_id,
        display_name="Migrated provider",
        base_url=base_url,
        passthrough=passthrough,
        encrypted_config=encrypted_config,
    )
    session.add(provider)
    await session.flush()
    return provider


def _db_service(
    session: AsyncSession, org: Organization, workspace: Workspace
) -> AgentManagementService:
    return AgentManagementService(session=session, role=_db_role(org, workspace))


@pytest.mark.anyio
@pytest.mark.usefixtures("db")
async def test_get_catalog_credentials_respects_workspace_override_access(
    session: AsyncSession,
    svc_organization: Organization,
    svc_workspace: Workspace,
    encryption_key: str,
) -> None:
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
        catalog_id=workspace_catalog.id,
    )
    # The workspace subset narrows the org set to one model.
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

    service = _db_service(session, svc_organization, svc_workspace)

    with pytest.raises(agent_service.TracecatAuthorizationError):
        await service.get_catalog_credentials(inherited_catalog.id)
    credentials = await service.get_catalog_credentials(workspace_catalog.id)
    assert credentials == {"ANTHROPIC_API_KEY": "live-anthropic"}


@pytest.mark.anyio
@pytest.mark.usefixtures("db")
async def test_get_catalog_credentials_uses_live_cloud_secret_with_catalog_metadata(
    session: AsyncSession,
    svc_organization: Organization,
    svc_workspace: Workspace,
    encryption_key: str,
) -> None:
    migrated_blob = _encrypt(
        {"AWS_ACCESS_KEY_ID": "old-key", "AWS_INFERENCE_PROFILE_ID": "old-target"},
        encryption_key,
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

    service = _db_service(session, svc_organization, svc_workspace)

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
    encryption_key: str,
) -> None:
    migrated_blob = _encrypt(
        {
            "AWS_ACCESS_KEY_ID": "old-key",
            "AWS_MODEL_ID": "anthropic.claude-3-haiku-20240307-v1:0",
        },
        encryption_key,
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

    service = _db_service(session, svc_organization, svc_workspace)

    credentials = await service.get_catalog_credentials(catalog.id)

    assert credentials is not None
    assert credentials["AWS_ACCESS_KEY_ID"] == "live-key"
    assert credentials["AWS_MODEL_ID"] == "anthropic.claude-3-haiku-20240307-v1:0"


@pytest.mark.anyio
@pytest.mark.usefixtures("db")
async def test_get_catalog_credentials_preserves_migrated_custom_provider_base_url_fallback(
    session: AsyncSession,
    svc_organization: Organization,
    svc_workspace: Workspace,
    encryption_key: str,
) -> None:
    migrated_blob = _encrypt(
        {
            "CUSTOM_MODEL_PROVIDER_BASE_URL": "https://llm.example.com/v1",
            "CUSTOM_MODEL_PROVIDER_API_KEY": "sk-custom",
            "CUSTOM_MODEL_PROVIDER_MODEL_NAME": "provider/custom-model",
            "CUSTOM_MODEL_PROVIDER_PASSTHROUGH": "true",
        },
        encryption_key,
    )
    provider = await _custom_provider(
        session, svc_organization.id, base_url=None, encrypted_config=migrated_blob
    )
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

    service = _db_service(session, svc_organization, svc_workspace)

    credentials = await service.get_catalog_credentials(catalog.id)

    # The selected catalog row's model_name wins over the shared provider-level
    # CUSTOM_MODEL_PROVIDER_MODEL_NAME baked into the migrated blob
    # ("provider/custom-model"), so per-model selections aren't all collapsed
    # onto one model.
    assert credentials == {
        "CUSTOM_MODEL_PROVIDER_BASE_URL": "https://llm.example.com/v1",
        "CUSTOM_MODEL_PROVIDER_API_KEY": "sk-custom",
        "CUSTOM_MODEL_PROVIDER_MODEL_NAME": "custom-model-provider",
        "CUSTOM_MODEL_PROVIDER_PASSTHROUGH": "false",
    }

    provider.base_url = "https://column.example.com/v1"
    await session.commit()

    credentials = await service.get_catalog_credentials(catalog.id)

    assert credentials is not None
    assert credentials["CUSTOM_MODEL_PROVIDER_BASE_URL"] == (
        "https://column.example.com/v1"
    )


@pytest.mark.anyio
@pytest.mark.usefixtures("db")
async def test_get_catalog_credentials_distinct_models_share_one_custom_provider(
    session: AsyncSession,
    svc_organization: Organization,
    svc_workspace: Workspace,
    encryption_key: str,
) -> None:
    """Each catalog row resolves to its own model_name even when several rows
    share one custom provider whose blob carries a stale, provider-wide
    CUSTOM_MODEL_PROVIDER_MODEL_NAME (regression: every selection collapsing
    onto the single legacy model name).
    """
    # Migrated/legacy blob carrying one shared model name for the provider.
    migrated_blob = _encrypt(
        {
            "CUSTOM_MODEL_PROVIDER_BASE_URL": "https://llm.example.com/v1",
            "CUSTOM_MODEL_PROVIDER_API_KEY": "sk-custom",
            "CUSTOM_MODEL_PROVIDER_MODEL_NAME": "stale/opus-4-6",
        },
        encryption_key,
    )
    provider = await _custom_provider(
        session, svc_organization.id, encrypted_config=migrated_blob
    )

    gemini = await _seed_catalog(
        session,
        org_id=svc_organization.id,
        provider="custom-model-provider",
        model_name="custom-gemini",
        custom_provider_id=provider.id,
    )
    opus = await _seed_catalog(
        session,
        org_id=svc_organization.id,
        provider="custom-model-provider",
        model_name="custom-opus-4-8",
        custom_provider_id=provider.id,
    )
    for catalog_row in (gemini, opus):
        await _grant_access(
            session,
            org_id=svc_organization.id,
            catalog_id=catalog_row.id,
        )
    await session.commit()

    service = _db_service(session, svc_organization, svc_workspace)

    gemini_creds = await service.get_catalog_credentials(gemini.id)
    opus_creds = await service.get_catalog_credentials(opus.id)

    assert gemini_creds is not None
    assert opus_creds is not None
    # The selected row's model_name wins; the shared "stale/opus-4-6" never leaks.
    assert gemini_creds["CUSTOM_MODEL_PROVIDER_MODEL_NAME"] == "custom-gemini"
    assert opus_creds["CUSTOM_MODEL_PROVIDER_MODEL_NAME"] == "custom-opus-4-8"
    assert (
        gemini_creds["CUSTOM_MODEL_PROVIDER_MODEL_NAME"]
        != opus_creds["CUSTOM_MODEL_PROVIDER_MODEL_NAME"]
    )
    # Shared provider-level credentials still resolve as expected.
    assert gemini_creds["CUSTOM_MODEL_PROVIDER_API_KEY"] == "sk-custom"
    assert opus_creds["CUSTOM_MODEL_PROVIDER_BASE_URL"] == "https://llm.example.com/v1"


@pytest.mark.anyio
@pytest.mark.usefixtures("db")
async def test_get_catalog_credentials_legacy_placeholder_row_keeps_blob_model_name(
    session: AsyncSession,
    svc_organization: Organization,
    svc_workspace: Workspace,
    encryption_key: str,
) -> None:
    """The single legacy backfill catalog row carries the "custom" placeholder
    model_name; the real upstream model name lives in the provider blob. For
    that row the blob's model name must stay authoritative (the per-row pin is
    only for real, discovered per-model rows).
    """
    migrated_blob = _encrypt(
        {
            "CUSTOM_MODEL_PROVIDER_BASE_URL": "https://llm.example.com/v1",
            "CUSTOM_MODEL_PROVIDER_API_KEY": "sk-custom",
            "CUSTOM_MODEL_PROVIDER_MODEL_NAME": "provider/real-upstream-model",
        },
        encryption_key,
    )
    provider = await _custom_provider(
        session, svc_organization.id, encrypted_config=migrated_blob
    )
    # The backfill writes the "custom" placeholder on the legacy linked row.
    catalog = await _seed_catalog(
        session,
        org_id=svc_organization.id,
        provider="custom-model-provider",
        model_name="custom",
        custom_provider_id=provider.id,
    )
    await _grant_access(
        session,
        org_id=svc_organization.id,
        catalog_id=catalog.id,
    )
    await session.commit()

    service = _db_service(session, svc_organization, svc_workspace)

    credentials = await service.get_catalog_credentials(catalog.id)

    assert credentials is not None
    # The placeholder must NOT overwrite the blob's real model name.
    assert (
        credentials["CUSTOM_MODEL_PROVIDER_MODEL_NAME"]
        == "provider/real-upstream-model"
    )


@pytest.mark.anyio
@pytest.mark.usefixtures("db")
async def test_load_custom_model_provider_creds_requires_catalog_access(
    session: AsyncSession,
    svc_organization: Organization,
    svc_workspace: Workspace,
) -> None:
    provider = await _custom_provider(session, svc_organization.id, passthrough=True)
    catalog = await _seed_catalog(
        session,
        org_id=svc_organization.id,
        provider="custom-model-provider",
        model_name="custom-model-provider",
        custom_provider_id=provider.id,
    )
    await session.commit()

    service = _db_service(session, svc_organization, svc_workspace)

    with pytest.raises(agent_service.TracecatAuthorizationError):
        await _load_custom_model_provider_creds(
            service,
            catalog_id=catalog.id,
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
        # The selected row's model_name is pinned (it's a real, non-placeholder
        # name) so the provider blob can't override the per-row selection.
        "CUSTOM_MODEL_PROVIDER_MODEL_NAME": "custom-model-provider",
    }


@pytest.mark.anyio
@pytest.mark.usefixtures("db")
async def test_delete_custom_provider_legacy_secret_preserves_v2_catalog_access(
    session: AsyncSession,
    svc_organization: Organization,
    svc_workspace: Workspace,
    encryption_key: str,
) -> None:
    await _seed_org_secret(
        session,
        org_id=svc_organization.id,
        name="agent-custom-model-provider-credentials",
        values={"CUSTOM_MODEL_PROVIDER_API_KEY": "legacy-key"},
        encryption_key=encryption_key,
    )
    provider = await _custom_provider(session, svc_organization.id, passthrough=True)
    v2_catalog = await _seed_catalog(
        session,
        org_id=svc_organization.id,
        provider="custom-model-provider",
        model_name="custom",
        custom_provider_id=provider.id,
    )
    legacy_catalog = await _seed_catalog(
        session,
        org_id=svc_organization.id,
        provider="custom-model-provider",
        model_name="legacy-custom",
    )
    await _grant_access(
        session,
        org_id=svc_organization.id,
        catalog_id=v2_catalog.id,
    )
    await _grant_access(
        session,
        org_id=svc_organization.id,
        catalog_id=legacy_catalog.id,
    )
    await session.commit()

    service = _db_service(session, svc_organization, svc_workspace)

    await service.delete_provider_credentials("custom-model-provider")

    deleted_secret_id = await session.scalar(
        sa.select(OrganizationSecret.id).where(
            OrganizationSecret.organization_id == svc_organization.id,
            OrganizationSecret.name == "agent-custom-model-provider-credentials",
        )
    )
    assert deleted_secret_id is None
    remaining_catalog_ids = set(
        (
            await session.execute(
                sa.select(AgentModelAccess.catalog_id).where(
                    AgentModelAccess.organization_id == svc_organization.id,
                    AgentModelAccess.workspace_id.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )
    assert v2_catalog.id in remaining_catalog_ids
    assert legacy_catalog.id not in remaining_catalog_ids


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


def _service_with_preset(
    role: Role,
    config: AgentConfig,
    *,
    workspace_credentials: dict[str, str] | None,
    runtime_credentials: dict[str, str] | None = None,
) -> AgentManagementService:
    service = AgentManagementService(AsyncMock(), role=role)
    service.presets = cast(
        AgentPresetService,
        SimpleNamespace(resolve_agent_preset_config=AsyncMock(return_value=config)),
    )
    service.get_workspace_provider_credentials = AsyncMock(
        return_value=workspace_credentials
    )
    service.get_runtime_provider_credentials = AsyncMock(
        return_value=runtime_credentials
    )
    return service


_OPUS_CONFIG = AgentConfig(
    model_name="claude-opus-4-5-20251101", model_provider="anthropic"
)


@pytest.mark.anyio
async def test_with_preset_config_sets_registry_and_env_context(role: Role) -> None:
    service = _service_with_preset(
        role,
        _OPUS_CONFIG,
        workspace_credentials={"ANTHROPIC_API_KEY": "workspace-key"},
        runtime_credentials={"ANTHROPIC_API_KEY": "org-key"},
    )

    assert registry_secrets.get_or_default("ANTHROPIC_API_KEY") is None
    assert secrets_manager.get("ANTHROPIC_API_KEY") is None

    async with service.with_preset_config(preset_id=uuid.uuid4()):
        assert registry_secrets.get("ANTHROPIC_API_KEY") == "workspace-key"
        assert secrets_manager.get("ANTHROPIC_API_KEY") == "workspace-key"

    assert registry_secrets.get_or_default("ANTHROPIC_API_KEY") is None
    assert secrets_manager.get("ANTHROPIC_API_KEY") is None
    cast(AsyncMock, service.get_runtime_provider_credentials).assert_not_awaited()


@pytest.mark.anyio
async def test_with_preset_config_catalog_falls_back_to_workspace_credentials(
    role: Role,
) -> None:
    catalog_id = uuid.uuid4()
    service = _service_with_preset(
        role,
        AgentConfig(
            model_name="legacy-claude",
            model_provider="anthropic",
            catalog_id=catalog_id,
        ),
        workspace_credentials={"ANTHROPIC_API_KEY": "workspace-key"},
        runtime_credentials={"ANTHROPIC_API_KEY": "org-key"},
    )
    service.get_catalog_credentials = AsyncMock(return_value=None)

    async with service.with_preset_config(preset_id=uuid.uuid4()) as config:
        assert config.catalog_id is None
        assert config.model_name == "legacy-claude"
        assert config.model_provider == "anthropic"
        assert registry_secrets.get("ANTHROPIC_API_KEY") == "workspace-key"
        assert secrets_manager.get("ANTHROPIC_API_KEY") == "workspace-key"

    service.get_catalog_credentials.assert_awaited_once_with(catalog_id)
    cast(AsyncMock, service.get_runtime_provider_credentials).assert_not_awaited()


@pytest.mark.anyio
async def test_with_preset_config_legacy_path_falls_back_to_org_credentials(
    role: Role,
) -> None:
    service = _service_with_preset(
        role,
        _OPUS_CONFIG,
        workspace_credentials=None,
        runtime_credentials={"ANTHROPIC_API_KEY": "org-key"},
    )

    async with service.with_preset_config(preset_id=uuid.uuid4()):
        assert registry_secrets.get("ANTHROPIC_API_KEY") == "org-key"
        assert secrets_manager.get("ANTHROPIC_API_KEY") == "org-key"

    cast(AsyncMock, service.get_runtime_provider_credentials).assert_awaited_once_with(
        "anthropic"
    )


@pytest.mark.anyio
async def test_with_preset_config_loads_custom_passthrough_base_url_from_workspace_secret(
    role: Role,
) -> None:
    service = _service_with_preset(
        role,
        AgentConfig(
            model_name="customer-alias",
            model_provider="custom-model-provider",
            base_url=None,
        ),
        workspace_credentials={
            "CUSTOM_MODEL_PROVIDER_BASE_URL": "https://litellm.customer.example",
            "CUSTOM_MODEL_PROVIDER_MODEL_NAME": "customer-routed-model",
            "CUSTOM_MODEL_PROVIDER_PASSTHROUGH": "true",
        },
    )

    async with service.with_preset_config(preset_id=uuid.uuid4()) as config:
        assert config.model_provider == "custom-model-provider"
        assert config.base_url == "https://litellm.customer.example"
        assert config.model_name == "customer-routed-model"
        assert config.passthrough is True

    cast(
        AsyncMock, service.get_workspace_provider_credentials
    ).assert_awaited_once_with("custom-model-provider")


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


@pytest.mark.parametrize(
    ("metadata", "target_key", "expected_flag"),
    [
        (
            {
                "inference_profile_id": "us.anthropic.claude-3-haiku",
                "use_converse": True,
            },
            "AWS_INFERENCE_PROFILE_ID",
            "true",
        ),
        (
            {
                "model_id": "anthropic.claude-3-haiku-20240307-v1:0",
                "use_converse": False,
            },
            "AWS_MODEL_ID",
            "false",
        ),
    ],
)
def test_catalog_target_credentials_serializes_bool_use_converse(
    metadata: dict[str, object], target_key: str, expected_flag: str
) -> None:
    """use_converse stored in JSONB as a bool must project to 'true'/'false'."""
    row = SimpleNamespace(model_provider="bedrock", model_metadata=metadata)
    svc = AgentManagementService.__new__(AgentManagementService)
    creds = svc._catalog_target_credentials(cast(AgentCatalog, row))
    target = metadata.get("inference_profile_id") or metadata.get("model_id")
    assert creds[target_key] == target
    assert creds["AWS_BEDROCK_USE_CONVERSE"] == expected_flag


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


@pytest.mark.anyio
async def test_refresh_bedrock_catalog_syncs_models_and_subscriptions(
    role: Role, monkeypatch: pytest.MonkeyPatch
) -> None:
    role = role.model_copy(update={"scopes": frozenset({"agent:update"})})
    service = AgentManagementService(AsyncMock(), role=role)
    credentials = {"AWS_BEARER_TOKEN_BEDROCK": "synthetic", "AWS_REGION": "us-east-1"}
    service.get_provider_credentials = AsyncMock(return_value=credentials)
    service._auto_grant_provider_access = AsyncMock()
    client = object()
    create_client = Mock(return_value=client)
    discovered = [
        BedrockDiscoveredModel(
            model_name="vendor.a-v1",
            display_name="A",
            foundation_model_id="vendor.a-v1",
            model_id="vendor.a-v1",
        )
    ]
    subscriptions = {
        "vendor.a-v1": {"status": "subscribed", "checked_at": "t"},
        "vendor.manual-v1": {"status": "not_subscribed", "checked_at": "t"},
    }
    get_subscriptions = Mock(return_value=subscriptions)
    monkeypatch.setattr(agent_service, "create_bedrock_client", create_client)
    monkeypatch.setattr(
        agent_service, "list_bedrock_models", Mock(return_value=discovered)
    )
    monkeypatch.setattr(agent_service, "get_bedrock_subscriptions", get_subscriptions)
    manual_row = SimpleNamespace(model_metadata={"model_id": "vendor.manual-v1"})
    monkeypatch.setattr(
        AgentCatalogService,
        "list_bedrock_rows",
        AsyncMock(return_value=[manual_row]),
    )
    sync = AsyncMock(return_value=1)
    monkeypatch.setattr(AgentCatalogService, "sync_bedrock_models", sync)

    count = await service.refresh_bedrock_catalog()

    assert count == 1
    create_client.assert_called_once_with(credentials, external_ids=[])
    _, foundation_ids = get_subscriptions.call_args.args
    assert set(foundation_ids) == {"vendor.a-v1", "vendor.manual-v1"}
    sync.assert_awaited_once_with(
        org_id=role.organization_id,
        models=discovered,
        subscriptions=subscriptions,
    )
    service._auto_grant_provider_access.assert_awaited_once_with("bedrock")


@pytest.mark.anyio
async def test_refresh_bedrock_catalog_requires_credentials(role: Role) -> None:
    role = role.model_copy(update={"scopes": frozenset({"agent:update"})})
    service = AgentManagementService(AsyncMock(), role=role)
    service.get_provider_credentials = AsyncMock(return_value=None)

    with pytest.raises(TracecatNotFoundError):
        await service.refresh_bedrock_catalog()


@pytest.mark.anyio
async def test_refresh_bedrock_catalog_role_mode_tries_workspace_external_ids(
    role: Role, monkeypatch: pytest.MonkeyPatch
) -> None:
    role = role.model_copy(update={"scopes": frozenset({"agent:update"})})
    assert role.workspace_id is not None
    other_workspace_id = uuid.uuid4()
    session = AsyncMock()
    session.execute.return_value = Mock(
        scalars=Mock(
            return_value=Mock(
                all=Mock(return_value=[role.workspace_id, other_workspace_id])
            )
        )
    )
    service = AgentManagementService(session, role=role)
    credentials = {"AWS_ROLE_ARN": "arn:aws:iam::123456789012:role/customer-role"}
    service.get_provider_credentials = AsyncMock(return_value=credentials)
    create_client = Mock(side_effect=BedrockError("Failed to assume"))
    monkeypatch.setattr(agent_service, "create_bedrock_client", create_client)

    with pytest.raises(ValueError, match="Failed to assume"):
        await service.refresh_bedrock_catalog()

    create_client.assert_called_once_with(
        credentials,
        external_ids=[
            build_workspace_external_id(role.workspace_id),
            build_workspace_external_id(other_workspace_id),
        ],
    )


@pytest.mark.anyio
async def test_subscribe_bedrock_model_resolves_profile_and_records_status(
    role: Role, monkeypatch: pytest.MonkeyPatch
) -> None:
    role = role.model_copy(update={"scopes": frozenset({"agent:update"})})
    service = AgentManagementService(AsyncMock(), role=role)
    service.get_provider_credentials = AsyncMock(
        return_value={"AWS_ACCESS_KEY_ID": "a", "AWS_SECRET_ACCESS_KEY": "b"}
    )
    catalog_id = uuid.uuid4()
    row = SimpleNamespace(
        id=catalog_id,
        organization_id=role.organization_id,
        model_provider="bedrock",
        model_name="profile",
        model_metadata={"inference_profile_id": "us.vendor.a-v1"},
    )
    monkeypatch.setattr(
        AgentCatalogService, "get_catalog_entry", AsyncMock(return_value=row)
    )
    record = AsyncMock()
    monkeypatch.setattr(AgentCatalogService, "set_bedrock_subscription", record)
    client = object()
    monkeypatch.setattr(
        agent_service, "create_bedrock_client", Mock(return_value=client)
    )
    resolve = Mock(return_value="vendor.a-v1")
    monkeypatch.setattr(agent_service, "resolve_profile_foundation_model_id", resolve)
    subscription = {"status": "pending", "checked_at": "t"}
    subscribe = Mock(return_value=subscription)
    monkeypatch.setattr(agent_service, "subscribe_bedrock_model", subscribe)

    result = await service.subscribe_bedrock_model(catalog_id)

    assert result == subscription
    resolve.assert_called_once_with(client, "us.vendor.a-v1")
    subscribe.assert_called_once_with(client, "vendor.a-v1")
    record.assert_awaited_once_with(
        org_id=role.organization_id,
        catalog_id=catalog_id,
        foundation_model_id="vendor.a-v1",
        subscription=subscription,
    )


async def _stored_provider_credentials(
    service: AgentManagementService, provider: str
) -> dict[str, str] | None:
    service.secrets_service.session.expire_all()
    return await service.get_provider_credentials(provider)


@pytest.mark.anyio
@pytest.mark.usefixtures("db")
async def test_create_provider_credentials_rejected_key_is_not_stored(
    session: AsyncSession,
    svc_organization: Organization,
    svc_workspace: Workspace,
    encryption_key: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def reject(provider: str, credentials: dict[str, str]) -> None:
        raise ProviderKeyCheckError(
            "OpenAI didn't accept this API key.",
            code=ProviderKeyCheckErrorCode.INVALID_KEY,
            field="OPENAI_API_KEY",
        )

    monkeypatch.setattr(agent_service, "check_provider_key", reject)
    service = _db_service(session, svc_organization, svc_workspace)

    with pytest.raises(ProviderKeyCheckError):
        await service.create_provider_credentials(
            ModelCredentialCreate(
                provider="openai", credentials={"OPENAI_API_KEY": "sk-bad"}
            )
        )

    assert await _stored_provider_credentials(service, "openai") is None


@pytest.mark.anyio
@pytest.mark.usefixtures("db")
@pytest.mark.parametrize("method", ["create", "update"])
async def test_provider_key_check_sees_stored_key_for_blank_update(
    session: AsyncSession,
    svc_organization: Organization,
    svc_workspace: Workspace,
    encryption_key: str,
    monkeypatch: pytest.MonkeyPatch,
    method: str,
) -> None:
    stored = {"OPENAI_API_KEY": "sk-stored", "OPENAI_BASE_URL": ""}
    await _seed_org_secret(
        session,
        org_id=svc_organization.id,
        name="agent-openai-credentials",
        values=stored,
        encryption_key=encryption_key,
    )
    await session.commit()
    checked: list[dict[str, str]] = []

    async def reject(provider: str, credentials: dict[str, str]) -> None:
        checked.append(dict(credentials))
        raise ProviderKeyCheckError(
            "OpenAI didn't accept this API key.",
            code=ProviderKeyCheckErrorCode.INVALID_KEY,
            field="OPENAI_API_KEY",
        )

    monkeypatch.setattr(agent_service, "check_provider_key", reject)
    service = _db_service(session, svc_organization, svc_workspace)
    update = {"OPENAI_API_KEY": "", "OPENAI_BASE_URL": ""}

    with pytest.raises(ProviderKeyCheckError):
        if method == "create":
            await service.create_provider_credentials(
                ModelCredentialCreate(provider="openai", credentials=update)
            )
        else:
            await service.update_provider_credentials(
                "openai", ModelCredentialUpdate(credentials=update)
            )

    assert checked == [{"OPENAI_API_KEY": "sk-stored", "OPENAI_BASE_URL": ""}]
    assert await _stored_provider_credentials(service, "openai") == stored
