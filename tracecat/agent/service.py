from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator

from pydantic import SecretStr
from sqlmodel.ext.asyncio.session import AsyncSession

from tracecat.agent.config import MODEL_CONFIGS, PROVIDER_CREDENTIAL_CONFIGS
from tracecat.agent.models import (
    ModelConfig,
    ModelCredentialCreate,
    ModelCredentialUpdate,
    ProviderCredentialConfig,
)
from tracecat.db.schemas import OrganizationSecret
from tracecat.logger import logger
from tracecat.secrets import secrets_manager
from tracecat.secrets.enums import SecretType
from tracecat.secrets.models import SecretCreate, SecretKeyValue, SecretUpdate
from tracecat.secrets.service import SecretsService
from tracecat.service import BaseService
from tracecat.settings.models import SettingCreate, SettingUpdate, ValueType
from tracecat.settings.service import SettingsService
from tracecat.types.auth import Role
from tracecat.types.exceptions import TracecatNotFoundError


class AgentManagementService(BaseService):
    """Service for managing agent configuration at the organization level."""

    service_name = "agent-management"

    def __init__(self, session: AsyncSession, role: Role | None = None):
        super().__init__(session, role=role)
        self.secrets_service = SecretsService(session, role=role)
        self.settings_service = SettingsService(session, role=role)

    def _get_credential_secret_name(self, provider: str) -> str:
        """Get the standardized secret name for a provider's credentials."""
        return f"agent-{provider}-credentials"

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

    async def get_provider_credentials(self, provider: str) -> dict[str, str] | None:
        """Get decrypted credentials for an AI provider."""
        secret_name = self._get_credential_secret_name(provider)
        try:
            secret = await self.secrets_service.get_org_secret_by_name(secret_name)
            decrypted_keys = self.secrets_service.decrypt_keys(secret.encrypted_keys)
            return {kv.key: kv.value.get_secret_value() for kv in decrypted_keys}
        except TracecatNotFoundError:
            return None

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
        """Check if credentials exist for a provider."""
        credentials = await self.get_provider_credentials(provider)
        return credentials is not None

    async def get_providers_status(self) -> dict[str, bool]:
        """Get credential status for all providers."""
        providers = await self.list_providers()
        status = {}
        for provider in providers:
            status[provider] = await self.check_provider_credentials(provider)
        return status

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

    async def get_default_model(self) -> str | None:
        """Get the organization's default AI model."""
        setting = await self.settings_service.get_org_setting("agent_default_model")
        if setting:
            return self.settings_service.get_value(setting)
        return None

    @contextlib.asynccontextmanager
    async def with_model_config(self) -> AsyncIterator[ModelConfig]:
        """Get the platform-specific secrets for the selected model."""
        # Get provider-specific secrets
        model_name = await self.get_default_model()
        if not model_name:
            raise TracecatNotFoundError("No default model set")
        model_config = MODEL_CONFIGS.get(model_name)
        if not model_config:
            raise TracecatNotFoundError(f"Model {model_name} not found")

        # Get organization credentials for the model's provider
        provider = model_config.provider
        logger.info("Loading secrets for model", provider=provider, model=model_name)

        # Fetch organization credentials for this provider
        credentials = await self.get_provider_credentials(provider)
        if not credentials:
            raise TracecatNotFoundError(
                f"No credentials found for provider '{provider}'. "
                f"Please configure credentials for this provider first."
            )

        # Use the credentials directly in the environment sandbox
        with secrets_manager.env_sandbox(credentials):
            yield model_config
