"""Services for the consolidated integrations catalog.

- IntegrationCatalogService: read/write the Integration table (catalog)
- ConnectionsService: expose connection-shaped projections over credential
  source-of-truth tables.

Static/API-key credentials remain in Secret. OAuth credentials remain in
OAuthIntegration. The catalog API presents both as "connections" for the UI.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence

from pydantic import BaseModel, SecretStr
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.auth.secrets import get_db_encryption_key
from tracecat.auth.types import Role
from tracecat.db.models import (
    Integration,
    OAuthIntegration,
    PlatformRegistryAction,
    PlatformRegistryRepository,
    PlatformRegistryVersion,
    RegistryAction,
    RegistryRepository,
    RegistryVersion,
    Secret,
)
from tracecat.exceptions import TracecatNotFoundError
from tracecat.integrations.catalog.schemas import (
    CatalogAuthOption,
    CatalogConnectionCreate,
    CatalogConnectionRead,
    CatalogCredentialField,
    CatalogIntegrationRead,
    CatalogStaticKVConnectionCreate,
)
from tracecat.integrations.enums import (
    ConnectionAuthMethod,
    IntegrationSource,
    IntegrationStatus,
    OAuthGrantType,
)
from tracecat.integrations.providers import all_providers
from tracecat.integrations.providers.base import (
    AuthorizationCodeOAuthProvider,
    BaseOAuthProvider,
    ClientCredentialsOAuthProvider,
    ServiceAccountOAuthProvider,
)
from tracecat.logger import logger
from tracecat.registry.versions.schemas import RegistryVersionManifest
from tracecat.secrets.constants import DEFAULT_SECRETS_ENVIRONMENT
from tracecat.secrets.encryption import encrypt_keyvalues
from tracecat.secrets.enums import SecretType
from tracecat.secrets.schemas import SecretKeyValue
from tracecat.service import BaseWorkspaceService


def _label_from_key(key: str) -> str:
    acronyms = {
        "AI",
        "API",
        "AWS",
        "CA",
        "GCP",
        "ID",
        "IP",
        "JSON",
        "JWT",
        "MCP",
        "SQL",
        "SSH",
        "SSL",
        "TLS",
        "URI",
        "URL",
    }
    words: list[str] = []
    for part in key.split("_"):
        if part in acronyms:
            words.append(part)
        elif not words:
            words.append(part.lower().capitalize())
        else:
            words.append(part.lower())
    return " ".join(words)


def _field_from_key(*, key: str, required: bool) -> CatalogCredentialField:
    return CatalogCredentialField(
        key=key,
        label=_label_from_key(key),
        required=required,
        secret=True,
        multiline=key.endswith("_JSON")
        or key.endswith("_PRIVATE_KEY")
        or key.endswith("_CERTIFICATE"),
    )


def _provider_auth_method(
    provider_cls: type[BaseOAuthProvider],
) -> ConnectionAuthMethod:
    if issubclass(provider_cls, ServiceAccountOAuthProvider):
        return ConnectionAuthMethod.SERVICE_ACCOUNT
    if issubclass(provider_cls, AuthorizationCodeOAuthProvider):
        return ConnectionAuthMethod.OAUTH_AUTH_CODE
    return ConnectionAuthMethod.OAUTH_CLIENT_CREDENTIALS


def _fallback_oauth_auth_method(grant_type: OAuthGrantType) -> ConnectionAuthMethod:
    if grant_type == OAuthGrantType.AUTHORIZATION_CODE:
        return ConnectionAuthMethod.OAUTH_AUTH_CODE
    return ConnectionAuthMethod.OAUTH_CLIENT_CREDENTIALS


def _provider_cls_for(
    provider_id: str,
    grant_type: OAuthGrantType,
) -> type[BaseOAuthProvider] | None:
    return next(
        (
            cls
            for cls in all_providers()
            if cls.id == provider_id and cls.grant_type == grant_type
        ),
        None,
    )


def _oauth_connection_label(auth_method: ConnectionAuthMethod) -> str:
    match auth_method:
        case ConnectionAuthMethod.OAUTH_AUTH_CODE:
            return "OAuth account"
        case ConnectionAuthMethod.OAUTH_CLIENT_CREDENTIALS:
            return "Client credentials"
        case ConnectionAuthMethod.SERVICE_ACCOUNT:
            return "Service account"
        case _:
            return _provider_auth_label(auth_method)


def _provider_auth_label(auth_method: ConnectionAuthMethod) -> str:
    match auth_method:
        case ConnectionAuthMethod.OAUTH_AUTH_CODE:
            return "OAuth"
        case ConnectionAuthMethod.OAUTH_CLIENT_CREDENTIALS:
            return "Client credentials"
        case ConnectionAuthMethod.SERVICE_ACCOUNT:
            return "Service account"
        case _:
            return auth_method.value.replace("_", " ").title()


def _credential_fields_from_secret(
    secret: Mapping[str, object],
) -> list[CatalogCredentialField]:
    fields: list[CatalogCredentialField] = []
    required_keys = secret.get("keys")
    if isinstance(required_keys, list):
        for key in required_keys:
            if isinstance(key, str):
                fields.append(_field_from_key(key=key, required=True))

    optional_keys = secret.get("optional_keys")
    if isinstance(optional_keys, list):
        for key in optional_keys:
            if isinstance(key, str):
                fields.append(_field_from_key(key=key, required=False))
    return fields


def _secret_mapping(secret: object) -> Mapping[str, object] | None:
    if isinstance(secret, Mapping):
        return secret
    if isinstance(secret, BaseModel):
        return secret.model_dump(mode="json")
    return None


def _record_secret_fields(
    fields_by_name: dict[str, list[CatalogCredentialField]],
    raw_secret: object,
) -> None:
    secret = _secret_mapping(raw_secret)
    if secret is None or secret.get("type") == "oauth":
        return
    name = secret.get("name")
    if not isinstance(name, str) or not name:
        return
    fields = _credential_fields_from_secret(secret)
    if not fields:
        return
    fields_by_name[name] = _merge_credential_fields(
        fields_by_name.get(name, []),
        fields,
    )


def _merge_credential_fields(
    existing: list[CatalogCredentialField],
    incoming: list[CatalogCredentialField],
) -> list[CatalogCredentialField]:
    by_key = {field.key: field for field in existing}
    for field in incoming:
        prior = by_key.get(field.key)
        if prior is None:
            by_key[field.key] = field
            continue
        if field.required and not prior.required:
            by_key[field.key] = prior.model_copy(update={"required": True})
    return list(by_key.values())


def _validate_static_kv_fields(
    static_option: CatalogAuthOption,
    keys: Mapping[str, str],
) -> None:
    required = {field.key for field in static_option.fields if field.required}
    allowed = {field.key for field in static_option.fields}
    provided = set(keys)
    missing = sorted(required - provided)
    unknown = sorted(provided - allowed)
    if missing:
        raise ValueError("Missing required credential fields: " + ", ".join(missing))
    if unknown:
        raise ValueError("Unsupported credential fields: " + ", ".join(unknown))


class IntegrationCatalogService(BaseWorkspaceService):
    """CRUD over the Integration catalog table."""

    service_name = "integrations.catalog"
    _static_fields_by_secret_name: dict[str, list[CatalogCredentialField]] | None

    def __init__(self, session: AsyncSession, role: Role | None = None):
        super().__init__(session, role=role)
        self._static_fields_by_secret_name = None

    async def list_integrations(
        self,
        *,
        source: IntegrationSource | None = None,
        search: str | None = None,
    ) -> Sequence[Integration]:
        """List integrations visible to the current workspace.

        Returns rows where ``workspace_id`` IS NULL (platform-shipped) OR
        equals the caller's workspace.
        """
        stmt = select(Integration).where(
            (Integration.workspace_id.is_(None))
            | (Integration.workspace_id == self.workspace_id)
        )
        if source is not None:
            stmt = stmt.where(Integration.source == source)
        if search:
            like = f"%{search.lower()}%"
            stmt = stmt.where(
                or_(
                    func.lower(Integration.display_name).like(like),
                    func.lower(Integration.namespace).like(like),
                )
            )
        stmt = stmt.order_by(Integration.display_name)
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def to_read_schema(self, integration: Integration) -> CatalogIntegrationRead:
        return CatalogIntegrationRead(
            id=integration.id,
            workspace_id=integration.workspace_id,
            namespace=integration.namespace,
            display_name=integration.display_name,
            description=integration.description,
            icon_url=integration.icon_url,
            source=integration.source,
            auth_options=await self.auth_options_for(integration),
            created_at=integration.created_at,
            updated_at=integration.updated_at,
        )

    async def get_integration(self, integration_id: uuid.UUID) -> Integration:
        stmt = select(Integration).where(
            Integration.id == integration_id,
            (Integration.workspace_id.is_(None))
            | (Integration.workspace_id == self.workspace_id),
        )
        result = await self.session.execute(stmt)
        integration = result.scalar_one_or_none()
        if integration is None:
            raise TracecatNotFoundError(f"Integration {integration_id} not found")
        return integration

    async def auth_options_for(
        self, integration: Integration
    ) -> list[CatalogAuthOption]:
        """Return auth paths that are actually supported by this integration."""
        options: list[CatalogAuthOption] = []
        if integration.source == IntegrationSource.PLATFORM:
            options.extend(await self._provider_auth_options(integration.namespace))

        static_fields = await self._static_fields_for_secret(integration.namespace)
        if static_fields:
            label = "API key" if len(static_fields) == 1 else "Static credentials"
            options.append(
                CatalogAuthOption(
                    auth_method=ConnectionAuthMethod.STATIC_KV,
                    label=label,
                    description="Credential fields required by registry actions.",
                    fields=static_fields,
                    status=await self._static_connection_status(integration.namespace),
                )
            )
        return options

    async def _provider_auth_options(self, namespace: str) -> list[CatalogAuthOption]:
        provider_classes = [
            cls for cls in all_providers() if getattr(cls, "id", None) == namespace
        ]
        if not provider_classes:
            return []

        existing = await self.session.execute(
            select(OAuthIntegration).where(
                OAuthIntegration.workspace_id == self.workspace_id,
                OAuthIntegration.provider_id == namespace,
            )
        )
        integrations_by_grant: dict[OAuthGrantType, IntegrationStatus] = {}
        for integration in existing.scalars().all():
            current_status = integrations_by_grant.get(integration.grant_type)
            next_status = integration.status
            if current_status is None:
                integrations_by_grant[integration.grant_type] = next_status
                continue
            if next_status == IntegrationStatus.CONNECTED:
                integrations_by_grant[integration.grant_type] = next_status
            elif (
                next_status == IntegrationStatus.CONFIGURED
                and current_status == IntegrationStatus.NOT_CONFIGURED
            ):
                integrations_by_grant[integration.grant_type] = next_status

        options: list[CatalogAuthOption] = []
        for provider_cls in provider_classes:
            if not issubclass(
                provider_cls, ClientCredentialsOAuthProvider
            ) and not issubclass(
                provider_cls,
                AuthorizationCodeOAuthProvider,
            ):
                continue
            auth_method = _provider_auth_method(provider_cls)
            integration_status = integrations_by_grant.get(provider_cls.grant_type)
            metadata = provider_cls.metadata
            options.append(
                CatalogAuthOption(
                    auth_method=auth_method,
                    label=_provider_auth_label(auth_method),
                    description=metadata.description,
                    provider_id=provider_cls.id,
                    grant_type=provider_cls.grant_type,
                    requires_config=metadata.requires_config,
                    enabled=metadata.enabled,
                    status=integration_status or IntegrationStatus.NOT_CONFIGURED,
                    fields=_provider_config_fields(auth_method),
                )
            )

        grant_order = {
            OAuthGrantType.AUTHORIZATION_CODE: 0,
            OAuthGrantType.CLIENT_CREDENTIALS: 1,
        }
        return sorted(
            options,
            key=lambda option: (
                grant_order.get(
                    option.grant_type or OAuthGrantType.CLIENT_CREDENTIALS, 99
                ),
                option.label,
            ),
        )

    async def _static_connection_status(
        self,
        namespace: str,
    ) -> IntegrationStatus:
        result = await self.session.execute(
            select(Secret.id)
            .where(
                Secret.workspace_id == self.workspace_id,
                Secret.name == namespace,
                Secret.environment == DEFAULT_SECRETS_ENVIRONMENT,
            )
            .limit(1)
        )
        if result.scalar_one_or_none() is None:
            return IntegrationStatus.NOT_CONFIGURED
        return IntegrationStatus.CONNECTED

    async def _static_fields_for_secret(
        self, secret_name: str
    ) -> list[CatalogCredentialField]:
        fields_by_name = await self._load_static_fields_by_secret_name()
        return fields_by_name.get(secret_name, [])

    async def _load_static_fields_by_secret_name(
        self,
    ) -> dict[str, list[CatalogCredentialField]]:
        if self._static_fields_by_secret_name is not None:
            return self._static_fields_by_secret_name

        fields_by_name: dict[str, list[CatalogCredentialField]] = {}

        platform_result = await self.session.execute(
            select(PlatformRegistryAction.secrets).where(
                PlatformRegistryAction.secrets.is_not(None)
            )
        )
        workspace_result = await self.session.execute(
            select(RegistryAction.secrets).where(
                RegistryAction.organization_id == self.organization_id,
                RegistryAction.secrets.is_not(None),
            )
        )

        for secrets_payload in [
            *platform_result.scalars().all(),
            *workspace_result.scalars().all(),
        ]:
            if not isinstance(secrets_payload, list):
                continue
            for raw_secret in secrets_payload:
                _record_secret_fields(fields_by_name, raw_secret)

        await self._record_manifest_static_fields(fields_by_name)

        self._static_fields_by_secret_name = fields_by_name
        return fields_by_name

    async def _record_manifest_static_fields(
        self,
        fields_by_name: dict[str, list[CatalogCredentialField]],
    ) -> None:
        """Merge static secret fields from current registry manifests.

        The catalog seed reads platform manifests, while registry action rows can lag
        in local or freshly migrated environments. Reading both keeps catalog auth
        capabilities aligned with the rows that were seeded.
        """
        platform_manifest_result = await self.session.execute(
            select(PlatformRegistryVersion.manifest)
            .join(
                PlatformRegistryRepository,
                PlatformRegistryVersion.repository_id == PlatformRegistryRepository.id,
            )
            .where(
                PlatformRegistryRepository.current_version_id
                == PlatformRegistryVersion.id
            )
        )
        org_manifest_result = await self.session.execute(
            select(RegistryVersion.manifest)
            .join(
                RegistryRepository,
                RegistryVersion.repository_id == RegistryRepository.id,
            )
            .where(
                RegistryRepository.organization_id == self.organization_id,
                RegistryRepository.current_version_id == RegistryVersion.id,
            )
        )

        for manifest_data in [
            *platform_manifest_result.scalars().all(),
            *org_manifest_result.scalars().all(),
        ]:
            manifest = RegistryVersionManifest.model_validate(manifest_data)
            for manifest_action in manifest.actions.values():
                for raw_secret in manifest_action.secrets or []:
                    _record_secret_fields(fields_by_name, raw_secret)

    async def validate_connection_create(
        self,
        integration: Integration,
        params: CatalogConnectionCreate,
    ) -> None:
        """Validate a connection payload against integration-supported auth options."""
        supported = await self.auth_options_for(integration)
        matching = [
            option for option in supported if option.auth_method == params.auth_method
        ]
        if not matching:
            raise ValueError(
                f"{integration.display_name} does not support {params.auth_method.value} connections."
            )

        if any(option.provider_id for option in matching):
            raise ValueError(
                f"{integration.display_name} uses the OAuth provider flow for {params.auth_method.value}."
            )

        if isinstance(params, CatalogStaticKVConnectionCreate):
            _validate_static_kv_fields(matching[0], params.keys)


def _provider_config_fields(
    auth_method: ConnectionAuthMethod,
) -> list[CatalogCredentialField]:
    match auth_method:
        case ConnectionAuthMethod.OAUTH_AUTH_CODE:
            return [
                CatalogCredentialField(
                    key="client_id",
                    label="Client ID",
                    required=True,
                    secret=False,
                ),
                CatalogCredentialField(
                    key="client_secret",
                    label="Client secret",
                    required=True,
                ),
            ]
        case ConnectionAuthMethod.OAUTH_CLIENT_CREDENTIALS:
            return [
                CatalogCredentialField(
                    key="client_id",
                    label="Client ID",
                    required=True,
                    secret=False,
                ),
                CatalogCredentialField(
                    key="client_secret",
                    label="Client secret",
                    required=True,
                ),
            ]
        case ConnectionAuthMethod.SERVICE_ACCOUNT:
            return [
                CatalogCredentialField(
                    key="service_account_json",
                    label="Service account JSON",
                    required=True,
                    multiline=True,
                    placeholder='{"type": "service_account", ...}',
                ),
            ]
        case _:
            return []


class ConnectionsService(BaseWorkspaceService):
    """Connection-shaped projections over credential source-of-truth tables."""

    service_name = "integrations.connections"
    _encryption_key: str

    def __init__(self, session: AsyncSession, role: Role | None = None):
        super().__init__(session, role=role)
        self._encryption_key = get_db_encryption_key()

    async def list_connection_summaries(
        self,
        integration: Integration,
    ) -> list[CatalogConnectionRead]:
        """Return connection-shaped rows projected from credential storage.

        Static/API-key credentials live in ``secret``. OAuth provider bindings
        live in ``oauth_integration``. The catalog projects both into the same
        read schema for UI consistency.
        """
        rows = await self._list_static_connection_summaries(integration)
        rows.extend(await self._list_oauth_connection_summaries(integration))
        return sorted(rows, key=lambda row: row.created_at, reverse=True)

    async def _list_static_connection_summaries(
        self,
        integration: Integration,
    ) -> list[CatalogConnectionRead]:
        result = await self.session.execute(
            select(Secret).where(
                Secret.workspace_id == self.workspace_id,
                Secret.name == integration.namespace,
            )
        )
        rows: list[CatalogConnectionRead] = []
        for secret in result.scalars().all():
            rows.append(self._static_connection_read(integration, secret))
        return rows

    def _static_connection_read(
        self,
        integration: Integration,
        secret: Secret,
    ) -> CatalogConnectionRead:
        return CatalogConnectionRead(
            id=secret.id,
            integration_id=integration.id,
            workspace_id=secret.workspace_id,
            user_id=None,
            auth_method=ConnectionAuthMethod.STATIC_KV,
            label=self._static_connection_label(secret),
            expires_at=None,
            scope=None,
            metadata_={
                "source": "secret",
                "secret_id": str(secret.id),
                "secret_name": secret.name,
                "environment": secret.environment,
                "secret_type": secret.type,
            },
            created_at=secret.created_at,
            updated_at=secret.updated_at,
            is_expired=False,
        )

    @staticmethod
    def _static_connection_label(secret: Secret) -> str:
        if secret.environment == DEFAULT_SECRETS_ENVIRONMENT:
            return "Default"
        return secret.environment

    async def _list_oauth_connection_summaries(
        self,
        integration: Integration,
    ) -> list[CatalogConnectionRead]:
        result = await self.session.execute(
            select(OAuthIntegration).where(
                OAuthIntegration.workspace_id == self.workspace_id,
                OAuthIntegration.provider_id == integration.namespace,
            )
        )
        rows: list[CatalogConnectionRead] = []
        for oauth in result.scalars().all():
            provider_cls = _provider_cls_for(oauth.provider_id, oauth.grant_type)
            auth_method = (
                _provider_auth_method(provider_cls)
                if provider_cls is not None
                else _fallback_oauth_auth_method(oauth.grant_type)
            )
            if (
                auth_method == ConnectionAuthMethod.OAUTH_AUTH_CODE
                and oauth.status != IntegrationStatus.CONNECTED
            ):
                continue
            if (
                auth_method != ConnectionAuthMethod.OAUTH_AUTH_CODE
                and oauth.status == IntegrationStatus.NOT_CONFIGURED
            ):
                continue
            if oauth.workspace_id is None:
                continue

            rows.append(
                CatalogConnectionRead(
                    id=oauth.id,
                    integration_id=integration.id,
                    workspace_id=oauth.workspace_id,
                    user_id=oauth.user_id,
                    auth_method=auth_method,
                    label=_oauth_connection_label(auth_method),
                    expires_at=oauth.expires_at,
                    scope=oauth.scope,
                    metadata_={
                        "source": "oauth_integration",
                        "provider_id": oauth.provider_id,
                        "grant_type": oauth.grant_type.value,
                    },
                    created_at=oauth.created_at,
                    updated_at=oauth.updated_at,
                    is_expired=oauth.is_expired,
                )
            )
        return rows

    async def create_connection(
        self,
        integration: Integration,
        params: CatalogConnectionCreate,
    ) -> CatalogConnectionRead:
        """Create or replace a static Secret for an integration environment."""
        if not isinstance(params, CatalogStaticKVConnectionCreate):
            raise ValueError(f"Unsupported connection payload: {type(params).__name__}")
        if not params.keys:
            raise ValueError("At least one credential field is required.")
        environment = params.environment.strip() or DEFAULT_SECRETS_ENVIRONMENT

        encrypted_keys = encrypt_keyvalues(
            [
                SecretKeyValue(key=key, value=SecretStr(value))
                for key, value in params.keys.items()
            ],
            key=self._encryption_key,
        )
        result = await self.session.execute(
            select(Secret).where(
                Secret.workspace_id == self.workspace_id,
                Secret.name == integration.namespace,
                Secret.environment == environment,
            )
        )
        secret = result.scalar_one_or_none()
        if secret is None:
            secret = Secret(
                workspace_id=self.workspace_id,
                name=integration.namespace,
                type=SecretType.CUSTOM,
                description=f"Credentials for {integration.display_name}.",
                encrypted_keys=encrypted_keys,
                environment=environment,
            )
        else:
            secret.type = SecretType.CUSTOM
            secret.encrypted_keys = encrypted_keys
        self.session.add(secret)
        await self.session.flush()
        await self.session.refresh(secret)
        logger.info(
            "Stored static integration credentials",
            secret_id=secret.id,
            integration_id=integration.id,
            auth_method=params.auth_method,
            workspace_id=self.workspace_id,
        )
        return self._static_connection_read(integration, secret)

    async def delete_connection(self, connection_id: uuid.UUID) -> None:
        stmt = select(Secret).where(
            Secret.id == connection_id,
            Secret.workspace_id == self.workspace_id,
        )
        result = await self.session.execute(stmt)
        secret = result.scalar_one_or_none()
        if secret is not None:
            await self.session.delete(secret)
            await self.session.flush()
            return

        oauth_stmt = select(OAuthIntegration).where(
            OAuthIntegration.id == connection_id,
            OAuthIntegration.workspace_id == self.workspace_id,
        )
        oauth_result = await self.session.execute(oauth_stmt)
        oauth = oauth_result.scalar_one_or_none()
        if oauth is None:
            raise TracecatNotFoundError(f"Connection {connection_id} not found")
        await self.session.delete(oauth)
        await self.session.flush()
