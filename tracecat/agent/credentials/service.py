from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.agent.catalog.service import load_catalog_state
from tracecat.agent.config import PROVIDER_CREDENTIAL_CONFIGS
from tracecat.agent.provider_config import (
    BUILT_IN_PROVIDER_ORDER,
    credential_secret_name,
    ensure_builtin_provider,
    provider_base_url_key,
    provider_credentials_complete,
    provider_runtime_target,
)
from tracecat.agent.schemas import (
    BuiltInProviderRead,
    ModelCatalogEntry,
    ModelCredentialCreate,
    ModelCredentialUpdate,
    ProviderCredentialConfig,
)
from tracecat.agent.types import ModelDiscoveryStatus, ModelSourceType
from tracecat.auth.types import Role
from tracecat.authz.controls import require_scope
from tracecat.db.models import (
    AgentCatalog,
    AgentModelSelectionLink,
    OrganizationSecret,
)
from tracecat.exceptions import TracecatNotFoundError
from tracecat.logger import logger
from tracecat.secrets.constants import DEFAULT_SECRETS_ENVIRONMENT
from tracecat.secrets.enums import SecretType
from tracecat.secrets.schemas import SecretCreate, SecretKeyValue, SecretUpdate
from tracecat.secrets.service import SecretsService
from tracecat.service import BaseOrgService

BUILTIN_CATALOG_STATE_SETTINGS = {
    "discovery_status": "agent_builtin_catalog_discovery_status",
    "last_refreshed_at": "agent_builtin_catalog_last_refreshed_at",
    "last_error": "agent_builtin_catalog_last_error",
}


class AgentCredentialsService(BaseOrgService):
    """Service for provider credential configuration and status."""

    service_name = "agent-credentials"

    def __init__(self, session: AsyncSession, role: Role | None = None):
        super().__init__(session, role=role)
        self.secrets_service = SecretsService(session, role=role)

    @staticmethod
    def _credential_key_values(
        credentials: dict[str, str],
    ) -> list[SecretKeyValue]:
        return [
            SecretKeyValue(key=key, value=SecretStr(value))
            for key, value in credentials.items()
        ]

    def _build_provider_read(
        self,
        *,
        provider: str,
        discovery_status: ModelDiscoveryStatus,
        last_refreshed_at: datetime | None,
        last_error: str | None,
        credentials: dict[str, str] | None,
        rows: list[AgentCatalog],
        enabled_catalog_ids: set[uuid.UUID],
    ) -> BuiltInProviderRead:
        # These are derived presentation fields, but building them here keeps the
        # router thin and the admin response shape explicit.
        base_url = None
        if credentials and (base_url_key := provider_base_url_key(provider)):
            base_url = credentials.get(base_url_key)
        return BuiltInProviderRead(
            provider=provider,
            label=PROVIDER_CREDENTIAL_CONFIGS[provider].label,
            source_type=ModelSourceType(provider),
            credentials_configured=provider_credentials_complete(
                provider=provider,
                credentials=credentials,
            ),
            base_url=base_url,
            runtime_target=provider_runtime_target(
                provider=provider,
                credentials=credentials,
            ),
            discovery_status=discovery_status,
            last_refreshed_at=last_refreshed_at,
            last_error=last_error,
            discovered_models=[
                ModelCatalogEntry(
                    model_provider=row.model_provider,
                    model_name=row.model_name,
                    source_type=ModelSourceType(row.model_provider).value,
                    source_name=PROVIDER_CREDENTIAL_CONFIGS[row.model_provider].label,
                    source_id=None,
                    base_url=None,
                    enabled=row.id in enabled_catalog_ids,
                    last_refreshed_at=row.last_refreshed_at,
                    metadata=row.model_metadata,
                    enabled_config=None,
                )
                for row in rows
                if row.model_provider == provider
            ],
        )

    async def _load_provider_credentials(self, provider: str) -> dict[str, str] | None:
        ensure_builtin_provider(provider)
        secret_name = credential_secret_name(provider)
        stmt = select(OrganizationSecret).where(
            OrganizationSecret.organization_id == self.organization_id,
            OrganizationSecret.name == secret_name,
            OrganizationSecret.environment == DEFAULT_SECRETS_ENVIRONMENT,
        )
        secret = (await self.session.execute(stmt)).scalar_one_or_none()
        if secret is None:
            return None
        decrypted_keys = self.secrets_service.decrypt_keys(secret.encrypted_keys)
        return {kv.key: kv.value.get_secret_value() for kv in decrypted_keys}

    @require_scope("agent:read")
    async def get_provider_credentials(self, provider: str) -> dict[str, str] | None:
        return await self._load_provider_credentials(provider)

    async def check_provider_credentials(self, provider: str) -> bool:
        ensure_builtin_provider(provider)
        secret_name = credential_secret_name(provider)
        result = await self.session.execute(
            select(OrganizationSecret.id).where(
                OrganizationSecret.organization_id == self.organization_id,
                OrganizationSecret.name == secret_name,
                OrganizationSecret.environment == DEFAULT_SECRETS_ENVIRONMENT,
            )
        )
        return result.scalar_one_or_none() is not None

    @require_scope("agent:read")
    async def list_provider_credential_configs(
        self,
    ) -> list[ProviderCredentialConfig]:
        return [
            PROVIDER_CREDENTIAL_CONFIGS[source_type.value]
            for source_type in BUILT_IN_PROVIDER_ORDER
            if source_type.value in PROVIDER_CREDENTIAL_CONFIGS
        ]

    @require_scope("agent:read")
    async def get_provider_credential_config(
        self, provider: str
    ) -> ProviderCredentialConfig:
        ensure_builtin_provider(provider)
        return PROVIDER_CREDENTIAL_CONFIGS[provider]

    @require_scope("agent:update")
    async def create_provider_credentials(
        self, params: ModelCredentialCreate
    ) -> OrganizationSecret:
        ensure_builtin_provider(params.provider)
        secret_name = credential_secret_name(params.provider)
        keys = self._credential_key_values(params.credentials)
        try:
            existing = await self.secrets_service.get_org_secret_by_name(secret_name)
        except TracecatNotFoundError:
            create_params = SecretCreate(
                name=secret_name,
                type=SecretType.CUSTOM,
                description=f"Credentials for {params.provider} AI provider",
                keys=keys,
                tags={"provider": params.provider, "type": "agent-credentials"},
            )
            await self.secrets_service.create_org_secret(create_params)
            created = await self.secrets_service.get_org_secret_by_name(secret_name)
            return created

        update_params = SecretUpdate(keys=keys)
        await self.secrets_service.update_org_secret(existing, update_params)
        return existing

    @require_scope("agent:update")
    async def update_provider_credentials(
        self, provider: str, params: ModelCredentialUpdate
    ) -> OrganizationSecret:
        ensure_builtin_provider(provider)
        secret_name = credential_secret_name(provider)
        secret = await self.secrets_service.get_org_secret_by_name(secret_name)
        update_params = SecretUpdate(
            keys=self._credential_key_values(params.credentials),
        )
        await self.secrets_service.update_org_secret(secret, update_params)
        return secret

    @require_scope("agent:update")
    async def delete_provider_credentials(self, provider: str) -> None:
        ensure_builtin_provider(provider)
        secret_name = credential_secret_name(provider)
        try:
            secret = await self.secrets_service.get_org_secret_by_name(secret_name)
            await self.secrets_service.delete_org_secret(secret)
        except TracecatNotFoundError:
            logger.warning(
                "Attempted to delete non-existent credentials",
                provider=provider,
                secret_name=secret_name,
            )

    async def get_providers_status(self) -> dict[str, bool]:
        status: dict[str, bool] = {}
        for source_type in BUILT_IN_PROVIDER_ORDER:
            provider = source_type.value
            # This endpoint reports runtime readiness, not just secret presence.
            # Some providers need several keys before the gateway can use them.
            status[provider] = provider_credentials_complete(
                provider=provider,
                credentials=await self._load_provider_credentials(provider),
            )
        return status

    async def list_providers(
        self,
        *,
        configured_only: bool = True,
        include_discovered_models: bool = False,
    ) -> list[BuiltInProviderRead]:
        (
            discovery_status,
            last_refreshed_at,
            last_error,
        ) = await load_catalog_state(self.session)
        rows: list[AgentCatalog] = []
        enabled_catalog_ids: set[uuid.UUID] = set()
        if include_discovered_models:
            builtin_providers = [
                source_type.value for source_type in BUILT_IN_PROVIDER_ORDER
            ]
            rows = list(
                (
                    await self.session.execute(
                        select(AgentCatalog)
                        .where(
                            AgentCatalog.organization_id.is_(None),
                            AgentCatalog.model_provider.in_(builtin_providers),
                        )
                        .order_by(
                            AgentCatalog.model_provider.asc(),
                            AgentCatalog.model_name.asc(),
                        )
                    )
                )
                .scalars()
                .all()
            )
            enabled_catalog_ids = set(
                (
                    await self.session.execute(
                        select(AgentModelSelectionLink.catalog_id)
                        .join(
                            AgentCatalog,
                            AgentModelSelectionLink.catalog_id == AgentCatalog.id,
                        )
                        .where(
                            AgentModelSelectionLink.organization_id
                            == self.organization_id,
                            AgentModelSelectionLink.workspace_id.is_(None),
                        )
                    )
                )
                .scalars()
                .all()
            )
        # The same credential snapshot is reused for every provider row.
        credentials_by_provider = {
            source_type.value: await self._load_provider_credentials(source_type.value)
            for source_type in BUILT_IN_PROVIDER_ORDER
        }
        providers: list[BuiltInProviderRead] = []
        for source_type in BUILT_IN_PROVIDER_ORDER:
            provider = source_type.value
            credentials = credentials_by_provider[provider]
            credentials_configured = provider_credentials_complete(
                provider=provider,
                credentials=credentials,
            )
            if configured_only and not credentials_configured:
                continue
            providers.append(
                self._build_provider_read(
                    provider=provider,
                    discovery_status=discovery_status,
                    last_refreshed_at=last_refreshed_at,
                    last_error=last_error,
                    credentials=credentials,
                    rows=rows if include_discovered_models else [],
                    enabled_catalog_ids=enabled_catalog_ids,
                )
            )
        return providers
