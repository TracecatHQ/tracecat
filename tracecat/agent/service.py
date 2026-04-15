from __future__ import annotations

import asyncio
import contextlib
import uuid
from collections.abc import AsyncIterator, Iterator
from dataclasses import replace

import orjson
from azure.identity.aio import ClientSecretCredential
from google.auth.transport.requests import Request as GoogleAuthRequest
from google.oauth2 import service_account
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tracecat_registry._internal import secrets as registry_secrets

from tracecat.agent.access.service import AgentModelAccessService
from tracecat.agent.catalog.schemas import AgentCatalogRead
from tracecat.agent.config import MODEL_CONFIGS, PROVIDER_CREDENTIAL_CONFIGS
from tracecat.agent.preset.service import AgentPresetService
from tracecat.agent.schemas import (
    DefaultModelSelection,
    DefaultModelSelectionUpdate,
    ModelConfig,
    ModelCredentialCreate,
    ModelCredentialUpdate,
    ProviderCredentialConfig,
)
from tracecat.agent.types import AgentConfig
from tracecat.auth.types import Role
from tracecat.authz.controls import require_scope
from tracecat.db.models import OrganizationSecret, Secret
from tracecat.exceptions import TracecatAuthorizationError, TracecatNotFoundError
from tracecat.integrations.aws_assume_role import build_workspace_external_id
from tracecat.logger import logger
from tracecat.secrets import secrets_manager
from tracecat.secrets.constants import DEFAULT_SECRETS_ENVIRONMENT
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

    async def _set_org_setting_value(self, *, key: str, value: str) -> None:
        """Create or update an organization setting with a JSON string value."""
        setting = await self.settings_service.get_org_setting(key)
        if setting:
            await self.settings_service.update_org_setting(
                setting, SettingUpdate(value=value)
            )
            return

        logger.warning("Organization setting not found, creating it", key=key)
        await self.settings_service.create_org_setting(
            SettingCreate(
                key=key,
                value=value,
                value_type=ValueType.JSON,
                is_sensitive=False,
            )
        )

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

    @require_scope("agent:read")
    async def get_workspace_provider_credentials(
        self, provider: str
    ) -> dict[str, str] | None:
        """Get credentials for an AI provider at workspace level."""
        secret_name = self._get_workspace_credential_secret_name(provider)
        try:
            secret = await self.secrets_service.get_secret_by_name(secret_name)
            secret_keys = self.secrets_service.decrypt_keys(secret.encrypted_keys)
            return {kv.key: kv.value.get_secret_value() for kv in secret_keys}
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
        self, provider: str, *, use_workspace_credentials: bool = True
    ) -> dict[str, str] | None:
        """Get provider credentials augmented for runtime consumers."""
        if use_workspace_credentials:
            credentials = await self.get_workspace_provider_credentials(provider)
        else:
            credentials = await self.get_provider_credentials(provider)

        if credentials is None:
            return None
        return await self._augment_runtime_provider_credentials(provider, credentials)

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
        except TracecatNotFoundError:
            logger.warning(
                "Attempted to delete non-existent credentials",
                provider=provider,
                secret_name=secret_name,
            )

    async def check_provider_credentials(self, provider: str) -> bool:
        """Check if credentials exist for a provider at organization level.

        Uses a direct DB query to avoid requiring org:secret:read scope,
        since this is an internal check gated by agent:read at the router level.
        """
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
    async def set_default_model(self, model_name: str) -> None:
        """Set the organization's default AI model."""
        # Validate model exists
        if model_name not in MODEL_CONFIGS:
            raise TracecatNotFoundError(f"Model {model_name} not found")

        # Update or create the setting
        setting = await self.settings_service.get_org_setting("agent_default_model")
        if setting:
            await self.settings_service.update_org_setting(
                setting, SettingUpdate(value=model_name)
            )
        else:
            # This shouldn't happen if settings are initialized properly,
            # but handle it gracefully
            logger.warning("Default model setting not found, creating it")
            await self.settings_service.create_org_setting(
                SettingCreate(
                    key="agent_default_model",
                    value=model_name,
                    value_type=ValueType.JSON,
                    is_sensitive=False,
                )
            )

    @require_scope("agent:update")
    async def set_default_model_selection(
        self,
        params: DefaultModelSelectionUpdate,
    ) -> DefaultModelSelection:
        """Set the canonical default model selection for the organization."""
        access_svc = AgentModelAccessService(session=self.session, role=self.role)
        enabled_models = await access_svc.get_org_models()
        catalog_entry = next(
            (entry for entry in enabled_models if entry.id == params.catalog_id),
            None,
        )
        if catalog_entry is None:
            raise TracecatNotFoundError(
                f"Catalog entry {params.catalog_id} is not enabled for this organization"
            )

        await self._set_org_setting_value(
            key=_DEFAULT_MODEL_SETTING_KEY,
            value=catalog_entry.model_name,
        )
        await self._set_org_setting_value(
            key=_DEFAULT_MODEL_CATALOG_ID_SETTING_KEY,
            value=str(catalog_entry.id),
        )
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
    async def with_model_config(
        self,
        *,
        use_workspace_credentials: bool = False,
    ) -> AsyncIterator[ModelConfig]:
        """Get the platform-specific secrets for the selected model.

        Args:
            use_workspace_credentials: If True, use workspace-scoped credentials.
                If False (default), use organization-scoped credentials.
        """
        # Get provider-specific secrets
        model_name = await self.get_default_model()
        if not model_name:
            raise TracecatNotFoundError("No default model set")
        model_config = MODEL_CONFIGS.get(model_name)
        if not model_config:
            raise TracecatNotFoundError(f"Model {model_name} not found")

        # Get credentials for the model's provider from appropriate scope
        provider = model_config.provider
        logger.info(
            "Loading secrets for model",
            provider=provider,
            model=model_name,
            use_workspace_credentials=use_workspace_credentials,
        )

        # Fetch credentials from appropriate scope
        credentials = await self.get_runtime_provider_credentials(
            provider, use_workspace_credentials=use_workspace_credentials
        )

        if not credentials:
            scope = "workspace" if use_workspace_credentials else "organization"
            raise TracecatNotFoundError(
                f"No credentials found for provider '{provider}' at {scope} level. "
                f"Please configure credentials for this provider first."
            )

        # For Bedrock, the model ID must come from credentials
        # Prefer inference profile ID (required for newer models like Claude 4)
        if provider == "bedrock":
            model_id = credentials.get("AWS_INFERENCE_PROFILE_ID") or credentials.get(
                "AWS_MODEL_ID"
            )
            if not model_id:
                raise TracecatNotFoundError(
                    "No Bedrock model configured. Please set either "
                    "AWS_INFERENCE_PROFILE_ID (for newer models like Claude 4) or "
                    "AWS_MODEL_ID (for legacy models) in your Bedrock credentials."
                )
            model_config = model_config.model_copy(update={"name": model_id})

        # For Azure OpenAI, the deployment name comes from credentials
        elif provider == "azure_openai":
            deployment = credentials.get("AZURE_DEPLOYMENT_NAME")
            if not deployment:
                raise TracecatNotFoundError(
                    "No Azure OpenAI deployment configured. Please set "
                    "AZURE_DEPLOYMENT_NAME in your Azure OpenAI credentials."
                )
            model_config = model_config.model_copy(update={"name": deployment})

        # For Azure AI, the model name comes from credentials
        elif provider == "azure_ai":
            model_name = credentials.get("AZURE_AI_MODEL_NAME")
            if not model_name:
                raise TracecatNotFoundError(
                    "No Azure AI model configured. Please set "
                    "AZURE_AI_MODEL_NAME in your Azure AI credentials."
                )
            model_config = model_config.model_copy(update={"name": model_name})

        # For Vertex AI, the model name comes from credentials
        elif provider == "vertex_ai":
            model_name = credentials.get("VERTEX_AI_MODEL")
            if not model_name:
                raise TracecatNotFoundError(
                    "No Vertex AI model configured. Please set "
                    "VERTEX_AI_MODEL in your Vertex AI credentials."
                )
            model_config = model_config.model_copy(update={"name": model_name})

        # Expose credentials in both env and registry secrets context.
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
        use_workspace_credentials: bool = True,
    ) -> AsyncIterator[AgentConfig]:
        """Yield an agent preset configuration with provider credentials loaded.

        Args:
            preset_id: Agent preset ID to load
            slug: Agent preset slug to load (alternative to preset_id)
            preset_version_id: Optional preset version ID to pin
            preset_version: Optional preset version number to pin
            use_workspace_credentials: If True (default), use workspace-scoped credentials.
                If False, use organization-scoped credentials.
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

        # Get credentials from appropriate scope
        credentials = await self.get_runtime_provider_credentials(
            preset_config.model_provider,
            use_workspace_credentials=use_workspace_credentials,
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
