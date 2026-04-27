from __future__ import annotations

import asyncio
import contextlib
import uuid
from collections.abc import AsyncIterator, Iterator, Mapping
from dataclasses import replace
from datetime import UTC, datetime

import orjson
import sqlalchemy as sa
from azure.identity.aio import ClientSecretCredential
from google.auth.transport.requests import Request as GoogleAuthRequest
from google.oauth2 import service_account
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from tracecat_registry._internal import secrets as registry_secrets

from tracecat.agent.access.service import AgentModelAccessService
from tracecat.agent.catalog.schemas import AgentCatalogRead
from tracecat.agent.config import MODEL_CONFIGS, PROVIDER_CREDENTIAL_CONFIGS
from tracecat.agent.preset.service import AgentPresetService
from tracecat.agent.schemas import (
    DefaultModelSelection,
    ModelConfig,
    ModelCredentialCreate,
    ModelCredentialUpdate,
    ProviderCredentialConfig,
)
from tracecat.agent.types import AgentConfig
from tracecat.auth.secrets import get_db_encryption_key
from tracecat.auth.types import Role
from tracecat.authz.controls import require_scope
from tracecat.db.models import (
    AgentCatalog,
    AgentCustomProvider,
    AgentModelAccess,
    OrganizationSecret,
    Secret,
)
from tracecat.exceptions import TracecatAuthorizationError, TracecatNotFoundError
from tracecat.integrations.aws_assume_role import build_workspace_external_id
from tracecat.logger import logger
from tracecat.secrets import secrets_manager
from tracecat.secrets.constants import DEFAULT_SECRETS_ENVIRONMENT
from tracecat.secrets.encryption import decrypt_keyvalues, decrypt_value
from tracecat.secrets.enums import SecretType
from tracecat.secrets.schemas import SecretCreate, SecretKeyValue, SecretUpdate
from tracecat.secrets.service import SecretsService
from tracecat.service import BaseOrgService
from tracecat.settings.schemas import SettingCreate, SettingUpdate, ValueType
from tracecat.settings.service import SettingsService

_AWS_ASSUME_ROLE_EXTERNAL_ID_SECRET_KEY = "TRACECAT_AWS_EXTERNAL_ID"
_VERTEX_BEARER_TOKEN_KEY = "VERTEX_AI_BEARER_TOKEN"
_GOOGLE_CLOUD_SCOPE = "https://www.googleapis.com/auth/cloud-platform"
_AZURE_COGNITIVE_SCOPE = "https://cognitiveservices.azure.com/.default"
_DEFAULT_MODEL_SETTING_KEY = "agent_default_model"
_DEFAULT_MODEL_CATALOG_ID_SETTING_KEY = "agent_default_model_catalog_id"
_CLOUD_PROVIDER_TARGET_KEYS: dict[str, tuple[tuple[str, str], ...]] = {
    "bedrock": (
        ("inference_profile_id", "AWS_INFERENCE_PROFILE_ID"),
        ("model_id", "AWS_MODEL_ID"),
    ),
    "azure_openai": (("deployment_name", "AZURE_DEPLOYMENT_NAME"),),
    "azure_ai": (("azure_ai_model_name", "AZURE_AI_MODEL_NAME"),),
    "vertex_ai": (("vertex_model", "VERTEX_AI_MODEL"),),
}
_LEGACY_CUSTOM_PROVIDER_CONFIG_KEYS = frozenset(
    {
        "CUSTOM_MODEL_PROVIDER_API_KEY",
        "CUSTOM_MODEL_PROVIDER_BASE_URL",
        "CUSTOM_MODEL_PROVIDER_MODEL_NAME",
        "CUSTOM_MODEL_PROVIDER_PASSTHROUGH",
    }
)


def _is_legacy_custom_provider_config(payload: Mapping[object, object]) -> bool:
    """Return true for migrated env-var-shaped custom provider config."""
    return any(key in payload for key in _LEGACY_CUSTOM_PROVIDER_CONFIG_KEYS)


def _legacy_custom_provider_credentials(
    payload: Mapping[object, object],
) -> dict[str, str]:
    """Extract runtime credential keys from migrated custom provider config."""
    credentials: dict[str, str] = {}
    for key in _LEGACY_CUSTOM_PROVIDER_CONFIG_KEYS:
        value = payload.get(key)
        if isinstance(value, str) and value:
            credentials[key] = value
    return credentials


def _decrypt_custom_provider_config(
    provider_row: AgentCustomProvider,
) -> dict[str, str]:
    """Decrypt an ``AgentCustomProvider.encrypted_config`` blob.

    Handles the two on-disk formats the column may carry:

    - **CRUD-path.** ``encrypt_value(orjson.dumps({"api_key": ..., "custom_headers": ...}))``
      — written by ``AgentCustomProviderService.create_provider`` /
      ``update_provider``.
    - **Migration-path.** Raw ``encrypt_keyvalues(list[SecretKeyValue])`` blob
      copied byte-for-byte from the legacy
      ``agent-custom-model-provider-credentials`` org secret by the v2
      backfill.

    Returns ``{}`` on decrypt failure rather than raising so callers can
    still surface plaintext columns. Errors are logged so operators can
    spot rotation drift.
    """
    if provider_row.encrypted_config is None:
        return {}

    key = get_db_encryption_key()
    credentials: dict[str, str] = {}

    try:
        plain = decrypt_value(provider_row.encrypted_config, key=key)
        payload = orjson.loads(plain)
        if isinstance(payload, dict):
            if _is_legacy_custom_provider_config(payload):
                return _legacy_custom_provider_credentials(payload)
            api_key = payload.get("api_key")
            if isinstance(api_key, str) and api_key:
                credentials["CUSTOM_MODEL_PROVIDER_API_KEY"] = api_key
            base_url = payload.get("base_url")
            if isinstance(base_url, str) and base_url:
                credentials["CUSTOM_MODEL_PROVIDER_BASE_URL"] = base_url
            model_name = payload.get("model_name")
            if isinstance(model_name, str) and model_name:
                credentials["CUSTOM_MODEL_PROVIDER_MODEL_NAME"] = model_name
            passthrough = payload.get("passthrough")
            if isinstance(passthrough, bool):
                credentials["CUSTOM_MODEL_PROVIDER_PASSTHROUGH"] = (
                    "true" if passthrough else "false"
                )
            elif isinstance(passthrough, str) and passthrough:
                credentials["CUSTOM_MODEL_PROVIDER_PASSTHROUGH"] = passthrough
            return credentials
    except Exception:
        pass

    try:
        decrypted = decrypt_keyvalues(provider_row.encrypted_config, key=key)
        for kv in decrypted:
            credentials[kv.key] = kv.value.get_secret_value()
    except Exception:
        logger.exception(
            "Failed to decrypt custom provider encrypted_config",
            custom_provider_id=str(provider_row.id),
        )
        return {}
    return credentials


def _refresh_vertex_token(credentials_blob: str) -> str:
    """Synchronous helper that refreshes a Vertex AI SA token.

    Isolated so it can be called via ``asyncio.to_thread``.
    """
    creds = service_account.Credentials.from_service_account_info(
        orjson.loads(credentials_blob),
        scopes=[_GOOGLE_CLOUD_SCOPE],
    )
    creds.refresh(GoogleAuthRequest())
    return creds.token


async def _resolve_vertex_bearer_token(
    credentials: dict[str, str],
) -> dict[str, str]:
    """Resolve the Vertex AI bearer token off-thread and inject it into the credentials dict.

    The result is cached by the upstream ``aiocache`` TTL cache in
    ``credentials.py``, so the blocking ``refresh()`` round-trip only
    happens once per cache window (~60 s by default).
    """
    blob = credentials["GOOGLE_API_CREDENTIALS"]
    token = await asyncio.to_thread(_refresh_vertex_token, blob)
    augmented = credentials.copy()
    augmented[_VERTEX_BEARER_TOKEN_KEY] = token
    return augmented


async def _resolve_azure_ad_token(
    credentials: dict[str, str],
) -> dict[str, str]:
    """Resolve an Azure Entra bearer token from client credentials."""
    tenant_id = credentials.get("AZURE_TENANT_ID")
    client_id = credentials.get("AZURE_CLIENT_ID")
    client_secret = credentials.get("AZURE_CLIENT_SECRET")
    configured = {
        "AZURE_TENANT_ID": tenant_id,
        "AZURE_CLIENT_ID": client_id,
        "AZURE_CLIENT_SECRET": client_secret,
    }
    present_keys = [key for key, value in configured.items() if value]
    if not present_keys:
        return credentials
    if len(present_keys) != len(configured):
        raise ValueError(
            "Azure Entra client credentials require AZURE_TENANT_ID, "
            "AZURE_CLIENT_ID, and AZURE_CLIENT_SECRET."
        )
    assert tenant_id is not None
    assert client_id is not None
    assert client_secret is not None

    credential = ClientSecretCredential(
        tenant_id=tenant_id,
        client_id=client_id,
        client_secret=client_secret,
    )
    try:
        token = await credential.get_token(_AZURE_COGNITIVE_SCOPE)
    except Exception as exc:
        raise ValueError(
            "Failed to acquire Azure Entra token from client credentials."
        ) from exc
    finally:
        with contextlib.suppress(Exception):
            await credential.close()

    augmented = credentials.copy()
    augmented["AZURE_AD_TOKEN"] = token.token
    return augmented


class AgentManagementService(BaseOrgService):
    """Service for managing agent configuration at the organization level."""

    service_name = "agent-management"

    def __init__(self, session: AsyncSession, role: Role | None = None):
        super().__init__(session, role=role)
        self.secrets_service = SecretsService(session, role=role)
        self.settings_service = SettingsService(session, role=role)
        try:
            # This is a workspace scoped service
            self.presets = AgentPresetService(
                session=self.session,
                role=self.role,
            )
        except TracecatAuthorizationError:
            self.presets = None

    def _get_credential_secret_name(self, provider: str) -> str:
        """Get the standardized secret name for a provider's credentials."""
        return f"agent-{provider}-credentials"

    def _get_workspace_credential_secret_name(self, provider: str) -> str:
        """Get the workspace secret name for a provider's credentials.

        Maps agent provider names to workspace secret names used by the registry.
        """
        provider_to_secret = {
            "bedrock": "amazon_bedrock",
        }
        return provider_to_secret.get(provider, provider)

    async def _stage_org_setting_value(self, *, key: str, value: str) -> None:
        """Stage a create-or-update for an org setting without committing.

        The caller is responsible for the ``session.commit()`` that flushes
        all staged changes atomically.
        """
        setting = await self.settings_service.get_org_setting(key)
        if setting:
            await self.settings_service._update_setting(
                setting, SettingUpdate(value=value)
            )
            return

        logger.warning("Organization setting not found, creating it", key=key)
        await self.settings_service._create_org_setting(
            SettingCreate(
                key=key,
                value=value,
                value_type=ValueType.JSON,
                is_sensitive=False,
            )
        )

    async def _set_org_setting_value(self, *, key: str, value: str) -> None:
        """Create or update an organization setting with a JSON string value."""
        await self._stage_org_setting_value(key=key, value=value)
        await self.session.commit()

    async def _get_default_model_name_setting(self) -> str | None:
        """Return the stored legacy default model name, if present."""
        setting = await self.settings_service.get_org_setting(
            _DEFAULT_MODEL_SETTING_KEY
        )
        if not setting:
            return None
        value = self.settings_service.get_value(setting)
        return value if isinstance(value, str) and value else None

    async def _get_default_model_catalog_id_setting(self) -> uuid.UUID | None:
        """Return the stored canonical default model catalog id, if present."""
        setting = await self.settings_service.get_org_setting(
            _DEFAULT_MODEL_CATALOG_ID_SETTING_KEY
        )
        if not setting:
            return None

        value = self.settings_service.get_value(setting)
        if not isinstance(value, str) or not value:
            return None
        try:
            return uuid.UUID(value)
        except ValueError:
            logger.warning("Invalid default model catalog id setting", value=value)
            return None

    def _resolve_legacy_default_model_entry(
        self,
        enabled_models: list[AgentCatalogRead],
        *,
        model_name: str,
    ) -> AgentCatalogRead | None:
        """Resolve a legacy name-only default model selection."""
        matches = [entry for entry in enabled_models if entry.model_name == model_name]
        if not matches:
            return None
        if len(matches) == 1:
            return matches[0]

        builtin_matches = [
            entry for entry in matches if entry.custom_provider_id is None
        ]
        if len(builtin_matches) == 1:
            return builtin_matches[0]
        return None

    def _to_default_model_selection(
        self, catalog_entry: AgentCatalogRead
    ) -> DefaultModelSelection:
        """Project a catalog row into the public default-model schema."""
        return DefaultModelSelection(
            catalog_id=catalog_entry.id,
            model_name=catalog_entry.model_name,
            model_provider=catalog_entry.model_provider,
            custom_provider_id=catalog_entry.custom_provider_id,
        )

    async def list_providers(self) -> list[str]:
        """List all available AI model providers."""
        return sorted({c.provider for c in MODEL_CONFIGS.values()})

    async def list_models(self) -> dict[str, ModelConfig]:
        """List all available AI models."""
        return MODEL_CONFIGS

    async def get_model_config(self, model_name: str) -> ModelConfig:
        """Get configuration for a specific model."""
        if model_name not in MODEL_CONFIGS:
            raise TracecatNotFoundError(f"Model {model_name} not found")
        return MODEL_CONFIGS[model_name]

    async def list_provider_credential_configs(
        self,
    ) -> list[ProviderCredentialConfig]:
        """List all provider credential configurations."""
        return list(PROVIDER_CREDENTIAL_CONFIGS.values())

    async def get_provider_credential_config(
        self, provider: str
    ) -> ProviderCredentialConfig:
        """Get credential configuration for a specific provider."""
        if provider not in PROVIDER_CREDENTIAL_CONFIGS:
            raise TracecatNotFoundError(f"Provider {provider} not found")
        return PROVIDER_CREDENTIAL_CONFIGS[provider]

    @require_scope("agent:update")
    async def create_provider_credentials(
        self, params: ModelCredentialCreate
    ) -> OrganizationSecret:
        """Create or update credentials for an AI provider."""
        secret_name = self._get_credential_secret_name(params.provider)

        # Check if credentials already exist
        try:
            existing = await self.secrets_service.get_org_secret_by_name(secret_name)
            # Update existing credentials
            keys = [
                SecretKeyValue(key=k, value=SecretStr(v))
                for k, v in params.credentials.items()
            ]
            update_params = SecretUpdate(keys=keys)
            await self.secrets_service.update_org_secret(existing, update_params)
            await self._auto_grant_provider_access(params.provider)
            return existing
        except TracecatNotFoundError:
            # Create new credentials
            keys = [
                SecretKeyValue(key=k, value=SecretStr(v))
                for k, v in params.credentials.items()
            ]
            create_params = SecretCreate(
                name=secret_name,
                type=SecretType.CUSTOM,
                description=f"Credentials for {params.provider} AI provider",
                keys=keys,
                tags={"provider": params.provider, "type": "agent-credentials"},
            )
            await self.secrets_service.create_org_secret(create_params)
            await self._auto_grant_provider_access(params.provider)
            return await self.secrets_service.get_org_secret_by_name(secret_name)

    @require_scope("agent:update")
    async def update_provider_credentials(
        self, provider: str, params: ModelCredentialUpdate
    ) -> OrganizationSecret:
        """Update existing credentials for an AI provider."""
        secret_name = self._get_credential_secret_name(provider)
        secret = await self.secrets_service.get_org_secret_by_name(secret_name)

        keys = [
            SecretKeyValue(key=k, value=SecretStr(v))
            for k, v in params.credentials.items()
        ]
        update_params = SecretUpdate(keys=keys)
        await self.secrets_service.update_org_secret(secret, update_params)
        return secret

    @require_scope("agent:read")
    async def get_provider_credentials(self, provider: str) -> dict[str, str] | None:
        """Get decrypted credentials for an AI provider at organization level."""
        secret_name = self._get_credential_secret_name(provider)
        try:
            secret = await self.secrets_service.get_org_secret_by_name(secret_name)
            decrypted_keys = self.secrets_service.decrypt_keys(secret.encrypted_keys)
            return {kv.key: kv.value.get_secret_value() for kv in decrypted_keys}
        except TracecatNotFoundError:
            return None

    async def _augment_runtime_provider_credentials(
        self, provider: str, credentials: dict[str, str]
    ) -> dict[str, str]:
        """Augment provider credentials with runtime-only values when required."""
        match provider:
            case "bedrock" if (
                "AWS_ROLE_ARN" in credentials
                and self.role.workspace_id is not None
                and _AWS_ASSUME_ROLE_EXTERNAL_ID_SECRET_KEY not in credentials
            ):
                runtime_credentials = credentials.copy()
                runtime_credentials[_AWS_ASSUME_ROLE_EXTERNAL_ID_SECRET_KEY] = (
                    build_workspace_external_id(self.role.workspace_id)
                )
                return runtime_credentials
            case "vertex_ai" if "GOOGLE_API_CREDENTIALS" in credentials:
                return await _resolve_vertex_bearer_token(credentials)
            case "azure_openai" | "azure_ai" if not credentials.get(
                "AZURE_API_KEY"
            ) and not credentials.get("AZURE_AD_TOKEN"):
                return await _resolve_azure_ad_token(credentials)
            case _:
                return credentials

    async def get_runtime_provider_credentials(
        self, provider: str
    ) -> dict[str, str] | None:
        """Get org-scoped provider credentials augmented for runtime consumers."""
        credentials = await self.get_provider_credentials(provider)
        if credentials is None:
            return None
        return await self._augment_runtime_provider_credentials(provider, credentials)

    def _catalog_target_credentials(self, row: AgentCatalog) -> dict[str, str]:
        """Project cloud catalog target metadata into runtime credential keys."""
        target_keys = _CLOUD_PROVIDER_TARGET_KEYS.get(row.model_provider)
        if not target_keys:
            return {}
        metadata = row.model_metadata if isinstance(row.model_metadata, dict) else {}
        credentials: dict[str, str] = {}
        for metadata_key, credential_key in target_keys:
            value = metadata.get(metadata_key)
            if isinstance(value, str) and value:
                credentials[credential_key] = value
        return credentials

    def _catalog_encrypted_target_credentials(
        self,
        row: AgentCatalog,
    ) -> dict[str, str]:
        """Decrypt migrated catalog config for cloud target keys only."""
        target_keys = _CLOUD_PROVIDER_TARGET_KEYS.get(row.model_provider)
        if not target_keys or row.encrypted_config is None:
            return {}
        allowed_keys = {credential_key for _, credential_key in target_keys}
        try:
            decrypted = decrypt_keyvalues(
                row.encrypted_config,
                key=get_db_encryption_key(),
            )
        except Exception:
            logger.exception(
                "Failed to decrypt catalog target fallback",
                catalog_id=str(row.id),
                model_provider=row.model_provider,
            )
            return {}
        return {
            kv.key: kv.value.get_secret_value()
            for kv in decrypted
            if kv.key in allowed_keys and kv.value.get_secret_value()
        }

    async def _get_cloud_catalog_credentials(
        self,
        row: AgentCatalog,
    ) -> dict[str, str] | None:
        """Merge live provider secrets with catalog target metadata."""
        credentials = await self.get_provider_credentials(row.model_provider)
        if credentials is None:
            return None

        target_credentials = self._catalog_target_credentials(row)
        if not target_credentials:
            target_credentials = self._catalog_encrypted_target_credentials(row)
        runtime_credentials = credentials | target_credentials
        return await self._augment_runtime_provider_credentials(
            row.model_provider,
            runtime_credentials,
        )

    async def get_catalog_credentials(
        self, catalog_id: uuid.UUID
    ) -> dict[str, str] | None:
        """Load credentials for a catalog row, augmented for runtime consumers.

        Dispatches on the row shape:

        - **Custom-model-provider rows** (``custom_provider_id`` set): read
          from the linked ``AgentCustomProvider`` row. Plaintext columns
          (``base_url``, ``passthrough``) are the source of truth; the
          row's ``encrypted_config`` carries the API key. The v2 backfill
          migration also writes a legacy-format copy of the blob onto the
          catalog row itself, but we intentionally ignore that here so
          post-CRUD rotations aren't shadowed by stale migration state.

        - **Cloud rows** (Bedrock / Azure / Vertex): use the live
          ``agent-{provider}-credentials`` secret as the credential source of
          truth. Catalog metadata supplies invocation target keys; migrated
          ``encrypted_config`` is only a fallback for those target keys.

        - **Platform rows** or any row without ``encrypted_config`` fall
          back to ``get_runtime_provider_credentials(provider)`` so direct
          providers (OpenAI / Anthropic / Gemini) keep working.
        """
        access_svc = AgentModelAccessService(session=self.session, role=self.role)
        if not await access_svc.is_catalog_enabled(
            catalog_id,
            workspace_id=self.role.workspace_id,
        ):
            return None

        stmt = select(AgentCatalog).where(
            AgentCatalog.id == catalog_id,
            sa.or_(
                AgentCatalog.organization_id.is_(None),
                AgentCatalog.organization_id == self.organization_id,
            ),
        )
        row = (await self.session.execute(stmt)).scalar_one_or_none()
        if row is None:
            return None

        if row.custom_provider_id is not None:
            provider_row = (
                await self.session.execute(
                    select(AgentCustomProvider).where(
                        AgentCustomProvider.id == row.custom_provider_id,
                        AgentCustomProvider.organization_id == self.organization_id,
                    )
                )
            ).scalar_one_or_none()
            if provider_row is None:
                return None

            credentials = _decrypt_custom_provider_config(provider_row)
            if provider_row.base_url:
                credentials["CUSTOM_MODEL_PROVIDER_BASE_URL"] = provider_row.base_url
            else:
                credentials.pop("CUSTOM_MODEL_PROVIDER_BASE_URL", None)
            credentials["CUSTOM_MODEL_PROVIDER_PASSTHROUGH"] = (
                "true" if provider_row.passthrough else "false"
            )
            return credentials or None

        if row.model_provider in _CLOUD_PROVIDER_TARGET_KEYS:
            return await self._get_cloud_catalog_credentials(row)

        if row.encrypted_config is None:
            # Direct-provider platform row — delegate to legacy secret lookup.
            return await self.get_runtime_provider_credentials(row.model_provider)

        try:
            decrypted = decrypt_keyvalues(
                row.encrypted_config,
                key=get_db_encryption_key(),
            )
        except Exception:
            logger.exception(
                "Failed to decrypt catalog encrypted_config",
                catalog_id=str(row.id),
                model_provider=row.model_provider,
            )
            return None

        credentials = {kv.key: kv.value.get_secret_value() for kv in decrypted}
        return await self._augment_runtime_provider_credentials(
            row.model_provider, credentials
        )

    @staticmethod
    def _resolve_custom_provider_config(
        config: AgentConfig,
        credentials: dict[str, str],
    ) -> AgentConfig:
        """Populate derived runtime settings for the custom model provider."""
        if config.model_provider != "custom-model-provider":
            return config
        passthrough = credentials.get(
            "CUSTOM_MODEL_PROVIDER_PASSTHROUGH", ""
        ).lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        if not passthrough:
            return replace(config, passthrough=False)
        if not (base_url := credentials.get("CUSTOM_MODEL_PROVIDER_BASE_URL")):
            raise TracecatNotFoundError(
                "Custom model provider passthrough requires "
                "CUSTOM_MODEL_PROVIDER_BASE_URL in provider credentials."
            )
        updates: dict[str, str | bool] = {"base_url": base_url, "passthrough": True}
        if model_name := credentials.get("CUSTOM_MODEL_PROVIDER_MODEL_NAME"):
            updates["model_name"] = model_name
        return replace(config, **updates)

    @require_scope("agent:update")
    async def delete_provider_credentials(self, provider: str) -> None:
        """Delete credentials for an AI provider."""
        secret_name = self._get_credential_secret_name(provider)
        try:
            secret = await self.secrets_service.get_org_secret_by_name(secret_name)
            await self.secrets_service.delete_org_secret(secret)
            await self._auto_revoke_provider_access(provider)
        except TracecatNotFoundError:
            logger.warning(
                "Attempted to delete non-existent credentials",
                provider=provider,
                secret_name=secret_name,
            )

    async def _auto_grant_provider_access(self, provider: str) -> None:
        """Grant org-wide ``AgentModelAccess`` for every catalog row that
        matches ``provider``.

        Configuring credentials for a provider implicitly enables every
        catalog row that provider exposes. This runs unconditionally —
        entitled orgs can then toggle individual models off, while
        un-entitled orgs get a working default without the per-model
        picker.

        Idempotent via the unique index with ``NULLS NOT DISTINCT`` on
        ``(organization_id, workspace_id, catalog_id)``.
        """
        org_id = self.role.organization_id
        if org_id is None:
            return

        catalog_ids = (
            (
                await self.session.execute(
                    sa.select(AgentCatalog.id).where(
                        AgentCatalog.model_provider == provider,
                        sa.or_(
                            AgentCatalog.organization_id.is_(None),
                            AgentCatalog.organization_id == org_id,
                        ),
                    )
                )
            )
            .scalars()
            .all()
        )
        if not catalog_ids:
            return

        now = datetime.now(UTC)
        values = [
            {
                "id": uuid.uuid4(),
                "organization_id": org_id,
                "workspace_id": None,
                "catalog_id": catalog_id,
                "created_at": now,
                "updated_at": now,
            }
            for catalog_id in catalog_ids
        ]
        stmt = (
            pg_insert(AgentModelAccess)
            .values(values)
            .on_conflict_do_nothing(
                index_elements=["organization_id", "workspace_id", "catalog_id"],
            )
        )
        await self.session.execute(stmt)
        await self.session.commit()

    async def _auto_revoke_provider_access(self, provider: str) -> None:
        """Delete org-wide ``AgentModelAccess`` rows for every catalog entry
        belonging to ``provider`` for this org.

        The symmetric counterpart of :meth:`_auto_grant_provider_access`:
        removing provider credentials revokes access to every model that
        provider exposed. Workspace-scoped access rows (``workspace_id``
        non-null) are intentionally left alone — they represent explicit
        subset overrides that the admin may want to keep for when creds are
        reattached.
        """
        org_id = self.role.organization_id
        if org_id is None:
            return

        catalog_id_select = sa.select(AgentCatalog.id).where(
            AgentCatalog.model_provider == provider,
            sa.or_(
                AgentCatalog.organization_id.is_(None),
                AgentCatalog.organization_id == org_id,
            ),
        )
        await self.session.execute(
            sa.delete(AgentModelAccess).where(
                AgentModelAccess.organization_id == org_id,
                AgentModelAccess.workspace_id.is_(None),
                AgentModelAccess.catalog_id.in_(catalog_id_select),
            )
        )
        await self.session.commit()

    async def check_provider_credentials(self, provider: str) -> bool:
        """Check if credentials exist for a provider at organization level.

        For the ``custom-model-provider`` slug, v2 moves the configuration
        out of the legacy ``agent-custom-model-provider-credentials`` secret
        into the ``AgentCustomProvider`` table. Check both so orgs that
        only have the v2 row still show as "configured".

        Uses a direct DB query to avoid requiring org:secret:read scope,
        since this is an internal check gated by agent:read at the router level.
        """
        if provider == "custom-model-provider":
            v2_result = await self.session.execute(
                select(AgentCustomProvider.id)
                .where(AgentCustomProvider.organization_id == self.organization_id)
                .limit(1)
            )
            if v2_result.first() is not None:
                return True

        secret_name = self._get_credential_secret_name(provider)
        result = await self.session.execute(
            select(OrganizationSecret.id).where(
                OrganizationSecret.organization_id == self.organization_id,
                OrganizationSecret.name == secret_name,
                OrganizationSecret.environment == DEFAULT_SECRETS_ENVIRONMENT,
            )
        )
        return result.scalar_one_or_none() is not None

    async def get_providers_status(self) -> dict[str, bool]:
        """Get credential status for all providers at organization level."""
        providers = await self.list_providers()
        status = {}
        for provider in providers:
            status[provider] = await self.check_provider_credentials(provider)
        return status

    async def check_workspace_provider_credentials(self, provider: str) -> bool:
        """Check if credentials exist for a provider at workspace level.

        Uses a direct DB query to avoid requiring secret:read scope,
        since this is an internal check gated by agent:read at the router level.
        """
        secret_name = self._get_workspace_credential_secret_name(provider)
        workspace_id = self.role.workspace_id
        if workspace_id is None:
            return False
        result = await self.session.execute(
            select(Secret.id).where(
                Secret.workspace_id == workspace_id,
                Secret.name == secret_name,
                Secret.environment == DEFAULT_SECRETS_ENVIRONMENT,
            )
        )
        return result.scalar_one_or_none() is not None

    async def get_workspace_providers_status(self) -> dict[str, bool]:
        """Get credential status for all providers at workspace level."""
        providers = await self.list_providers()
        status = {}
        for provider in providers:
            status[provider] = await self.check_workspace_provider_credentials(provider)
        return status

    @require_scope("agent:update")
    async def set_default_model(self, catalog_id: uuid.UUID) -> DefaultModelSelection:
        """Set the organization's default AI model by catalog id.

        Model names aren't unique across providers (e.g. ``gpt-4o`` on
        both ``openai`` and ``azure_openai``), so the default is keyed by
        catalog row id. The legacy ``agent_default_model`` name setting
        is written alongside the canonical catalog-id setting for
        backwards compatibility with readers that haven't migrated.
        """
        access_svc = AgentModelAccessService(session=self.session, role=self.role)
        enabled_models = await access_svc.get_org_models()
        catalog_entry = next(
            (entry for entry in enabled_models if entry.id == catalog_id),
            None,
        )
        if catalog_entry is None:
            raise TracecatNotFoundError(
                f"Catalog row {catalog_id!s} is not enabled for this organization"
            )

        await self._stage_org_setting_value(
            key=_DEFAULT_MODEL_SETTING_KEY,
            value=catalog_entry.model_name,
        )
        await self._stage_org_setting_value(
            key=_DEFAULT_MODEL_CATALOG_ID_SETTING_KEY,
            value=str(catalog_entry.id),
        )
        await self.session.commit()
        return self._to_default_model_selection(catalog_entry)

    async def get_default_model(self) -> str | None:
        """Get the organization's default AI model."""
        setting = await self.settings_service.get_org_setting("agent_default_model")
        if setting:
            return self.settings_service.get_value(setting)
        return None

    async def get_default_model_selection(self) -> DefaultModelSelection | None:
        """Get the canonical default model selection, if it resolves cleanly."""
        access_svc = AgentModelAccessService(session=self.session, role=self.role)
        enabled_models = await access_svc.get_org_models()

        if catalog_id := await self._get_default_model_catalog_id_setting():
            if catalog_entry := next(
                (entry for entry in enabled_models if entry.id == catalog_id),
                None,
            ):
                return self._to_default_model_selection(catalog_entry)
            return None

        if model_name := await self._get_default_model_name_setting():
            if catalog_entry := self._resolve_legacy_default_model_entry(
                enabled_models,
                model_name=model_name,
            ):
                return self._to_default_model_selection(catalog_entry)
        return None

    @contextlib.contextmanager
    def _credentials_sandbox(self, credentials: dict[str, str]) -> Iterator[None]:
        """Expose provider credentials to both Tracecat and registry contexts."""
        secrets_token = registry_secrets.set_context(credentials)
        try:
            with secrets_manager.env_sandbox(credentials):
                yield
        finally:
            registry_secrets.reset_context(secrets_token)

    @contextlib.asynccontextmanager
    async def with_model_config(self) -> AsyncIterator[ModelConfig]:
        """Yield the org's default model + install its credentials.

        Prefers the v2 catalog-backed path: if ``agent_default_model_catalog_id``
        is set, loads the catalog row and decrypts ``encrypted_config`` (for
        cloud + custom rows) or falls back to ``agent-{provider}-credentials``
        (direct-provider platform rows). If only the legacy
        ``agent_default_model`` name is set, matches it against the org's
        enabled catalog rows.

        The returned ``ModelConfig`` carries ``catalog_id`` so downstream
        ``AgentConfig`` constructors can thread it into the LLM token.
        """
        catalog_id = await self._get_default_model_catalog_id_setting()
        legacy_name: str | None = None
        catalog_entry: AgentCatalogRead | None = None

        if catalog_id is None:
            legacy_name = await self._get_default_model_name_setting()
            if not legacy_name:
                raise TracecatNotFoundError("No default model set")

            access_svc = AgentModelAccessService(session=self.session, role=self.role)
            enabled_models = await access_svc.get_org_models()
            catalog_entry = self._resolve_legacy_default_model_entry(
                enabled_models, model_name=legacy_name
            )
            if catalog_entry is None:
                raise TracecatNotFoundError(
                    f"Default model {legacy_name!r} is not enabled for this "
                    "organization"
                )
            catalog_id = catalog_entry.id

        model_config = ModelConfig.model_construct(
            name=catalog_entry.model_name if catalog_entry else "",
            provider=catalog_entry.model_provider if catalog_entry else "",
            catalog_id=catalog_id,
        )
        logger.info(
            "Loading secrets for model",
            catalog_id=str(catalog_id),
            model=model_config.name or legacy_name,
        )

        credentials = await self.get_catalog_credentials(catalog_id)
        if not credentials:
            raise TracecatNotFoundError(
                "No organization credentials found for the default model. "
                "Configure credentials in organization settings first."
            )

        # Cloud catalog rows store the invocation target inside the encrypted
        # blob (same shape as the legacy org-secret). Extract it so the
        # returned ``ModelConfig.name`` matches what pydantic-ai /
        # Temporal callers expect: the string sent to the provider.
        provider = model_config.provider
        if not provider:
            # Re-fetch the catalog row when we didn't have a pydantic
            # projection in hand (legacy-name path filled catalog_entry above).
            stmt = select(AgentCatalog).where(AgentCatalog.id == catalog_id)
            row = (await self.session.execute(stmt)).scalar_one()
            provider = row.model_provider
            model_config = model_config.model_copy(
                update={"provider": provider, "name": row.model_name}
            )

        if provider == "bedrock":
            target = credentials.get("AWS_INFERENCE_PROFILE_ID") or credentials.get(
                "AWS_MODEL_ID"
            )
            if not target:
                raise TracecatNotFoundError(
                    "No Bedrock invocation target configured on catalog row "
                    f"{catalog_id}. Add AWS_INFERENCE_PROFILE_ID or "
                    "AWS_MODEL_ID via organization settings."
                )
            model_config = model_config.model_copy(update={"name": target})
        elif provider == "azure_openai":
            deployment = credentials.get("AZURE_DEPLOYMENT_NAME")
            if not deployment:
                raise TracecatNotFoundError(
                    f"No Azure OpenAI deployment configured on catalog row "
                    f"{catalog_id}."
                )
            model_config = model_config.model_copy(update={"name": deployment})
        elif provider == "azure_ai":
            azure_model = credentials.get("AZURE_AI_MODEL_NAME")
            if not azure_model:
                raise TracecatNotFoundError(
                    f"No Azure AI model configured on catalog row {catalog_id}."
                )
            model_config = model_config.model_copy(update={"name": azure_model})
        elif provider == "vertex_ai":
            vertex_model = credentials.get("VERTEX_AI_MODEL")
            if not vertex_model:
                raise TracecatNotFoundError(
                    f"No Vertex AI model configured on catalog row {catalog_id}."
                )
            model_config = model_config.model_copy(update={"name": vertex_model})

        # Expose credentials in both env and registry secrets context so
        # legacy pydantic-ai consumers (auto-title, ranker) pick them up
        # through ``registry_secrets`` / env vars unchanged.
        with self._credentials_sandbox(credentials):
            yield model_config

    @contextlib.asynccontextmanager
    async def with_preset_config(
        self,
        *,
        preset_id: uuid.UUID | None = None,
        slug: str | None = None,
        preset_version_id: uuid.UUID | None = None,
        preset_version: int | None = None,
    ) -> AsyncIterator[AgentConfig]:
        """Yield an agent preset configuration with provider credentials loaded.

        Prefers the v2 catalog-backed path via ``preset_config.catalog_id``;
        falls back to the legacy ``agent-{provider}-credentials`` secret when
        the preset hasn't been linked to a catalog row yet (e.g. pre-migration
        rows whose ``catalog_id`` backfill failed).

        Args:
            preset_id: Agent preset ID to load
            slug: Agent preset slug to load (alternative to preset_id)
            preset_version_id: Optional preset version ID to pin
            preset_version: Optional preset version number to pin
        """
        if self.presets is None:
            raise TracecatAuthorizationError(
                "Agent presets require a workspace role",
            )

        preset_config = await self.presets.resolve_agent_preset_config(
            preset_id=preset_id,
            slug=slug,
            preset_version_id=preset_version_id,
            preset_version=preset_version,
        )

        if preset_config.catalog_id is not None:
            credentials = await self.get_catalog_credentials(preset_config.catalog_id)
            logger.info(
                "with_preset_config: catalog path",
                catalog_id=str(preset_config.catalog_id),
                model_provider=preset_config.model_provider,
                resolved=bool(credentials),
                cred_keys=sorted(credentials.keys()) if credentials else None,
            )
        else:
            credentials = await self.get_runtime_provider_credentials(
                preset_config.model_provider,
            )
            logger.info(
                "with_preset_config: legacy path (no catalog_id on preset)",
                model_provider=preset_config.model_provider,
                resolved=bool(credentials),
            )

        if not credentials:
            raise TracecatNotFoundError(
                f"No credentials found for provider '{preset_config.model_provider}'. "
                "Please configure credentials for this provider first."
            )

        preset_config = self._resolve_custom_provider_config(
            preset_config,
            credentials,
        )

        with self._credentials_sandbox(credentials):
            yield preset_config
