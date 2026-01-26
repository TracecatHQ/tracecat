from __future__ import annotations

import contextlib
import uuid
from collections.abc import AsyncIterator

from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.agent.config import MODEL_CONFIGS, PROVIDER_CREDENTIAL_CONFIGS
from tracecat.agent.preset.service import AgentPresetService
from tracecat.agent.schemas import (
    ModelConfig,
    ModelCredentialCreate,
    ModelCredentialUpdate,
    ProviderCredentialConfig,
)
from tracecat.agent.types import AgentConfig
from tracecat.auth.types import Role
from tracecat.db.models import OrganizationSecret
from tracecat.exceptions import TracecatAuthorizationError, TracecatNotFoundError
from tracecat.logger import logger
from tracecat.secrets import secrets_manager
from tracecat.secrets.enums import SecretType
from tracecat.secrets.schemas import SecretCreate, SecretKeyValue, SecretUpdate
from tracecat.secrets.service import SecretsService
from tracecat.service import BaseOrgService
from tracecat.settings.schemas import SettingCreate, SettingUpdate, ValueType
from tracecat.settings.service import SettingsService


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
        """Get the workspace secret name for a provider's credentials."""
        return provider

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
        """Get decrypted credentials for an AI provider at organization level."""
        secret_name = self._get_credential_secret_name(provider)
        try:
            secret = await self.secrets_service.get_org_secret_by_name(secret_name)
            decrypted_keys = self.secrets_service.decrypt_keys(secret.encrypted_keys)
            return {kv.key: kv.value.get_secret_value() for kv in decrypted_keys}
        except TracecatNotFoundError:
            return None

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
        """Check if credentials exist for a provider at organization level."""
        secret_name = self._get_credential_secret_name(provider)
        try:
            await self.secrets_service.get_org_secret_by_name(secret_name)
            return True
        except TracecatNotFoundError:
            return False

    async def get_providers_status(self) -> dict[str, bool]:
        """Get credential status for all providers at organization level."""
        providers = await self.list_providers()
        status = {}
        for provider in providers:
            status[provider] = await self.check_provider_credentials(provider)
        return status

    async def check_workspace_provider_credentials(self, provider: str) -> bool:
        """Check if credentials exist for a provider at workspace level."""
        secret_name = self._get_workspace_credential_secret_name(provider)
        try:
            await self.secrets_service.get_secret_by_name(secret_name)
            return True
        except TracecatNotFoundError:
            return False

    async def get_workspace_providers_status(self) -> dict[str, bool]:
        """Get credential status for all providers at workspace level."""
        providers = await self.list_providers()
        status = {}
        for provider in providers:
            status[provider] = await self.check_workspace_provider_credentials(provider)
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
        if use_workspace_credentials:
            credentials = await self.get_workspace_provider_credentials(provider)
        else:
            credentials = await self.get_provider_credentials(provider)

        if not credentials:
            scope = "workspace" if use_workspace_credentials else "organization"
            raise TracecatNotFoundError(
                f"No credentials found for provider '{provider}' at {scope} level. "
                f"Please configure credentials for this provider first."
            )

        # For Bedrock, the model name must be the ARN from credentials
        if provider == "bedrock":
            arn = credentials.get("AWS_MODEL_ARN")
            if not arn:
                raise TracecatNotFoundError(
                    "AWS_MODEL_ARN not found in Bedrock credentials. "
                    "Please configure the Model ARN in your Bedrock credentials."
                )
            model_config = model_config.model_copy(update={"name": arn})

        # Use the credentials directly in the environment sandbox
        with secrets_manager.env_sandbox(credentials):
            yield model_config

    @contextlib.asynccontextmanager
    async def with_preset_config(
        self,
        *,
        preset_id: uuid.UUID | None = None,
        slug: str | None = None,
        use_workspace_credentials: bool = True,
    ) -> AsyncIterator[AgentConfig]:
        """Yield an agent preset configuration with provider credentials loaded.

        Args:
            preset_id: Agent preset ID to load
            slug: Agent preset slug to load (alternative to preset_id)
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
        )

        # Get credentials from appropriate scope
        if use_workspace_credentials:
            credentials = await self.get_workspace_provider_credentials(
                preset_config.model_provider
            )
        else:
            credentials = await self.get_provider_credentials(
                preset_config.model_provider
            )

        if not credentials:
            raise TracecatNotFoundError(
                f"No credentials found for provider '{preset_config.model_provider}'. "
                "Please configure credentials for this provider first."
            )

        with secrets_manager.env_sandbox(credentials):
            yield preset_config
