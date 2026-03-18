from __future__ import annotations

import asyncio
import contextlib
import json
import uuid
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any, cast
from urllib.parse import urlsplit, urlunsplit

import httpx
import orjson
from google.auth.transport.requests import Request as GoogleAuthRequest
from google.oauth2 import service_account
from cryptography.fernet import InvalidToken
from pydantic import SecretStr
from sqlalchemy import case, delete, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from tracecat_registry._internal import secrets as registry_secrets

from tracecat import config
from tracecat.agent.builtin_catalog import (
    LITELLM_PINNED_VERSION,
    BuiltInCatalogModel,
    _is_agent_enableable,
    get_builtin_catalog_by_provider,
    get_builtin_catalog_models,
)
from tracecat.agent.config import MODEL_CONFIGS, PROVIDER_CREDENTIAL_CONFIGS
from tracecat.agent.legacy_model_matching import (
    LegacyCatalogMatch,
    resolve_accessible_catalog_match_for_model_name,
    resolve_accessible_catalog_match_for_provider_model,
    resolve_catalog_match_for_provider_model,
    resolve_enabled_catalog_match_for_model_name,
    resolve_enabled_catalog_match_for_provider_model,
)
from tracecat.agent.preset.service import AgentPresetService
from tracecat.agent.schemas import (
    AgentModelSourceCreate,
    AgentModelSourceRead,
    AgentModelSourceUpdate,
    BuiltInCatalogEntry,
    BuiltInCatalogRead,
    BuiltInProviderRead,
    DefaultModelSelection,
    EnabledModelOperation,
    EnabledModelRuntimeConfig,
    EnabledModelRuntimeConfigUpdate,
    EnabledModelsBatchOperation,
    ManualDiscoveredModel,
    ModelCatalogEntry,
    ModelConfig,
    ModelCredentialCreate,
    ModelCredentialUpdate,
    ModelSelection,
    ProviderCredentialConfig,
    WorkspaceModelSubsetRead,
    WorkspaceModelSubsetUpdate,
)
from tracecat.agent.types import (
    AgentConfig,
    CustomModelSourceFlavor,
    CustomModelSourceType,
    ModelDiscoveryStatus,
    ModelSourceType,
)
from tracecat.auth.types import Role
from tracecat.authz.controls import require_scope
from tracecat.db.engine import (
    get_async_session_bypass_rls_context_manager,
)
from tracecat.db.locks import (
    derive_lock_key_from_parts,
    pg_advisory_lock,
    pg_advisory_unlock,
    try_pg_advisory_lock,
)
from tracecat.db.models import (
    AgentCatalog,
    AgentEnabledModel,
    AgentModelSource,
    AgentPreset,
    AgentPresetVersion,
    AgentSession,
    Organization,
    OrganizationSecret,
    PlatformSetting,
    Workspace,
)
from tracecat.exceptions import TracecatAuthorizationError, TracecatNotFoundError
from tracecat.integrations.aws_assume_role import build_workspace_external_id
from tracecat.logger import logger
from tracecat.secrets import secrets_manager
from tracecat.secrets.constants import DEFAULT_SECRETS_ENVIRONMENT
from tracecat.secrets.encryption import decrypt_value, encrypt_value
from tracecat.secrets.enums import SecretType
from tracecat.secrets.schemas import SecretCreate, SecretKeyValue, SecretUpdate
from tracecat.secrets.service import SecretsService
from tracecat.service import BaseOrgService, requires_entitlement
from tracecat.settings.schemas import SettingCreate, SettingUpdate, ValueType
from tracecat.settings.service import SettingsService
from tracecat.tiers.enums import Entitlement

_AWS_ASSUME_ROLE_EXTERNAL_ID_SECRET_KEY = "TRACECAT_AWS_EXTERNAL_ID"
_VERTEX_BEARER_TOKEN_KEY = "VERTEX_AI_BEARER_TOKEN"
_GOOGLE_CLOUD_SCOPE = "https://www.googleapis.com/auth/cloud-platform"


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


DEFAULT_SIDECAR_STATE_SETTINGS = {
    "discovery_status": "agent_default_sidecar_discovery_status",
    "last_refreshed_at": "agent_default_sidecar_last_refreshed_at",
    "last_error": "agent_default_sidecar_last_error",
}
DEFAULT_SIDECAR_SOURCE_NAME = "Default models"
MODEL_CATALOG_STARTUP_SYNC_LOCK_KEY = derive_lock_key_from_parts(
    "agent_model_catalog_startup_sync"
)
BUILTIN_CATALOG_STATE_SETTINGS = {
    "discovery_status": "agent_builtin_catalog_discovery_status",
    "last_refreshed_at": "agent_builtin_catalog_last_refreshed_at",
    "last_error": "agent_builtin_catalog_last_error",
}
_BUILT_IN_PROVIDER_SOURCE_TYPES = {
    ModelSourceType.OPENAI,
    ModelSourceType.ANTHROPIC,
    ModelSourceType.GEMINI,
    ModelSourceType.BEDROCK,
    ModelSourceType.VERTEX_AI,
    ModelSourceType.AZURE_OPENAI,
    ModelSourceType.AZURE_AI,
}


def _sanitize_url_for_log(url: str) -> str:
    """Strip userinfo, query strings, and fragments before logging URLs."""
    try:
        parsed = urlsplit(url)
    except ValueError:
        return "<invalid-url>"

    host = parsed.hostname or ""
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"

    netloc = host
    if parsed.port is not None:
        netloc = f"{netloc}:{parsed.port}"

    return urlunsplit((parsed.scheme, netloc, parsed.path, "", ""))


_CUSTOM_SOURCE_TYPES = {
    ModelSourceType.OPENAI_COMPATIBLE_GATEWAY,
    ModelSourceType.MANUAL_CUSTOM,
}
LEGACY_CUSTOM_PROVIDER = "custom-model-provider"
LEGACY_CUSTOM_SOURCE_NAME = "Imported legacy custom model"
ENABLE_ALL_MODELS_ON_UPGRADE_SETTING = "agent_enable_all_models_on_upgrade"
PRUNED_MODEL_NAME_SUFFIX = " [unavailable]"
SOURCE_RUNTIME_API_KEY = "TRACECAT_SOURCE_API_KEY"
SOURCE_RUNTIME_API_KEY_HEADER = "TRACECAT_SOURCE_API_KEY_HEADER"
SOURCE_RUNTIME_API_VERSION = "TRACECAT_SOURCE_API_VERSION"
SOURCE_RUNTIME_BASE_URL = "TRACECAT_SOURCE_BASE_URL"
_BUILT_IN_PROVIDER_ORDER = (
    ModelSourceType.OPENAI,
    ModelSourceType.ANTHROPIC,
    ModelSourceType.GEMINI,
    ModelSourceType.VERTEX_AI,
    ModelSourceType.BEDROCK,
    ModelSourceType.AZURE_OPENAI,
    ModelSourceType.AZURE_AI,
)


def _parse_catalog_offset(cursor: str | None) -> int:
    if cursor is None:
        return 0
    if not cursor.isdecimal():
        raise ValueError("Invalid cursor. Expected a non-negative integer offset.")
    return int(cursor)


@dataclass(frozen=True, slots=True)
class ModelSelectionKey:
    source_id: uuid.UUID | None
    model_provider: str
    model_name: str


@dataclass(frozen=True, slots=True)
class ResolvedCatalogRecord:
    source_id: uuid.UUID | None
    model_provider: str
    model_name: str
    source_type: ModelSourceType
    source_name: str
    base_url: str | None
    last_refreshed_at: datetime | None
    metadata: dict[str, Any] | None


@dataclass(frozen=True, slots=True)
class OpenAICompatibleDiscoveryResult:
    items: list[dict[str, object]]
    runtime_base_url: str


@dataclass(frozen=True, slots=True)
class SourceDiscoveryResult:
    models: list[dict[str, object]]
    runtime_base_url: str | None = None


@dataclass(frozen=True, slots=True)
class LegacyModelRepairSummary:
    migrated_defaults: int = 0
    migrated_presets: int = 0
    migrated_versions: int = 0
    unresolved_defaults: int = 0
    unresolved_presets: int = 0
    unresolved_versions: int = 0
    ambiguous_defaults: int = 0
    ambiguous_presets: int = 0
    ambiguous_versions: int = 0


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

    def _serialize_sensitive_config(
        self, payload: dict[str, str] | None
    ) -> bytes | None:
        if not payload:
            return None
        return encrypt_value(
            orjson.dumps(payload),
            key=config.TRACECAT__DB_ENCRYPTION_KEY or "",
        )

    def _deserialize_sensitive_config(self, payload: bytes | None) -> dict[str, str]:
        if not payload:
            return {}
        try:
            decrypted = decrypt_value(
                payload,
                key=config.TRACECAT__DB_ENCRYPTION_KEY or "",
            )
        except (InvalidToken, ValueError):
            try:
                # Tolerate legacy/plain JSON rows and lightweight test fixtures.
                return orjson.loads(payload)
            except orjson.JSONDecodeError:
                self.logger.warning(
                    "Failed to decode source config payload; treating as empty",
                    organization_id=str(self.organization_id),
                )
                return {}
        return orjson.loads(decrypted)

    def _provider_from_source_type(self, source_type: ModelSourceType) -> str:
        match source_type:
            case ModelSourceType.OPENAI_COMPATIBLE_GATEWAY:
                return "openai_compatible_gateway"
            case ModelSourceType.MANUAL_CUSTOM:
                return "direct_endpoint"
            case _:
                return source_type.value

    def _provider_label(self, provider: str) -> str:
        if provider in PROVIDER_CREDENTIAL_CONFIGS:
            return PROVIDER_CREDENTIAL_CONFIGS[provider].label
        return provider.replace("_", " ").title()

    def _ensure_builtin_provider(self, provider: str) -> str:
        if provider not in {
            source_type.value for source_type in _BUILT_IN_PROVIDER_ORDER
        }:
            raise TracecatNotFoundError(f"Provider {provider} not found")
        return provider

    def _source_config_payload(
        self,
        *,
        api_key: str | None,
        flavor: CustomModelSourceFlavor | None,
    ) -> dict[str, str] | None:
        payload: dict[str, str] = {}
        if api_key:
            payload["api_key"] = api_key
        if flavor is not None:
            payload["flavor"] = flavor.value
        return payload or None

    def _source_flavor(
        self, source_config: dict[str, str]
    ) -> CustomModelSourceFlavor | None:
        if not (flavor := source_config.get("flavor")):
            return None
        return CustomModelSourceFlavor(flavor)

    def _openai_compatible_runtime_base_url(self, url: str) -> str:
        base = url.strip().rstrip("/")
        if base.endswith("/v1/models"):
            return f"{base.removesuffix('/v1/models')}/v1"
        if base.endswith("/models"):
            return base.removesuffix("/models").rstrip("/")
        return base

    def _source_runtime_base_url(
        self,
        source: AgentModelSource,
        *,
        source_config: dict[str, str] | None = None,
    ) -> str | None:
        if not source.base_url:
            return None
        if (
            self._source_type_from_row(source)
            != CustomModelSourceType.OPENAI_COMPATIBLE_GATEWAY
        ):
            return source.base_url
        config_values = source_config or self._deserialize_sensitive_config(
            source.encrypted_config
        )
        if runtime_base_url := config_values.get("runtime_base_url"):
            return runtime_base_url
        return self._openai_compatible_runtime_base_url(source.base_url)

    async def _get_legacy_custom_provider_secret_config(self) -> dict[str, str] | None:
        secret_name = self._get_credential_secret_name(LEGACY_CUSTOM_PROVIDER)
        stmt = select(OrganizationSecret).where(
            OrganizationSecret.organization_id == self.organization_id,
            OrganizationSecret.name == secret_name,
            OrganizationSecret.environment == DEFAULT_SECRETS_ENVIRONMENT,
        )
        secret = (await self.session.execute(stmt)).scalar_one_or_none()
        if secret is None:
            return None
        credentials = {
            kv.key: kv.value.get_secret_value()
            for kv in self.secrets_service.decrypt_keys(secret.encrypted_keys)
        }
        if not (
            base_url := credentials.get("CUSTOM_MODEL_PROVIDER_BASE_URL", "").strip()
        ):
            return None
        if not (
            model_name := credentials.get(
                "CUSTOM_MODEL_PROVIDER_MODEL_NAME", ""
            ).strip()
        ):
            return None
        config = {
            "base_url": base_url,
            "model_name": model_name,
        }
        if api_key := credentials.get("CUSTOM_MODEL_PROVIDER_API_KEY", "").strip():
            config["api_key"] = api_key
        return config

    async def _find_legacy_custom_provider_source(
        self,
        *,
        base_url: str,
        model_name: str,
    ) -> AgentModelSource | None:
        stmt = select(AgentModelSource).where(
            AgentModelSource.organization_id == self.organization_id,
            AgentModelSource.base_url == base_url,
        )
        rows = list((await self.session.execute(stmt)).scalars().all())
        for row in rows:
            for item in row.declared_models or []:
                if (
                    item.get("model_name") == model_name
                    and item.get("model_provider") == LEGACY_CUSTOM_PROVIDER
                ):
                    return row
        return None

    async def _ensure_legacy_custom_provider_source_synced(
        self,
    ) -> AgentModelSource | None:
        if not (
            secret_config := await self._get_legacy_custom_provider_secret_config()
        ):
            return None

        source = await self._find_legacy_custom_provider_source(
            base_url=secret_config["base_url"],
            model_name=secret_config["model_name"],
        )
        declared_models = [
            ManualDiscoveredModel(
                model_name=secret_config["model_name"],
                display_name=secret_config["model_name"],
                model_provider=LEGACY_CUSTOM_PROVIDER,
            ).model_dump(mode="json")
        ]
        encrypted_config = self._serialize_sensitive_config(
            self._source_config_payload(
                api_key=secret_config.get("api_key"),
                flavor=CustomModelSourceFlavor.MANUAL,
            )
        )

        if source is None:
            source = AgentModelSource(
                organization_id=self.organization_id,
                display_name=LEGACY_CUSTOM_SOURCE_NAME,
                model_provider=LEGACY_CUSTOM_PROVIDER,
                base_url=secret_config["base_url"],
                encrypted_config=encrypted_config,
                declared_models=declared_models,
            )
            self.session.add(source)
            await self.session.commit()
            await self.session.refresh(source)
        else:
            source.base_url = secret_config["base_url"]
            source.encrypted_config = encrypted_config
            source.declared_models = declared_models
            self.session.add(source)
            await self.session.commit()
            await self.session.refresh(source)

        await self.refresh_model_source(source.id)
        return source

    def _display_provider_for_model(
        self,
        *,
        source_type: ModelSourceType,
        model_id: str,
        provider_hint: str | None,
    ) -> str:
        if provider_hint and (
            normalized_provider_hint := provider_hint.strip().lower()
        ) in {
            *(source_type.value for source_type in ModelSourceType),
            LEGACY_CUSTOM_PROVIDER,
            "direct_endpoint",
        }:
            return normalized_provider_hint
        if source_type in _BUILT_IN_PROVIDER_SOURCE_TYPES:
            return source_type.value
        if source_type == ModelSourceType.OPENAI_COMPATIBLE_GATEWAY:
            return "openai_compatible_gateway"
        lowered = model_id.lower()
        if lowered.startswith("claude"):
            return "anthropic"
        if (
            lowered.startswith("gpt")
            or lowered.startswith("o1")
            or lowered.startswith("o3")
        ):
            return "openai"
        if lowered.startswith("gemini"):
            return "gemini"
        return self._provider_from_source_type(source_type)

    def _source_type_from_catalog(
        self,
        *,
        row: AgentCatalog,
        source: AgentModelSource | None = None,
    ) -> ModelSourceType:
        if row.source_id is None:
            return ModelSourceType(row.model_provider)
        if (
            source is not None
            and self._source_type_from_row(source)
            == CustomModelSourceType.OPENAI_COMPATIBLE_GATEWAY
        ):
            return ModelSourceType.OPENAI_COMPATIBLE_GATEWAY
        return ModelSourceType.MANUAL_CUSTOM

    def _source_name_from_catalog(
        self,
        *,
        row: AgentCatalog,
        source: AgentModelSource | None = None,
    ) -> str:
        if row.source_id is None:
            return self._provider_label(row.model_provider)
        if source is not None:
            return source.display_name
        return "Custom source"

    def _selection_key(
        self,
        *,
        source_id: uuid.UUID | None,
        model_provider: str,
        model_name: str,
    ) -> ModelSelectionKey:
        return ModelSelectionKey(
            source_id=source_id,
            model_provider=model_provider,
            model_name=model_name,
        )

    def _selection_key_from_catalog_row(
        self,
        row: AgentCatalog,
    ) -> ModelSelectionKey:
        return self._selection_key(
            source_id=row.source_id,
            model_provider=row.model_provider,
            model_name=row.model_name,
        )

    def _selection_key_from_enabled_row(
        self,
        row: AgentEnabledModel,
    ) -> ModelSelectionKey:
        return self._selection_key(
            source_id=row.source_id,
            model_provider=row.model_provider,
            model_name=row.model_name,
        )

    def _model_selection_from_key(self, selection: ModelSelectionKey) -> ModelSelection:
        return ModelSelection(
            source_id=selection.source_id,
            model_provider=selection.model_provider,
            model_name=selection.model_name,
        )

    def _model_selection_from_catalog_row(self, row: AgentCatalog) -> ModelSelection:
        return self._model_selection_from_key(self._selection_key_from_catalog_row(row))

    def _model_selection_from_enabled_row(
        self,
        row: AgentEnabledModel,
    ) -> ModelSelection:
        return self._model_selection_from_key(self._selection_key_from_enabled_row(row))

    def _selection_key_from_model_selection(
        self,
        selection: ModelSelection,
    ) -> ModelSelectionKey:
        return self._selection_key(
            source_id=selection.source_id,
            model_provider=selection.model_provider,
            model_name=selection.model_name,
        )

    def _condition_for_selection_on_model(
        self,
        model: type[AgentEnabledModel]
        | type[AgentPreset]
        | type[AgentPresetVersion]
        | type[AgentSession],
        *,
        selection: ModelSelectionKey,
    ) -> Any:
        return (
            (
                model.source_id.is_(None)
                if selection.source_id is None
                else model.source_id == selection.source_id
            )
            & (model.model_provider == selection.model_provider)
            & (model.model_name == selection.model_name)
        )

    def _invalidated_model_name(self, model_name: str) -> str:
        if model_name.endswith(PRUNED_MODEL_NAME_SUFFIX):
            return model_name
        max_name_length = 500 - len(PRUNED_MODEL_NAME_SUFFIX)
        return f"{model_name[:max_name_length]}{PRUNED_MODEL_NAME_SUFFIX}"

    def _catalog_entry_from_row(
        self,
        row: AgentCatalog,
        *,
        enabled: bool,
        source: AgentModelSource | None = None,
        enabled_config: dict[str, Any] | None = None,
    ) -> ModelCatalogEntry:
        source_config = (
            self._deserialize_sensitive_config(source.encrypted_config)
            if source is not None
            else None
        )
        return ModelCatalogEntry(
            model_provider=row.model_provider,
            model_name=row.model_name,
            source_type=self._source_type_from_catalog(row=row, source=source).value,
            source_name=self._source_name_from_catalog(row=row, source=source),
            source_id=row.source_id,
            base_url=(
                self._source_runtime_base_url(source, source_config=source_config)
                if source is not None
                else None
            ),
            enabled=enabled,
            last_refreshed_at=row.last_refreshed_at,
            metadata=row.model_metadata,
            enabled_config=(
                EnabledModelRuntimeConfig.model_validate(enabled_config)
                if enabled_config
                else None
            ),
        )

    def _catalog_entry_from_enabled_row(
        self,
        row: AgentEnabledModel,
        *,
        catalog_row: AgentCatalog,
        source: AgentModelSource | None = None,
    ) -> ModelCatalogEntry:
        metadata: dict[str, Any] | None = None
        runtime_base_url = None
        if source is not None:
            source_config = self._deserialize_sensitive_config(source.encrypted_config)
            if flavor := self._source_flavor(source_config):
                metadata = {"source_flavor": flavor.value}
            runtime_base_url = self._source_runtime_base_url(
                source, source_config=source_config
            )
        return ModelCatalogEntry(
            model_provider=catalog_row.model_provider,
            model_name=catalog_row.model_name,
            source_type=self._source_type_from_catalog(
                row=catalog_row,
                source=source,
            ).value,
            source_name=self._source_name_from_catalog(row=catalog_row, source=source),
            source_id=catalog_row.source_id,
            base_url=runtime_base_url,
            enabled=True,
            last_refreshed_at=row.updated_at,
            metadata=metadata or catalog_row.model_metadata,
            enabled_config=(
                EnabledModelRuntimeConfig.model_validate(row.enabled_config)
                if row.enabled_config
                else None
            ),
        )

    def _resolved_from_builtin(self, row: BuiltInCatalogModel) -> ResolvedCatalogRecord:
        return ResolvedCatalogRecord(
            source_id=None,
            model_provider=row.model_provider,
            model_name=row.model_id,
            source_type=row.source_type,
            source_name=self._provider_label(row.model_provider),
            base_url=None,
            last_refreshed_at=None,
            metadata=row.metadata,
        )

    def _resolved_from_catalog(
        self,
        row: AgentCatalog,
        *,
        source: AgentModelSource | None = None,
    ) -> ResolvedCatalogRecord:
        return ResolvedCatalogRecord(
            source_id=row.source_id,
            model_provider=row.model_provider,
            model_name=row.model_name,
            source_type=self._source_type_from_catalog(row=row, source=source),
            source_name=self._source_name_from_catalog(row=row, source=source),
            base_url=(
                self._source_runtime_base_url(source) if source is not None else None
            ),
            last_refreshed_at=row.last_refreshed_at,
            metadata=row.model_metadata,
        )

    async def _get_platform_setting_value(self, key: str) -> object | None:
        stmt = select(PlatformSetting).where(PlatformSetting.key == key)
        setting = (await self.session.execute(stmt)).scalar_one_or_none()
        if not setting:
            return None
        value_bytes = setting.value
        if setting.is_encrypted:
            value_bytes = decrypt_value(
                value_bytes, key=config.TRACECAT__DB_ENCRYPTION_KEY or ""
            )
        return orjson.loads(value_bytes)

    async def _set_platform_setting_value(
        self, *, key: str, value: object, encrypted: bool = False
    ) -> None:
        stmt = select(PlatformSetting).where(PlatformSetting.key == key)
        setting = (await self.session.execute(stmt)).scalar_one_or_none()
        value_bytes = orjson.dumps(value)
        if encrypted:
            value_bytes = encrypt_value(
                value_bytes, key=config.TRACECAT__DB_ENCRYPTION_KEY or ""
            )
        if setting:
            setting.value = value_bytes
            setting.is_encrypted = encrypted
            setting.value_type = "json"
        else:
            self.session.add(
                PlatformSetting(
                    key=key,
                    value=value_bytes,
                    value_type="json",
                    is_encrypted=encrypted,
                )
            )

    async def _get_org_setting_value(self, key: str) -> object | None:
        setting = await self.settings_service.get_org_setting(key)
        if setting is None:
            return None
        return self.settings_service.get_value(setting)

    async def _set_org_setting_value(self, *, key: str, value: object) -> None:
        if setting := await self.settings_service.get_org_setting(key):
            await self.settings_service.update_org_setting(
                setting, SettingUpdate(value=value)
            )
            return
        await self.settings_service.create_org_setting(
            SettingCreate(
                key=key,
                value=value,
                value_type=ValueType.JSON,
                is_sensitive=False,
            )
        )

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

    async def _upsert_catalog_rows(
        self,
        *,
        source_type: ModelSourceType,
        source_name: str,
        models: list[dict[str, object]],
        source_id: uuid.UUID | None = None,
        organization_scoped: bool,
    ) -> list[AgentCatalog]:
        del source_type, source_name
        if not organization_scoped or source_id is None:
            raise ValueError("Catalog upserts are only supported for custom sources.")

        existing_identities = set(
            (
                await self.session.execute(
                    select(AgentCatalog.model_provider, AgentCatalog.model_name).where(
                        AgentCatalog.organization_id == self.organization_id,
                        AgentCatalog.source_id == source_id,
                    )
                )
            )
            .tuples()
            .all()
        )
        models_by_identity: dict[tuple[str, str], dict[str, object]] = {}
        ordered_identities: list[tuple[str, str]] = []
        now = datetime.now(UTC)

        for model in models:
            model_name = str(model.get("model_name") or model["model_id"])
            model_provider = str(model["model_provider"])
            identity = (model_provider, model_name)
            if identity not in models_by_identity:
                ordered_identities.append(identity)
            models_by_identity[identity] = model

        stale_identities = existing_identities - set(ordered_identities)
        if stale_identities:
            stale_selections = [
                self._selection_key(
                    source_id=source_id,
                    model_provider=model_provider,
                    model_name=model_name,
                )
                for model_provider, model_name in stale_identities
            ]
            await self._prune_stale_model_selections(stale_selections)
            stale_conditions = [
                (AgentCatalog.model_provider == model_provider)
                & (AgentCatalog.model_name == model_name)
                for model_provider, model_name in stale_identities
            ]
            await self.session.execute(
                delete(AgentCatalog).where(
                    AgentCatalog.organization_id == self.organization_id,
                    AgentCatalog.source_id == source_id,
                    or_(*stale_conditions),
                )
            )

        if not models_by_identity:
            return []

        values = [
            {
                "organization_id": self.organization_id,
                "source_id": source_id,
                "model_provider": model_provider,
                "model_name": str(model_name),
                "model_metadata": cast(dict[str, Any] | None, model.get("metadata")),
                "last_refreshed_at": now,
            }
            for model_provider, model_name in ordered_identities
            if (model := models_by_identity.get((model_provider, model_name)))
            is not None
        ]
        stmt = pg_insert(AgentCatalog).values(values)
        stmt = stmt.on_conflict_do_update(
            index_elements=["source_id", "model_provider", "model_name"],
            set_={
                "organization_id": stmt.excluded.organization_id,
                "model_provider": stmt.excluded.model_provider,
                "model_metadata": stmt.excluded.model_metadata,
                "last_refreshed_at": stmt.excluded.last_refreshed_at,
                "updated_at": func.now(),
            },
        )
        await self.session.execute(stmt)
        persisted_rows = (
            (
                await self.session.execute(
                    select(AgentCatalog).where(
                        AgentCatalog.organization_id == self.organization_id,
                        AgentCatalog.source_id == source_id,
                    )
                )
            )
            .scalars()
            .all()
        )
        persisted_by_identity = {
            (row.model_provider, row.model_name): row for row in persisted_rows
        }
        return [
            persisted_by_identity[(model_provider, model_name)]
            for model_provider, model_name in ordered_identities
        ]

    async def _upsert_builtin_catalog_rows(self) -> list[AgentCatalog]:
        builtin_rows = list(get_builtin_catalog_models())
        existing_ids = set(
            (
                await self.session.execute(
                    select(AgentCatalog.id).where(
                        AgentCatalog.organization_id.is_(None),
                    )
                )
            )
            .scalars()
            .all()
        )
        current_ids = {row.agent_catalog_id for row in builtin_rows}
        stale_ids = existing_ids - current_ids
        if stale_ids:
            stale_rows = list(
                (
                    await self.session.execute(
                        select(AgentCatalog).where(
                            AgentCatalog.organization_id.is_(None),
                            AgentCatalog.id.in_(stale_ids),
                        )
                    )
                )
                .scalars()
                .all()
            )
            await self._prune_stale_model_selections(
                [self._selection_key_from_catalog_row(row) for row in stale_rows]
            )
            await self.session.execute(
                delete(AgentCatalog).where(
                    AgentCatalog.organization_id.is_(None),
                    AgentCatalog.id.in_(stale_ids),
                )
            )
        if not builtin_rows:
            return []

        now = datetime.now(UTC)
        stmt = pg_insert(AgentCatalog).values(
            [
                {
                    "id": row.agent_catalog_id,
                    "organization_id": None,
                    "source_id": None,
                    "model_provider": row.model_provider,
                    "model_name": row.model_id,
                    "model_metadata": row.metadata,
                    "last_refreshed_at": now,
                }
                for row in builtin_rows
            ]
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["source_id", "model_provider", "model_name"],
            set_={
                "model_provider": stmt.excluded.model_provider,
                "model_name": stmt.excluded.model_name,
                "model_metadata": stmt.excluded.model_metadata,
                "last_refreshed_at": stmt.excluded.last_refreshed_at,
                "updated_at": func.now(),
            },
        )
        await self.session.execute(stmt)
        persisted_rows = (
            (
                await self.session.execute(
                    select(AgentCatalog).where(
                        AgentCatalog.organization_id.is_(None),
                        AgentCatalog.id.in_(list(current_ids)),
                    )
                )
            )
            .scalars()
            .all()
        )
        persisted_by_id = {row.id: row for row in persisted_rows}
        return [persisted_by_id[row.agent_catalog_id] for row in builtin_rows]

    async def _list_org_catalog_rows(self) -> list[AgentCatalog]:
        stmt = (
            select(AgentCatalog)
            .where(
                AgentCatalog.organization_id == self.organization_id,
            )
            .order_by(AgentCatalog.model_name.asc())
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def _list_builtin_catalog_rows(self) -> list[AgentCatalog]:
        builtin_providers = [
            source_type.value for source_type in _BUILT_IN_PROVIDER_ORDER
        ]
        stmt = (
            select(AgentCatalog)
            .where(
                AgentCatalog.organization_id.is_(None),
                AgentCatalog.model_provider.in_(builtin_providers),
            )
            .order_by(AgentCatalog.model_provider.asc(), AgentCatalog.model_name.asc())
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def _list_org_enabled_rows(self) -> list[AgentEnabledModel]:
        stmt = select(AgentEnabledModel).where(
            AgentEnabledModel.organization_id == self.organization_id,
            AgentEnabledModel.workspace_id.is_(None),
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def _list_workspace_enabled_rows(
        self, workspace_id: uuid.UUID
    ) -> list[AgentEnabledModel]:
        stmt = select(AgentEnabledModel).where(
            AgentEnabledModel.organization_id == self.organization_id,
            AgentEnabledModel.workspace_id == workspace_id,
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def _list_workspace_subset_rows(
        self, workspace_id: uuid.UUID
    ) -> list[AgentEnabledModel]:
        stmt = select(AgentEnabledModel).where(
            AgentEnabledModel.organization_id == self.organization_id,
            AgentEnabledModel.workspace_id == workspace_id,
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def _get_enabled_row(
        self,
        selection: ModelSelectionKey,
    ) -> AgentEnabledModel | None:
        stmt = select(AgentEnabledModel).where(
            AgentEnabledModel.organization_id == self.organization_id,
            AgentEnabledModel.workspace_id.is_(None),
            AgentEnabledModel.source_id == selection.source_id,
            AgentEnabledModel.model_provider == selection.model_provider,
            AgentEnabledModel.model_name == selection.model_name,
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def _get_workspace(self, workspace_id: uuid.UUID) -> Workspace:
        stmt = select(Workspace).where(
            Workspace.id == workspace_id,
            Workspace.organization_id == self.organization_id,
        )
        workspace = (await self.session.execute(stmt)).scalar_one_or_none()
        if workspace is None:
            raise TracecatNotFoundError(f"Workspace {workspace_id} not found")
        return workspace

    async def _ensure_workspace_exists(self, workspace_id: uuid.UUID) -> None:
        await self._get_workspace(workspace_id)

    async def _filter_enabled_rows_for_workspace(
        self,
        rows: list[AgentEnabledModel],
        workspace_id: uuid.UUID | None,
    ) -> list[AgentEnabledModel]:
        if workspace_id is None:
            return rows
        await self._ensure_workspace_exists(workspace_id)
        workspace_rows = await self._list_workspace_subset_rows(workspace_id)
        if not workspace_rows:
            return rows
        allowed_keys = {
            self._selection_key_from_enabled_row(row) for row in workspace_rows
        }
        return [
            row
            for row in rows
            if self._selection_key_from_enabled_row(row) in allowed_keys
        ]

    async def _get_catalog_rows_by_selection(
        self,
        selections: list[ModelSelectionKey],
    ) -> dict[ModelSelectionKey, AgentCatalog]:
        if not selections:
            return {}
        conditions = [
            (
                (
                    AgentCatalog.source_id.is_(None)
                    if key.source_id is None
                    else AgentCatalog.source_id == key.source_id
                )
                & (AgentCatalog.model_provider == key.model_provider)
                & (AgentCatalog.model_name == key.model_name)
            )
            for key in selections
        ]
        stmt = select(AgentCatalog).where(
            (AgentCatalog.organization_id == self.organization_id)
            | AgentCatalog.organization_id.is_(None),
            or_(*conditions),
        )
        rows = list((await self.session.execute(stmt)).scalars().all())
        rows.sort(key=lambda row: row.organization_id is None)
        resolved_by_selection: dict[ModelSelectionKey, AgentCatalog] = {}
        for row in rows:
            resolved_by_selection.setdefault(
                self._selection_key_from_catalog_row(row),
                row,
            )
        return resolved_by_selection

    async def _load_sources_by_id(
        self, source_ids: set[uuid.UUID]
    ) -> dict[uuid.UUID, AgentModelSource]:
        if not source_ids:
            return {}
        stmt = select(AgentModelSource).where(AgentModelSource.id.in_(source_ids))
        return {
            row.id: row for row in (await self.session.execute(stmt)).scalars().all()
        }

    async def _build_enabled_model_entries(
        self,
        rows: list[AgentEnabledModel],
    ) -> list[ModelCatalogEntry]:
        catalog_rows_by_selection = await self._get_catalog_rows_by_selection(
            [self._selection_key_from_enabled_row(row) for row in rows]
        )
        source_ids = {
            catalog_row.source_id
            for catalog_row in catalog_rows_by_selection.values()
            if catalog_row.source_id is not None
        }
        sources_by_id = await self._load_sources_by_id(cast(set[uuid.UUID], source_ids))
        items = [
            self._catalog_entry_from_enabled_row(
                row,
                catalog_row=catalog_row,
                source=(
                    sources_by_id.get(catalog_row.source_id)
                    if catalog_row.source_id is not None
                    else None
                ),
            )
            for row in rows
            if (
                catalog_row := catalog_rows_by_selection.get(
                    self._selection_key_from_enabled_row(row)
                )
            )
            is not None
        ]
        items.sort(key=lambda item: (item.source_name or "", item.model_name))
        return items

    async def _ensure_default_enabled_models(self) -> None:
        if not (
            upgrade_setting := await self.settings_service.get_org_setting(
                ENABLE_ALL_MODELS_ON_UPGRADE_SETTING
            )
        ):
            return None
        rows = (
            (
                await self.session.execute(
                    select(AgentCatalog).where(
                        or_(
                            AgentCatalog.organization_id.is_(None),
                            AgentCatalog.organization_id == self.organization_id,
                        )
                    )
                )
            )
            .scalars()
            .all()
        )
        if not rows:
            self.logger.info(
                "Deferring pre-upgrade enable-all because no catalog rows are available yet",
                organization_id=str(self.organization_id),
            )
            return None

        configured_builtin_providers = {
            provider
            for provider in {
                row.model_provider
                for row in rows
                if row.source_id is None
                and row.model_provider
                in {
                    source_type.value for source_type in _BUILT_IN_PROVIDER_SOURCE_TYPES
                }
            }
            if self._provider_credentials_complete(
                provider=provider,
                credentials=await self.get_provider_credentials(provider),
            )
        }
        enableable_builtin_keys = {
            (row.model_provider, row.model_id)
            for row in get_builtin_catalog_models()
            if row.enableable
        }
        eligible_rows = [
            row
            for row in rows
            if row.source_id is not None
            or (
                row.model_provider in configured_builtin_providers
                and (row.model_provider, row.model_name) in enableable_builtin_keys
            )
        ]
        if not eligible_rows:
            self.logger.info(
                "Deferring pre-upgrade enable-all because no eligible model rows are available yet",
                organization_id=str(self.organization_id),
            )
            return None

        await self.session.execute(
            pg_insert(AgentEnabledModel)
            .values(
                [
                    {
                        "organization_id": self.organization_id,
                        "workspace_id": None,
                        "source_id": row.source_id,
                        "model_provider": row.model_provider,
                        "model_name": row.model_name,
                        "enabled_config": None,
                    }
                    for row in eligible_rows
                ]
            )
            .on_conflict_do_nothing(
                index_elements=[
                    "organization_id",
                    "workspace_id",
                    "source_id",
                    "model_provider",
                    "model_name",
                ]
            )
        )
        await self.session.commit()
        await self.settings_service.delete_org_setting(upgrade_setting)
        self.logger.info(
            "Enabled full model catalog for pre-upgrade organization",
            organization_id=str(self.organization_id),
            enabled_rows=len(eligible_rows),
        )

    async def prune_unconfigured_builtin_model_selections(
        self,
    ) -> set[ModelSelectionKey]:
        """Disable built-in model selections when the provider credentials are absent."""
        builtin_rows = [
            row
            for row in await self._list_org_enabled_rows()
            if row.source_id is None
            and row.model_provider
            in {source_type.value for source_type in _BUILT_IN_PROVIDER_SOURCE_TYPES}
        ]
        if not builtin_rows:
            return set()

        configured_builtin_providers = {
            provider
            for provider in {row.model_provider for row in builtin_rows}
            if self._provider_credentials_complete(
                provider=provider,
                credentials=await self.get_provider_credentials(provider),
            )
        }
        stale_selections = [
            self._selection_key_from_enabled_row(row)
            for row in builtin_rows
            if row.model_provider not in configured_builtin_providers
        ]
        if not stale_selections:
            return set()

        disabled = await self._disable_model_selections(stale_selections)
        self.logger.info(
            "Pruned builtin model selections for unconfigured providers",
            organization_id=str(self.organization_id),
            disabled_rows=len(disabled),
        )
        return disabled

    async def _list_provider_rows(self, provider: str) -> list[AgentCatalog]:
        stmt = (
            select(AgentCatalog)
            .where(
                AgentCatalog.organization_id.is_(None),
                AgentCatalog.model_provider == provider,
            )
            .order_by(AgentCatalog.model_name.asc())
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def _get_provider_state(
        self, provider: str
    ) -> tuple[ModelDiscoveryStatus, datetime | None, str | None]:
        del provider
        status, refreshed_at, _last_error = await self._get_builtin_catalog_state()
        return status, refreshed_at, None

    def _provider_base_url_key(self, provider: str) -> str | None:
        match provider:
            case "openai":
                return "OPENAI_BASE_URL"
            case "anthropic":
                return "ANTHROPIC_BASE_URL"
            case "azure_openai" | "azure_ai":
                return "AZURE_API_BASE"
            case _:
                return None

    def _provider_runtime_target(
        self, *, provider: str, credentials: dict[str, str] | None
    ) -> str | None:
        if not credentials:
            return None
        match provider:
            case "bedrock":
                return credentials.get("AWS_INFERENCE_PROFILE_ID") or credentials.get(
                    "AWS_MODEL_ID"
                )
            case "vertex_ai":
                return credentials.get("VERTEX_AI_MODEL")
            case "azure_openai":
                return credentials.get("AZURE_DEPLOYMENT_NAME")
            case "azure_ai":
                return credentials.get("AZURE_AI_MODEL_NAME")
            case _:
                return None

    def _provider_credentials_complete(
        self, *, provider: str, credentials: dict[str, str] | None
    ) -> bool:
        if not credentials:
            return False
        match provider:
            case "openai":
                return bool(credentials.get("OPENAI_API_KEY"))
            case "anthropic":
                return bool(credentials.get("ANTHROPIC_API_KEY"))
            case "gemini":
                return bool(credentials.get("GEMINI_API_KEY"))
            case "bedrock":
                return bool(credentials.get("AWS_REGION"))
            case "vertex_ai":
                return bool(
                    credentials.get("GOOGLE_API_CREDENTIALS")
                    and credentials.get("GOOGLE_CLOUD_PROJECT")
                )
            case "azure_openai":
                return bool(
                    credentials.get("AZURE_API_BASE")
                    and credentials.get("AZURE_API_VERSION")
                    and (
                        credentials.get("AZURE_API_KEY")
                        or credentials.get("AZURE_AD_TOKEN")
                    )
                )
            case "azure_ai":
                return bool(
                    credentials.get("AZURE_API_BASE")
                    and credentials.get("AZURE_API_KEY")
                )
            case _:
                return True

    def _build_builtin_catalog_entry(
        self,
        *,
        row: AgentCatalog,
        enabled_rows_by_key: dict[ModelSelectionKey, AgentEnabledModel],
        provider_status: ModelDiscoveryStatus,
        credentials: dict[str, str] | None,
    ) -> BuiltInCatalogEntry:
        provider = row.model_provider
        credential_config = PROVIDER_CREDENTIAL_CONFIGS[provider]
        credentials_configured = self._provider_credentials_complete(
            provider=provider,
            credentials=credentials,
        )
        metadata = row.model_metadata or {}
        enableable, enableable_readiness_message = _is_agent_enableable(metadata)
        ready = enableable and credentials_configured
        if enableable_readiness_message is not None:
            readiness_message = enableable_readiness_message
        elif not credentials_configured:
            readiness_message = (
                f"Configure {credential_config.label} credentials to enable this model."
            )
        else:
            readiness_message = None
        enabled_row = enabled_rows_by_key.get(self._selection_key_from_catalog_row(row))
        return BuiltInCatalogEntry(
            model_provider=row.model_provider,
            model_name=row.model_name,
            source_type=ModelSourceType(row.model_provider).value,
            source_name=self._provider_label(row.model_provider),
            source_id=None,
            enabled=enabled_row is not None,
            last_refreshed_at=row.last_refreshed_at,
            metadata=row.model_metadata,
            enabled_config=(
                EnabledModelRuntimeConfig.model_validate(enabled_row.enabled_config)
                if enabled_row and enabled_row.enabled_config
                else None
            ),
            credential_provider=provider,
            credential_label=credential_config.label,
            credential_fields=credential_config.fields,
            credentials_configured=credentials_configured,
            discovered=provider_status == ModelDiscoveryStatus.READY,
            ready=ready,
            enableable=ready,
            runtime_target_configured=True,
            readiness_message=readiness_message,
        )

    async def _build_provider_read(
        self, source_type: ModelSourceType
    ) -> BuiltInProviderRead:
        provider = source_type.value
        credentials = await self.get_provider_credentials(provider)
        (
            discovery_status,
            last_refreshed_at,
            last_error,
        ) = await self._get_provider_state(provider)
        enabled_keys = {
            self._selection_key_from_enabled_row(row)
            for row in await self._list_org_enabled_rows()
        }
        base_url = None
        if credentials and (base_url_key := self._provider_base_url_key(provider)):
            base_url = credentials.get(base_url_key)
        rows = await self._list_provider_rows(provider)
        provider_source_type = source_type
        return BuiltInProviderRead(
            provider=provider,
            label=PROVIDER_CREDENTIAL_CONFIGS[provider].label,
            source_type=provider_source_type,
            credentials_configured=self._provider_credentials_complete(
                provider=provider,
                credentials=credentials,
            ),
            base_url=base_url,
            runtime_target=self._provider_runtime_target(
                provider=provider,
                credentials=credentials,
            ),
            discovery_status=discovery_status,
            last_refreshed_at=last_refreshed_at,
            last_error=last_error,
            discovered_models=[
                self._catalog_entry_from_row(
                    row,
                    enabled=self._selection_key_from_catalog_row(row) in enabled_keys,
                )
                for row in rows
            ],
        )

    async def list_providers(self) -> list[BuiltInProviderRead]:
        """List built-in providers with discovery and credential state."""
        return [
            await self._build_provider_read(source_type)
            for source_type in _BUILT_IN_PROVIDER_ORDER
        ]

    async def _get_builtin_catalog_state(
        self,
    ) -> tuple[ModelDiscoveryStatus, datetime | None, str | None]:
        status = await self._get_platform_setting_value(
            BUILTIN_CATALOG_STATE_SETTINGS["discovery_status"]
        )
        refreshed_at = await self._get_platform_setting_value(
            BUILTIN_CATALOG_STATE_SETTINGS["last_refreshed_at"]
        )
        last_error = await self._get_platform_setting_value(
            BUILTIN_CATALOG_STATE_SETTINGS["last_error"]
        )
        return (
            ModelDiscoveryStatus(str(status or ModelDiscoveryStatus.NEVER.value)),
            datetime.fromisoformat(str(refreshed_at)) if refreshed_at else None,
            str(last_error) if last_error else None,
        )

    async def list_builtin_catalog(
        self,
        *,
        query: str | None = None,
        provider: str | None = None,
        cursor: str | None = None,
        limit: int = 100,
    ) -> BuiltInCatalogRead:
        start = _parse_catalog_offset(cursor)
        status, refreshed_at, last_error = await self._get_builtin_catalog_state()
        enabled_rows_by_key = {
            self._selection_key_from_enabled_row(row): row
            for row in await self._list_org_enabled_rows()
        }
        provider_statuses = {
            provider.value: (await self._get_provider_state(provider.value))[0]
            for provider in _BUILT_IN_PROVIDER_ORDER
        }
        credentials_by_provider = {
            provider.value: await self.get_provider_credentials(provider.value)
            for provider in _BUILT_IN_PROVIDER_ORDER
        }
        items = [
            self._build_builtin_catalog_entry(
                row=row,
                enabled_rows_by_key=enabled_rows_by_key,
                provider_status=provider_statuses[row.model_provider],
                credentials=credentials_by_provider[row.model_provider],
            )
            for row in await self._list_builtin_catalog_rows()
        ]
        normalized_query = query.strip().lower() if query else None
        if normalized_query:
            items = [
                item
                for item in items
                if normalized_query in item.model_name.lower()
                or normalized_query in item.model_provider.lower()
            ]
        if provider:
            items = [item for item in items if item.model_provider == provider]
        provider_order = {
            item.value: index for index, item in enumerate(_BUILT_IN_PROVIDER_ORDER)
        }
        items.sort(
            key=lambda item: (
                provider_order.get(item.model_provider, len(provider_order)),
                -int(bool(item.enabled)),
                item.model_name.lower(),
            )
        )
        bounded_limit = max(1, min(limit, 200))
        page = items[start : start + bounded_limit]
        next_cursor = (
            str(start + bounded_limit) if start + bounded_limit < len(items) else None
        )
        return BuiltInCatalogRead(
            discovery_status=status,
            last_refreshed_at=refreshed_at,
            last_error=last_error,
            next_cursor=next_cursor,
            models=page,
        )

    async def refresh_builtin_catalog(self) -> BuiltInCatalogRead:
        try:
            await self._upsert_builtin_catalog_rows()
            await self._set_platform_setting_value(
                key=BUILTIN_CATALOG_STATE_SETTINGS["discovery_status"],
                value=ModelDiscoveryStatus.READY.value,
            )
            await self._set_platform_setting_value(
                key=BUILTIN_CATALOG_STATE_SETTINGS["last_refreshed_at"],
                value=datetime.now(UTC).isoformat(),
            )
            await self._set_platform_setting_value(
                key=BUILTIN_CATALOG_STATE_SETTINGS["last_error"],
                value=None,
            )
            await self.session.commit()
        except Exception as exc:
            await self.session.rollback()
            await self._set_platform_setting_value(
                key=BUILTIN_CATALOG_STATE_SETTINGS["discovery_status"],
                value=ModelDiscoveryStatus.FAILED.value,
            )
            await self._set_platform_setting_value(
                key=BUILTIN_CATALOG_STATE_SETTINGS["last_refreshed_at"],
                value=datetime.now(UTC).isoformat(),
            )
            await self._set_platform_setting_value(
                key=BUILTIN_CATALOG_STATE_SETTINGS["last_error"],
                value=str(exc),
            )
            await self.session.commit()
            raise
        return await self.list_builtin_catalog()

    async def list_models(
        self, *, workspace_id: uuid.UUID | None = None
    ) -> list[ModelCatalogEntry]:
        """List the merged enabled model catalog for the organization."""
        if workspace_id is not None:
            await self._ensure_workspace_exists(workspace_id)
        enabled_rows = await self._filter_enabled_rows_for_workspace(
            await self._list_org_enabled_rows(), workspace_id
        )
        return await self._build_enabled_model_entries(enabled_rows)

    async def list_discovered_models(self) -> list[ModelCatalogEntry]:
        """List the discovered catalog with org enablement flags."""
        discovered = await self._list_org_catalog_rows()
        enabled_keys = {
            self._selection_key_from_enabled_row(row)
            for row in await self._list_org_enabled_rows()
        }
        sources_by_id = await self._load_sources_by_id(
            {row.source_id for row in discovered if row.source_id is not None}
        )
        items = [
            self._catalog_entry_from_row(
                row,
                enabled=self._selection_key_from_catalog_row(row) in enabled_keys,
                source=sources_by_id.get(row.source_id) if row.source_id else None,
            )
            for row in discovered
        ]
        items.sort(key=lambda item: (item.source_name or "", item.model_name))
        return items

    @require_scope("workspace:read")
    async def get_workspace_model_subset(
        self, workspace_id: uuid.UUID
    ) -> WorkspaceModelSubsetRead:
        await self._ensure_workspace_exists(workspace_id)
        rows = await self._list_workspace_subset_rows(workspace_id)
        return WorkspaceModelSubsetRead(
            inherit_all=not rows,
            models=sorted(
                (
                    ModelSelection(
                        source_id=row.source_id,
                        model_provider=row.model_provider,
                        model_name=row.model_name,
                    )
                    for row in rows
                ),
                key=lambda item: (
                    str(item.source_id) if item.source_id is not None else "",
                    item.model_provider,
                    item.model_name,
                ),
            ),
        )

    @require_scope("workspace:update")
    async def replace_workspace_model_subset(
        self,
        workspace_id: uuid.UUID,
        params: WorkspaceModelSubsetUpdate,
    ) -> WorkspaceModelSubsetRead:
        await self._ensure_workspace_exists(workspace_id)
        unique_models = list(
            dict.fromkeys(
                self._selection_key(
                    source_id=item.source_id,
                    model_provider=item.model_provider,
                    model_name=item.model_name,
                )
                for item in params.models
            )
        )
        await self.session.execute(
            delete(AgentEnabledModel).where(
                AgentEnabledModel.organization_id == self.organization_id,
                AgentEnabledModel.workspace_id == workspace_id,
            )
        )
        if params.inherit_all:
            await self.session.commit()
            return await self.get_workspace_model_subset(workspace_id)
        if not unique_models:
            raise ValueError(
                "Workspace subsets must include at least one model when inherit_all is false."
            )
        org_enabled_keys = {
            self._selection_key_from_enabled_row(row)
            for row in await self._list_org_enabled_rows()
        }
        missing_models = [
            model for model in unique_models if model not in org_enabled_keys
        ]
        if missing_models:
            raise TracecatNotFoundError(
                "Model "
                f"{missing_models[0].model_provider}/{missing_models[0].model_name} "
                "is not enabled for the organization"
            )
        await self.session.execute(
            pg_insert(AgentEnabledModel).values(
                [
                    {
                        "organization_id": self.organization_id,
                        "workspace_id": workspace_id,
                        "source_id": model.source_id,
                        "model_provider": model.model_provider,
                        "model_name": model.model_name,
                        "enabled_config": None,
                    }
                    for model in unique_models
                ]
            )
        )
        await self.session.commit()
        return await self.get_workspace_model_subset(workspace_id)

    @require_scope("workspace:update")
    async def clear_workspace_model_subset(self, workspace_id: uuid.UUID) -> None:
        await self._ensure_workspace_exists(workspace_id)
        await self.session.execute(
            delete(AgentEnabledModel).where(
                AgentEnabledModel.organization_id == self.organization_id,
                AgentEnabledModel.workspace_id == workspace_id,
            )
        )
        await self.session.commit()

    async def get_model_config(self, model_name: str) -> ModelConfig:
        """Get configuration for a specific model."""
        if model_name not in MODEL_CONFIGS:
            raise TracecatNotFoundError(f"Model {model_name} not found")
        return MODEL_CONFIGS[model_name]

    def _source_type_from_row(self, row: AgentModelSource) -> CustomModelSourceType:
        if row.declared_models:
            return CustomModelSourceType.MANUAL_CUSTOM
        if row.model_provider == CustomModelSourceType.OPENAI_COMPATIBLE_GATEWAY.value:
            return CustomModelSourceType.OPENAI_COMPATIBLE_GATEWAY
        return CustomModelSourceType.MANUAL_CUSTOM

    def _to_model_source_read(self, row: AgentModelSource) -> AgentModelSourceRead:
        source_config = self._deserialize_sensitive_config(row.encrypted_config)
        return AgentModelSourceRead(
            id=row.id,
            type=self._source_type_from_row(row),
            flavor=self._source_flavor(source_config),
            display_name=row.display_name,
            base_url=row.base_url,
            api_key_configured=bool(source_config.get("api_key")),
            api_key_header=row.api_key_header,
            api_version=row.api_version,
            discovery_status=ModelDiscoveryStatus(
                row.discovery_status or ModelDiscoveryStatus.NEVER.value
            ),
            last_refreshed_at=row.last_refreshed_at,
            last_error=row.last_error,
            declared_models=(
                [
                    ManualDiscoveredModel.model_validate(item)
                    for item in row.declared_models
                ]
                if row.declared_models
                else None
            ),
        )

    async def list_model_sources(self) -> list[AgentModelSourceRead]:
        stmt = (
            select(AgentModelSource)
            .where(
                AgentModelSource.organization_id == self.organization_id,
            )
            .order_by(AgentModelSource.display_name.asc())
        )
        rows = (await self.session.execute(stmt)).scalars().all()
        return [self._to_model_source_read(row) for row in rows]

    async def list_provider_credential_configs(
        self,
    ) -> list[ProviderCredentialConfig]:
        """List credential field configurations for built-in providers."""
        return [
            PROVIDER_CREDENTIAL_CONFIGS[source_type.value]
            for source_type in _BUILT_IN_PROVIDER_ORDER
            if source_type.value in PROVIDER_CREDENTIAL_CONFIGS
        ]

    async def get_provider_credential_config(
        self, provider: str
    ) -> ProviderCredentialConfig:
        """Get credential configuration for a specific provider."""
        self._ensure_builtin_provider(provider)
        return PROVIDER_CREDENTIAL_CONFIGS[provider]

    async def _validate_source_uniqueness(
        self,
        *,
        source_type: CustomModelSourceType,
        exclude_id: uuid.UUID | None = None,
    ) -> None:
        if source_type.value not in {
            custom_source_type.value for custom_source_type in _CUSTOM_SOURCE_TYPES
        }:
            raise ValueError(
                f"{source_type.value} is a built-in inventory input, not a user-created custom source."
            )
        del exclude_id

    async def get_model_source(self, source_id: uuid.UUID) -> AgentModelSource:
        stmt = select(AgentModelSource).where(
            AgentModelSource.organization_id == self.organization_id,
            AgentModelSource.id == source_id,
        )
        source = (await self.session.execute(stmt)).scalar_one_or_none()
        if source is None:
            raise TracecatNotFoundError(f"Source {source_id} not found")
        return source

    @require_scope("agent:update")
    async def create_model_source(
        self, params: AgentModelSourceCreate
    ) -> AgentModelSourceRead:
        await self._validate_source_uniqueness(source_type=params.type)
        source = AgentModelSource(
            organization_id=self.organization_id,
            display_name=params.display_name,
            model_provider=(
                "openai_compatible_gateway"
                if params.type == CustomModelSourceType.OPENAI_COMPATIBLE_GATEWAY
                else None
            ),
            base_url=params.base_url,
            encrypted_config=self._serialize_sensitive_config(
                self._source_config_payload(
                    api_key=params.api_key,
                    flavor=params.flavor,
                )
            ),
            api_key_header=params.api_key_header,
            api_version=params.api_version,
            declared_models=(
                [item.model_dump(mode="json") for item in params.declared_models]
                if params.declared_models
                else None
            ),
        )
        self.session.add(source)
        await self.session.commit()
        await self.session.refresh(source)
        return self._to_model_source_read(source)

    @require_scope("agent:update")
    async def update_model_source(
        self, source_id: uuid.UUID, params: AgentModelSourceUpdate
    ) -> AgentModelSourceRead:
        source = await self.get_model_source(source_id)
        await self._validate_source_uniqueness(
            source_type=self._source_type_from_row(source),
            exclude_id=source.id,
        )
        source_config = self._deserialize_sensitive_config(source.encrypted_config)
        if "display_name" in params.model_fields_set:
            source.display_name = params.display_name or source.display_name
        if "base_url" in params.model_fields_set:
            source.base_url = params.base_url
            source_config.pop("runtime_base_url", None)
        if "api_key" in params.model_fields_set:
            if params.api_key:
                source_config["api_key"] = params.api_key
            else:
                source_config.pop("api_key", None)
        if "flavor" in params.model_fields_set:
            if params.flavor is None:
                source_config.pop("flavor", None)
            else:
                source_config["flavor"] = params.flavor.value
        if "api_key_header" in params.model_fields_set:
            source.api_key_header = params.api_key_header
        if "api_version" in params.model_fields_set:
            source.api_version = params.api_version
        if "declared_models" in params.model_fields_set:
            source.declared_models = (
                [item.model_dump(mode="json") for item in params.declared_models]
                if params.declared_models
                else None
            )
        source.encrypted_config = self._serialize_sensitive_config(source_config)
        self.session.add(source)
        await self.session.commit()
        await self._ensure_default_enabled_models()
        await self.session.refresh(source)
        refreshed = await self.get_model_source(source.id)
        return self._to_model_source_read(refreshed)

    @require_scope("agent:update")
    async def delete_model_source(self, source_id: uuid.UUID) -> None:
        source = await self.get_model_source(source_id)
        await self._validate_source_uniqueness(
            source_type=self._source_type_from_row(source),
            exclude_id=source.id,
        )
        source_catalog_rows = list(
            (
                await self.session.execute(
                    select(AgentCatalog).where(
                        AgentCatalog.organization_id == self.organization_id,
                        AgentCatalog.source_id == source_id,
                    )
                )
            )
            .scalars()
            .all()
        )
        source_keys = [
            self._selection_key_from_catalog_row(row) for row in source_catalog_rows
        ]
        if source_keys:
            await self._prune_stale_model_selections(source_keys)
            await self.session.flush()
        await self.session.delete(source)
        await self.session.commit()

    def _build_auth_headers(
        self, *, api_key: str | None, api_key_header: str | None
    ) -> dict[str, str]:
        if not api_key:
            return {}
        header_name = api_key_header or "Authorization"
        if header_name.lower() == "authorization":
            return {header_name: f"Bearer {api_key}"}
        return {header_name: api_key}

    def _openai_compatible_discovery_urls(self, base_url: str) -> list[str]:
        base = base_url.strip().rstrip("/")
        if not base:
            return []

        candidates: list[str] = []

        def add(url: str) -> None:
            cleaned = url.strip()
            if cleaned and cleaned not in candidates:
                candidates.append(cleaned)

        add(base)
        add(f"{base}/")

        if base.endswith("/v1/models"):
            prefix = base.removesuffix("/v1/models")
            add(f"{base}/")
            add(f"{prefix}/v1")
            add(f"{prefix}/v1/")
            add(f"{prefix}/models")
            add(f"{prefix}/models/")
            add(prefix)
            add(f"{prefix}/")
        elif base.endswith("/models"):
            prefix = base.removesuffix("/models")
            add(f"{base}/")
            add(f"{prefix}/v1/models")
            add(f"{prefix}/v1/models/")
            add(f"{prefix}/v1")
            add(f"{prefix}/v1/")
            add(prefix)
            add(f"{prefix}/")
        elif base.endswith("/v1"):
            prefix = base.removesuffix("/v1")
            add(f"{base}/")
            add(f"{base}/models")
            add(f"{base}/models/")
            add(f"{prefix}/models")
            add(f"{prefix}/models/")
            add(prefix)
            add(f"{prefix}/")
        else:
            add(f"{base}/v1")
            add(f"{base}/v1/")
            add(f"{base}/v1/models")
            add(f"{base}/v1/models/")
            add(f"{base}/models")
            add(f"{base}/models/")

        return candidates

    async def _fetch_openai_compatible_models(
        self,
        *,
        base_url: str,
        api_key: str | None,
        api_key_header: str | None,
        extra_headers: dict[str, str] | None = None,
    ) -> OpenAICompatibleDiscoveryResult:
        headers = {
            **self._build_auth_headers(
                api_key=api_key,
                api_key_header=api_key_header,
            ),
            **(extra_headers or {}),
        }
        async with httpx.AsyncClient(timeout=20.0) as client:
            errors: list[str] = []
            for url in self._openai_compatible_discovery_urls(base_url):
                sanitized_url = _sanitize_url_for_log(url)
                try:
                    response = await client.get(url, headers=headers)
                    response.raise_for_status()
                except httpx.HTTPStatusError as exc:
                    errors.append(f"{sanitized_url} -> {exc.response.status_code}")
                    continue
                except httpx.RequestError as exc:
                    errors.append(f"{sanitized_url} -> {exc.__class__.__name__}")
                    continue

                try:
                    payload = response.json()
                except json.JSONDecodeError:
                    errors.append(f"{sanitized_url} -> invalid JSON")
                    continue

                if isinstance(payload, dict):
                    items = payload.get("data") or payload.get("models") or []
                else:
                    items = payload

                if not isinstance(items, list):
                    errors.append(f"{sanitized_url} -> unexpected payload")
                    continue

                return OpenAICompatibleDiscoveryResult(
                    items=[item for item in items if isinstance(item, dict)],
                    runtime_base_url=self._openai_compatible_runtime_base_url(url),
                )

        detail = ", ".join(errors[:4]) if errors else "no discovery endpoints tried"
        self.logger.warning(
            "OpenAI-compatible model discovery failed",
            organization_id=str(self.organization_id),
            base_url=_sanitize_url_for_log(base_url),
            detail=detail,
        )
        raise TracecatNotFoundError("Failed to discover models from gateway")

    def _format_model_source_refresh_error(self, exc: Exception) -> str:
        match exc:
            case TracecatNotFoundError():
                return str(exc)
            case httpx.TimeoutException():
                return "Timed out while contacting the custom source."
            case httpx.RequestError():
                return "Failed to connect to the custom source."
            case ValueError() | json.JSONDecodeError():
                return "The custom source returned an invalid discovery response."
            case _:
                return "Failed to refresh the custom source."

    def _normalize_openai_compatible_entries(
        self,
        *,
        source_type: ModelSourceType,
        source_name: str,
        items: list[dict[str, object]],
        source_id: uuid.UUID | None = None,
        base_url: str | None = None,
    ) -> list[dict[str, object]]:
        normalized: list[dict[str, object]] = []
        for item in items:
            model_id = str(item.get("id") or item.get("name") or "").strip()
            if not model_id:
                continue
            provider = self._display_provider_for_model(
                source_type=source_type,
                model_id=model_id,
                provider_hint=(
                    str(item["owned_by"])
                    if isinstance(item.get("owned_by"), str)
                    else str(item["provider"])
                    if isinstance(item.get("provider"), str)
                    else None
                ),
            )
            normalized.append(
                {
                    "model_provider": provider,
                    "model_id": model_id,
                    "display_name": str(item.get("name") or model_id),
                    "metadata": item,
                }
            )
        return normalized

    async def _discover_source_models(
        self, source: AgentModelSource
    ) -> SourceDiscoveryResult:
        source_type = (
            ModelSourceType.OPENAI_COMPATIBLE_GATEWAY
            if self._source_type_from_row(source)
            == CustomModelSourceType.OPENAI_COMPATIBLE_GATEWAY
            else ModelSourceType.MANUAL_CUSTOM
        )
        source_config = self._deserialize_sensitive_config(source.encrypted_config)
        match source_type:
            case ModelSourceType.OPENAI_COMPATIBLE_GATEWAY:
                if not source.base_url:
                    raise TracecatNotFoundError("Gateway source requires a base URL")
                discovery = await self._fetch_openai_compatible_models(
                    base_url=source.base_url,
                    api_key=source_config.get("api_key"),
                    api_key_header=source.api_key_header,
                )
                return SourceDiscoveryResult(
                    models=self._normalize_openai_compatible_entries(
                        source_type=source_type,
                        source_name=source.display_name,
                        items=discovery.items,
                        source_id=source.id,
                        base_url=discovery.runtime_base_url,
                    ),
                    runtime_base_url=discovery.runtime_base_url,
                )
            case ModelSourceType.MANUAL_CUSTOM:
                declared = source.declared_models or []
                return SourceDiscoveryResult(
                    models=[
                        {
                            "model_provider": item.get("model_provider")
                            or self._display_provider_for_model(
                                source_type=source_type,
                                model_id=item["model_name"],
                                provider_hint=None,
                            ),
                            "model_id": item["model_name"],
                            "display_name": item.get("display_name")
                            or item["model_name"],
                            "metadata": {
                                "declared": True,
                                "display_name": item.get("display_name")
                                or item["model_name"],
                            },
                        }
                        for item in declared
                    ],
                    runtime_base_url=source.base_url,
                )
            case _:
                raise TracecatNotFoundError(
                    f"{source_type.value} is a built-in provider inventory, not a custom source."
                )

    @require_scope("agent:update")
    async def refresh_model_source(
        self, source_id: uuid.UUID
    ) -> list[ModelCatalogEntry]:
        source = await self.get_model_source(source_id)
        await self._validate_source_uniqueness(
            source_type=self._source_type_from_row(source),
            exclude_id=source.id,
        )
        try:
            discovery = await self._discover_source_models(source)
            source_config = self._deserialize_sensitive_config(source.encrypted_config)
            if discovery.runtime_base_url:
                source_config["runtime_base_url"] = discovery.runtime_base_url
            else:
                source_config.pop("runtime_base_url", None)
            source.encrypted_config = self._serialize_sensitive_config(source_config)
            persisted = await self._upsert_catalog_rows(
                source_type=(
                    ModelSourceType.OPENAI_COMPATIBLE_GATEWAY
                    if self._source_type_from_row(source)
                    == CustomModelSourceType.OPENAI_COMPATIBLE_GATEWAY
                    else ModelSourceType.MANUAL_CUSTOM
                ),
                source_name=source.display_name,
                source_id=source.id,
                models=discovery.models,
                organization_scoped=True,
            )
            source.discovery_status = ModelDiscoveryStatus.READY.value
            source.last_error = None
            source.last_refreshed_at = datetime.now(UTC)
            self.session.add(source)
            await self.session.commit()
            await self._ensure_default_enabled_models()
            enabled_keys = {
                self._selection_key_from_enabled_row(row)
                for row in await self._list_org_enabled_rows()
            }
            return [
                self._catalog_entry_from_row(
                    row,
                    enabled=self._selection_key_from_catalog_row(row) in enabled_keys,
                    source=source,
                )
                for row in persisted
            ]
        except Exception as exc:
            await self.session.rollback()
            source.discovery_status = ModelDiscoveryStatus.FAILED.value
            source.last_error = self._format_model_source_refresh_error(exc)
            source.last_refreshed_at = datetime.now(UTC)
            self.session.add(source)
            await self.session.commit()
            raise

    @require_scope("agent:update")
    async def refresh_provider_inventory(self, provider: str) -> BuiltInProviderRead:
        try:
            source_type = ModelSourceType(provider)
        except ValueError as exc:
            raise TracecatNotFoundError(f"Provider {provider} not found") from exc
        if source_type not in _BUILT_IN_PROVIDER_SOURCE_TYPES:
            raise TracecatNotFoundError(f"Provider {provider} not found")
        return await self._build_provider_read(source_type)

    async def _get_catalog_row_model(
        self,
        selection: ModelSelectionKey,
    ) -> AgentCatalog:
        stmt = (
            select(AgentCatalog)
            .where(
                (
                    AgentCatalog.source_id.is_(None)
                    if selection.source_id is None
                    else AgentCatalog.source_id == selection.source_id
                ),
                AgentCatalog.model_provider == selection.model_provider,
                AgentCatalog.model_name == selection.model_name,
                (AgentCatalog.organization_id == self.organization_id)
                | AgentCatalog.organization_id.is_(None),
            )
            .order_by(AgentCatalog.organization_id.is_(None))
        )
        row = (await self.session.execute(stmt)).scalar_one_or_none()
        if row is not None:
            return row
        raise TracecatNotFoundError(
            f"Catalog entry {selection.model_provider}/{selection.model_name} not found"
        )

    async def _get_catalog_row(
        self,
        selection: ModelSelectionKey,
    ) -> ResolvedCatalogRecord:
        row = await self._get_catalog_row_model(selection)
        source = (
            await self.get_model_source(row.source_id)
            if row.source_id is not None
            else None
        )
        return self._resolved_from_catalog(row, source=source)

    async def _resolve_enableable_catalog_row(
        self,
        selection: ModelSelectionKey,
        provider_credentials_cache: dict[str, dict[str, str] | None],
    ) -> ResolvedCatalogRecord:
        row = await self._get_catalog_row(selection)
        if row.source_id is None:
            builtin_row = get_builtin_catalog_by_provider().get(row.model_provider, ())
            builtin_model = next(
                (item for item in builtin_row if item.model_id == row.model_name),
                None,
            )
            if builtin_model is not None:
                provider = builtin_model.model_provider
                if provider not in provider_credentials_cache:
                    provider_credentials_cache[
                        provider
                    ] = await self.get_provider_credentials(provider)
                credentials = provider_credentials_cache[provider]
                if not builtin_model.enableable:
                    raise TracecatNotFoundError(
                        builtin_model.readiness_message
                        or "This model cannot be enabled for agents."
                    )
                if not self._provider_credentials_complete(
                    provider=provider,
                    credentials=credentials,
                ):
                    raise TracecatNotFoundError(
                        f"No complete credentials found for provider '{provider}'. "
                        "Please configure this provider first."
                    )
        return row

    async def _enable_catalog_rows(
        self,
        selections: list[ModelSelectionKey],
    ) -> list[ResolvedCatalogRecord]:
        provider_credentials_cache: dict[str, dict[str, str] | None] = {}
        unique_selections = list(dict.fromkeys(selections))
        rows = [
            await self._resolve_enableable_catalog_row(
                selection, provider_credentials_cache
            )
            for selection in unique_selections
        ]
        if not rows:
            return []
        await self.session.execute(
            pg_insert(AgentEnabledModel)
            .values(
                [
                    {
                        "organization_id": self.organization_id,
                        "workspace_id": None,
                        "source_id": row.source_id,
                        "model_provider": row.model_provider,
                        "model_name": row.model_name,
                        "enabled_config": None,
                    }
                    for row in rows
                ]
            )
            .on_conflict_do_nothing(
                index_elements=[
                    "organization_id",
                    "workspace_id",
                    "source_id",
                    "model_provider",
                    "model_name",
                ]
            )
        )
        await self.session.commit()
        return rows

    @require_scope("agent:update")
    @requires_entitlement(Entitlement.AGENT_ADDONS)
    async def enable_model(self, params: EnabledModelOperation) -> ModelCatalogEntry:
        selection = self._selection_key_from_model_selection(params)
        await self._enable_catalog_rows([selection])
        enabled_row = await self._get_enabled_row(selection)
        if enabled_row is None:
            raise TracecatNotFoundError(
                f"Enabled model {params.model_provider}/{params.model_name} not found"
            )
        return (await self._build_enabled_model_entries([enabled_row]))[0]

    @require_scope("agent:update")
    @requires_entitlement(Entitlement.AGENT_ADDONS)
    async def enable_models(
        self, params: EnabledModelsBatchOperation
    ) -> list[ModelCatalogEntry]:
        selections = [
            self._selection_key_from_model_selection(item) for item in params.models
        ]
        await self._enable_catalog_rows(selections)
        selection_set = set(selections)
        enabled_rows = [
            row
            for row in await self._list_org_enabled_rows()
            if self._selection_key_from_enabled_row(row) in selection_set
        ]
        return await self._build_enabled_model_entries(enabled_rows)

    async def _disable_model_selections(
        self,
        selections: list[ModelSelectionKey],
    ) -> set[ModelSelectionKey]:
        disabled = await self._prune_stale_model_selections(selections)
        await self.session.commit()
        return disabled

    async def _prune_stale_model_selections(
        self,
        selections: list[ModelSelectionKey],
    ) -> set[ModelSelectionKey]:
        unique_selections = list(dict.fromkeys(selections))
        if not unique_selections:
            return set()

        conditions = [
            (AgentEnabledModel.organization_id == self.organization_id)
            & (
                AgentEnabledModel.source_id.is_(None)
                if selection.source_id is None
                else AgentEnabledModel.source_id == selection.source_id
            )
            & (AgentEnabledModel.model_provider == selection.model_provider)
            & (AgentEnabledModel.model_name == selection.model_name)
            for selection in unique_selections
        ]
        result = await self.session.execute(
            delete(AgentEnabledModel)
            .where(or_(*conditions))
            .returning(
                AgentEnabledModel.source_id,
                AgentEnabledModel.model_provider,
                AgentEnabledModel.model_name,
            )
        )
        disabled = {
            self._selection_key(
                source_id=source_id,
                model_provider=model_provider,
                model_name=model_name,
            )
            for source_id, model_provider, model_name in result.tuples().all()
        }
        disabled_selections = list(disabled)
        await self._invalidate_stale_selection_dependents(disabled_selections)
        await self._revalidate_default_model_setting(disabled)
        return disabled

    async def _invalidate_stale_selection_dependents(
        self, selections: list[ModelSelectionKey]
    ) -> None:
        if not selections:
            return

        workspace_ids = select(Workspace.id).where(
            Workspace.organization_id == self.organization_id
        )

        preset_conditions = [
            self._condition_for_selection_on_model(AgentPreset, selection=selection)
            for selection in selections
        ]
        if preset_conditions:
            await self.session.execute(
                update(AgentPreset)
                .where(
                    AgentPreset.workspace_id.in_(workspace_ids),
                    or_(*preset_conditions),
                )
                .values(
                    source_id=None,
                    model_name=case(
                        *[
                            (
                                condition,
                                self._invalidated_model_name(selection.model_name),
                            )
                            for selection, condition in zip(
                                selections, preset_conditions, strict=True
                            )
                        ],
                        else_=AgentPreset.model_name,
                    ),
                    base_url=None,
                    updated_at=func.now(),
                )
            )

        version_conditions = [
            self._condition_for_selection_on_model(
                AgentPresetVersion, selection=selection
            )
            for selection in selections
        ]
        if version_conditions:
            await self.session.execute(
                update(AgentPresetVersion)
                .where(
                    AgentPresetVersion.workspace_id.in_(workspace_ids),
                    or_(*version_conditions),
                )
                .values(
                    source_id=None,
                    model_name=case(
                        *[
                            (
                                condition,
                                self._invalidated_model_name(selection.model_name),
                            )
                            for selection, condition in zip(
                                selections, version_conditions, strict=True
                            )
                        ],
                        else_=AgentPresetVersion.model_name,
                    ),
                    base_url=None,
                    updated_at=func.now(),
                )
            )

        session_conditions = [
            self._condition_for_selection_on_model(AgentSession, selection=selection)
            for selection in selections
        ]
        if session_conditions:
            await self.session.execute(
                update(AgentSession)
                .where(
                    AgentSession.workspace_id.in_(workspace_ids),
                    or_(*session_conditions),
                )
                .values(
                    source_id=None,
                    model_provider=None,
                    model_name=None,
                    updated_at=func.now(),
                )
            )

    async def _revalidate_default_model_setting(
        self,
        disabled: set[ModelSelectionKey],
    ) -> None:
        if not disabled:
            return
        setting = await self.settings_service.get_org_setting("agent_default_model")
        if setting is None:
            return

        value = self.settings_service.get_value(setting)
        if isinstance(value, dict):
            try:
                selection = ModelSelection.model_validate(value)
            except Exception:
                return
            if self._selection_key_from_model_selection(selection) in disabled:
                await self._clear_default_model_selection()
            return

        if not value:
            return

        legacy_model_name = str(value)
        if ref_selection := await self._get_default_model_ref_selection():
            selection_key = self._selection_key_from_model_selection(ref_selection)
            if selection_key not in disabled and await self.is_model_enabled(
                selection_key, workspace_id=None
            ):
                return
        match await resolve_enabled_catalog_match_for_model_name(
            self.session,
            organization_id=self.organization_id,
            model_name=legacy_model_name,
        ):
            case match_result:
                selection = self._selection_from_legacy_match(match_result)
                if (
                    selection is not None
                    and self._selection_key_from_model_selection(selection)
                    not in disabled
                ):
                    return

        await self._clear_default_model_selection()

    def _normalize_enabled_model_config(
        self,
        *,
        model_provider: str,
        config: EnabledModelRuntimeConfig,
    ) -> dict[str, Any] | None:
        match model_provider:
            case "bedrock":
                normalized: dict[str, Any] = {}
                if inference_profile_id := config.bedrock_inference_profile_id:
                    if stripped := inference_profile_id.strip():
                        normalized["bedrock_inference_profile_id"] = stripped
                return normalized or None
            case _:
                if config.bedrock_inference_profile_id is not None:
                    raise ValueError(
                        "Per-model inference profiles are currently only supported for Bedrock models."
                    )
                return None

    @require_scope("agent:update")
    @requires_entitlement(Entitlement.AGENT_ADDONS)
    async def disable_model(self, selection: ModelSelection) -> None:
        await self._disable_model_selections(
            [self._selection_key_from_model_selection(selection)]
        )

    @require_scope("agent:update")
    @requires_entitlement(Entitlement.AGENT_ADDONS)
    async def disable_models(self, params: EnabledModelsBatchOperation) -> None:
        await self._disable_model_selections(
            [self._selection_key_from_model_selection(item) for item in params.models]
        )

    @require_scope("agent:update")
    @requires_entitlement(Entitlement.AGENT_ADDONS)
    async def update_enabled_model_config(
        self, params: EnabledModelRuntimeConfigUpdate
    ) -> ModelCatalogEntry:
        selection = self._selection_key(
            source_id=params.source_id,
            model_provider=params.model_provider,
            model_name=params.model_name,
        )
        row = await self._get_enabled_row(selection)
        if row is None:
            raise TracecatNotFoundError(
                f"Enabled model {params.model_provider}/{params.model_name} not found"
            )
        catalog_row = await self._get_catalog_row_model(selection)
        row.enabled_config = self._normalize_enabled_model_config(
            model_provider=catalog_row.model_provider,
            config=params.config,
        )
        self.session.add(row)
        await self.session.commit()
        await self.session.refresh(row)
        return (await self._build_enabled_model_entries([row]))[0]

    async def _resolve_legacy_catalog_match(
        self,
        *,
        model_provider: str,
        model_name: str,
        workspace_id: uuid.UUID | None = None,
    ) -> LegacyCatalogMatch:
        if model_provider == LEGACY_CUSTOM_PROVIDER:
            await self._ensure_legacy_custom_provider_source_synced()
        match_result = await resolve_catalog_match_for_provider_model(
            self.session,
            organization_id=self.organization_id,
            workspace_id=workspace_id,
            model_provider=model_provider,
            model_name=model_name,
        )
        if match_result.status != "missing":
            return match_result
        runtime_target = self._provider_runtime_target(
            provider=model_provider,
            credentials=await self.get_provider_credentials(model_provider),
        )
        if runtime_target and runtime_target != model_name:
            return await resolve_catalog_match_for_provider_model(
                self.session,
                organization_id=self.organization_id,
                workspace_id=workspace_id,
                model_provider=model_provider,
                model_name=runtime_target,
            )
        return match_result

    async def _resolve_accessible_legacy_catalog_match(
        self,
        *,
        model_provider: str,
        model_name: str,
    ) -> LegacyCatalogMatch:
        if model_provider == LEGACY_CUSTOM_PROVIDER:
            await self._ensure_legacy_custom_provider_source_synced()
        match_result = await resolve_accessible_catalog_match_for_provider_model(
            self.session,
            organization_id=self.organization_id,
            model_provider=model_provider,
            model_name=model_name,
        )
        if match_result.status != "missing":
            return match_result
        runtime_target = self._provider_runtime_target(
            provider=model_provider,
            credentials=await self.get_provider_credentials(model_provider),
        )
        if runtime_target and runtime_target != model_name:
            return await resolve_accessible_catalog_match_for_provider_model(
                self.session,
                organization_id=self.organization_id,
                model_provider=model_provider,
                model_name=runtime_target,
            )
        return match_result

    async def _resolve_accessible_legacy_default_model_match(
        self,
        *,
        legacy_model_name: str,
    ) -> LegacyCatalogMatch:
        match_result = await resolve_accessible_catalog_match_for_model_name(
            self.session,
            organization_id=self.organization_id,
            model_name=legacy_model_name,
        )
        if match_result.status != "missing":
            return match_result
        if legacy_model_name in PROVIDER_CREDENTIAL_CONFIGS:
            return await self._resolve_accessible_legacy_catalog_match(
                model_provider=legacy_model_name,
                model_name=legacy_model_name,
            )
        return match_result

    def _selection_from_legacy_match(
        self,
        match_result: LegacyCatalogMatch,
    ) -> ModelSelection | None:
        if (
            match_result.status != "matched"
            or match_result.model_provider is None
            or match_result.model_name is None
        ):
            return None
        return ModelSelection(
            source_id=match_result.source_id,
            model_provider=match_result.model_provider,
            model_name=match_result.model_name,
        )

    def _encode_default_model_ref(self, selection: ModelSelection) -> str:
        return orjson.dumps(selection.model_dump(mode="json")).decode()

    def _decode_default_model_ref(self, value: object) -> ModelSelection | None:
        if not isinstance(value, str) or not value:
            return None
        try:
            payload = orjson.loads(value)
        except orjson.JSONDecodeError:
            return None
        if not isinstance(payload, dict):
            return None
        try:
            return ModelSelection.model_validate(payload)
        except Exception:
            return None

    async def _get_default_model_ref_selection(self) -> ModelSelection | None:
        setting = await self.settings_service.get_org_setting("agent_default_model_ref")
        if setting is None:
            return None
        return self._decode_default_model_ref(self.settings_service.get_value(setting))

    async def _get_default_model_selection(self) -> ModelSelection | None:
        legacy_setting = await self.settings_service.get_org_setting(
            "agent_default_model"
        )
        if legacy_setting is None:
            return None
        value = self.settings_service.get_value(legacy_setting)
        if isinstance(value, dict):
            try:
                return ModelSelection.model_validate(value)
            except Exception:
                return None
        if not value:
            return None
        legacy_model_name = str(value)
        if ref_selection := await self._get_default_model_ref_selection():
            selection_key = self._selection_key_from_model_selection(ref_selection)
            if await self.is_model_enabled(selection_key, workspace_id=None):
                await self._persist_default_model_selection(ref_selection)
                return ref_selection
        match await resolve_enabled_catalog_match_for_model_name(
            self.session,
            organization_id=self.organization_id,
            model_name=legacy_model_name,
        ):
            case match_result:
                selection = self._selection_from_legacy_match(match_result)
                if selection is not None:
                    await self._persist_default_model_selection(selection)
                    return selection
                return None

    async def _persist_default_model_selection(
        self, selection: ModelSelection
    ) -> DefaultModelSelection:
        selection_key = self._selection_key_from_model_selection(selection)
        enabled_keys = {
            self._selection_key_from_enabled_row(row)
            for row in await self._list_org_enabled_rows()
        }
        if selection_key not in enabled_keys:
            raise TracecatNotFoundError(
                f"Model {selection.model_provider}/{selection.model_name} is not enabled"
            )
        setting = await self.settings_service.get_org_setting("agent_default_model")
        value = selection.model_dump(mode="json")
        if setting:
            await self.settings_service.update_org_setting(
                setting, SettingUpdate(value=value)
            )
        else:
            await self.settings_service.create_org_setting(
                SettingCreate(
                    key="agent_default_model",
                    value=value,
                    value_type=ValueType.JSON,
                    is_sensitive=False,
                )
            )
        ref_setting = await self.settings_service.get_org_setting(
            "agent_default_model_ref"
        )
        ref_value = self._encode_default_model_ref(selection)
        if ref_setting:
            await self.settings_service.update_org_setting(
                ref_setting, SettingUpdate(value=ref_value)
            )
        else:
            await self.settings_service.create_org_setting(
                SettingCreate(
                    key="agent_default_model_ref",
                    value=ref_value,
                    value_type=ValueType.JSON,
                    is_sensitive=False,
                )
            )
        row = await self._get_catalog_row(selection_key)
        return DefaultModelSelection(
            source_id=row.source_id,
            model_provider=row.model_provider,
            model_name=row.model_name,
            source_type=row.source_type,
            source_name=row.source_name,
        )

    async def get_default_model(self) -> DefaultModelSelection | None:
        selection = await self._get_default_model_selection()
        if selection is None:
            return None
        row = await self._get_catalog_row(
            self._selection_key_from_model_selection(selection)
        )
        return DefaultModelSelection(
            source_id=row.source_id,
            model_provider=row.model_provider,
            model_name=row.model_name,
            source_type=row.source_type,
            source_name=row.source_name,
        )

    @require_scope("agent:update")
    @requires_entitlement(Entitlement.AGENT_ADDONS)
    async def set_default_model_selection(
        self, selection: ModelSelection
    ) -> DefaultModelSelection:
        return await self._persist_default_model_selection(selection)

    async def _clear_default_model_selection(self) -> None:
        for key in ("agent_default_model", "agent_default_model_ref"):
            if setting := await self.settings_service.get_org_setting(key):
                await self.settings_service.update_org_setting(
                    setting, SettingUpdate(value=None)
                )

    @require_scope("agent:update")
    async def create_provider_credentials(
        self, params: ModelCredentialCreate
    ) -> OrganizationSecret:
        """Create or update credentials for an AI provider."""
        self._ensure_builtin_provider(params.provider)
        secret_name = self._get_credential_secret_name(params.provider)

        # Check if credentials already exist
        try:
            existing = await self.secrets_service.get_org_secret_by_name(secret_name)
            keys = [
                SecretKeyValue(key=key, value=SecretStr(value))
                for key, value in params.credentials.items()
            ]
            update_params = SecretUpdate(keys=keys)
            await self.secrets_service.update_org_secret(existing, update_params)
            await self._ensure_default_enabled_models()
            return existing
        except TracecatNotFoundError:
            keys = [
                SecretKeyValue(key=key, value=SecretStr(value))
                for key, value in params.credentials.items()
            ]
            create_params = SecretCreate(
                name=secret_name,
                type=SecretType.CUSTOM,
                description=f"Credentials for {params.provider} AI provider",
                keys=keys,
                tags={"provider": params.provider, "type": "agent-credentials"},
            )
            await self.secrets_service.create_org_secret(create_params)
            created = await self.secrets_service.get_org_secret_by_name(secret_name)
            await self._ensure_default_enabled_models()
            return created

    @require_scope("agent:update")
    async def update_provider_credentials(
        self, provider: str, params: ModelCredentialUpdate
    ) -> OrganizationSecret:
        """Update existing credentials for an AI provider."""
        self._ensure_builtin_provider(provider)
        secret_name = self._get_credential_secret_name(provider)
        secret = await self.secrets_service.get_org_secret_by_name(secret_name)

        keys = [
            SecretKeyValue(key=key, value=SecretStr(value))
            for key, value in params.credentials.items()
        ]
        update_params = SecretUpdate(keys=keys)
        await self.secrets_service.update_org_secret(secret, update_params)
        await self._ensure_default_enabled_models()
        return secret

    async def _load_provider_credentials(self, provider: str) -> dict[str, str] | None:
        """Load decrypted credentials for an AI provider without scope checks."""
        self._ensure_builtin_provider(provider)
        secret_name = self._get_credential_secret_name(provider)
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
        """Get decrypted credentials for an AI provider at organization level."""
        return await self._load_provider_credentials(provider)

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

    async def _get_workspace_provider_credentials_compat(
        self, provider: str
    ) -> dict[str, str] | None:
        """Fallback to legacy workspace-scoped provider secrets during cutover."""
        self._ensure_builtin_provider(provider)
        if self.role.workspace_id is None:
            return None

        secret_name = self._get_credential_secret_name(provider)
        result = await self.session.execute(
            select(Secret.encrypted_keys).where(
                Secret.workspace_id == self.role.workspace_id,
                Secret.name == secret_name,
                Secret.environment == DEFAULT_SECRETS_ENVIRONMENT,
            )
        )
        if (encrypted_keys := result.scalar_one_or_none()) is None:
            return None
        decrypted_keys = self.secrets_service.decrypt_keys(encrypted_keys)
        return {kv.key: kv.value.get_secret_value() for kv in decrypted_keys}

    async def get_provider_credentials_compat(
        self, provider: str
    ) -> dict[str, str] | None:
        """Get org-scoped credentials with legacy workspace fallback during cutover."""
        if credentials := await self.get_provider_credentials(provider):
            return credentials
        return await self._get_workspace_provider_credentials_compat(provider)
    @require_scope("agent:update")
    async def delete_provider_credentials(self, provider: str) -> None:
        """Delete credentials for an AI provider."""
        self._ensure_builtin_provider(provider)
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
        self._ensure_builtin_provider(provider)
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
        providers = [provider.value for provider in _BUILT_IN_PROVIDER_ORDER]
        status = {}
        for provider in providers:
            status[provider] = self._provider_credentials_complete(
                provider=provider,
                credentials=await self._load_provider_credentials(provider),
            )
        return status

    @contextlib.contextmanager
    def _credentials_sandbox(self, credentials: dict[str, str]) -> Iterator[None]:
        """Expose provider credentials to both Tracecat and registry contexts."""
        secrets_token = registry_secrets.set_context(credentials)
        try:
            with secrets_manager.env_sandbox(credentials):
                yield
        finally:
            registry_secrets.reset_context(secrets_token)

    async def _get_runtime_credentials(
        self,
        *,
        catalog_entry: ResolvedCatalogRecord,
        selection: ModelSelectionKey | None = None,
    ) -> dict[str, str]:
        source_type = catalog_entry.source_type
        if source_type in {
            ModelSourceType.OPENAI_COMPATIBLE_GATEWAY,
            ModelSourceType.MANUAL_CUSTOM,
        }:
            if catalog_entry.source_id is None:
                return {}
            source = await self.get_model_source(catalog_entry.source_id)
            source_config = self._deserialize_sensitive_config(source.encrypted_config)
            runtime_base_url = self._source_runtime_base_url(
                source, source_config=source_config
            )
            credentials = {}
            api_key = source_config.get("api_key")
            if api_key:
                credentials[SOURCE_RUNTIME_API_KEY] = api_key
            if source.api_key_header:
                credentials[SOURCE_RUNTIME_API_KEY_HEADER] = source.api_key_header
            if source.api_version:
                credentials[SOURCE_RUNTIME_API_VERSION] = source.api_version
            if runtime_base_url:
                credentials[SOURCE_RUNTIME_BASE_URL] = runtime_base_url
            match catalog_entry.model_provider:
                case (
                    "openai"
                    | "openai_compatible_gateway"
                    | "manual_custom"
                    | "direct_endpoint"
                ):
                    if api_key:
                        credentials["OPENAI_API_KEY"] = api_key
                    if runtime_base_url:
                        credentials["OPENAI_BASE_URL"] = runtime_base_url
                case "anthropic":
                    if api_key:
                        credentials["ANTHROPIC_API_KEY"] = api_key
                    if runtime_base_url:
                        credentials["ANTHROPIC_BASE_URL"] = runtime_base_url
                case "gemini":
                    if api_key:
                        credentials["GEMINI_API_KEY"] = api_key
                case "azure_openai":
                    if api_key:
                        credentials["AZURE_API_KEY"] = api_key
                    if runtime_base_url:
                        credentials["AZURE_API_BASE"] = runtime_base_url
                    if source.api_version:
                        credentials["AZURE_API_VERSION"] = source.api_version
                case "azure_ai":
                    if api_key:
                        credentials["AZURE_API_KEY"] = api_key
                    if runtime_base_url:
                        credentials["AZURE_API_BASE"] = runtime_base_url
                case "custom-model-provider":
                    if api_key:
                        credentials["CUSTOM_MODEL_PROVIDER_API_KEY"] = api_key
                    if runtime_base_url:
                        credentials["CUSTOM_MODEL_PROVIDER_BASE_URL"] = runtime_base_url
                case _:
                    pass
            return credentials
        credentials = (
            await self.get_provider_credentials(catalog_entry.model_provider) or {}
        )
        if selection is None:
            return credentials
        enabled_row = await self._get_enabled_row(selection)
        if (
            enabled_row
            and catalog_entry.model_provider == ModelSourceType.BEDROCK.value
            and enabled_row.enabled_config
        ):
            config = EnabledModelRuntimeConfig.model_validate(
                enabled_row.enabled_config
            )
            if config.bedrock_inference_profile_id:
                credentials["AWS_INFERENCE_PROFILE_ID"] = (
                    config.bedrock_inference_profile_id
                )
        return credentials

    async def get_runtime_credentials_for_selection(
        self,
        *,
        selection: ModelSelection,
    ) -> dict[str, str]:
        selection_key = self._selection_key_from_model_selection(selection)
        row = await self._get_catalog_row(selection_key)
        return await self._get_runtime_credentials(
            catalog_entry=row,
            selection=selection_key,
        )

    async def get_runtime_credentials_for_config(
        self,
        config: AgentConfig,
    ) -> dict[str, str] | None:
        """Resolve runtime credentials, preferring enabled selections when available."""
        if config.model_name and config.model_provider:
            selection = ModelSelection(
                source_id=config.source_id,
                model_provider=config.model_provider,
                model_name=config.model_name,
            )
            selection_key = self._selection_key_from_model_selection(selection)
            if await self.is_model_enabled(
                selection_key,
                workspace_id=self.role.workspace_id,
            ):
                return await self.get_runtime_credentials_for_selection(
                    selection=selection
                )

            if config.source_id is not None:
                try:
                    row = await self._get_catalog_row(selection_key)
                except TracecatNotFoundError:
                    pass
                else:
                    return await self._get_runtime_credentials(
                        catalog_entry=row,
                        selection=selection_key,
                    )

            return await self.get_provider_credentials(config.model_provider)
        if config.model_provider:
            return await self.get_provider_credentials(config.model_provider)
        return None

    async def _resolve_catalog_agent_config(
        self,
        *,
        selection: ModelSelectionKey,
    ) -> tuple[AgentConfig, dict[str, str]]:
        row = await self._get_catalog_row(selection)
        credentials = await self._get_runtime_credentials(
            catalog_entry=row,
            selection=selection,
        )
        if not credentials and row.source_type not in {
            ModelSourceType.OPENAI_COMPATIBLE_GATEWAY,
            ModelSourceType.MANUAL_CUSTOM,
        }:
            raise TracecatNotFoundError(
                f"No credentials found for provider '{row.model_provider}' at organization level. "
                "Please configure credentials for this provider first."
            )
        return (
            AgentConfig(
                model_name=row.model_name,
                model_provider=row.model_provider,
                source_id=row.source_id,
                base_url=row.base_url,
            ),
            credentials,
        )

    def _overlay_runtime_config(
        self,
        *,
        resolved_config: AgentConfig,
        overrides: AgentConfig,
    ) -> AgentConfig:
        """Merge non-model fields onto a catalog-resolved runtime config."""
        return replace(
            resolved_config,
            base_url=overrides.base_url or resolved_config.base_url,
            instructions=overrides.instructions,
            output_type=overrides.output_type,
            actions=overrides.actions,
            namespaces=overrides.namespaces,
            tool_approvals=overrides.tool_approvals,
            model_settings=overrides.model_settings,
            mcp_servers=overrides.mcp_servers,
            retries=overrides.retries,
            enable_internet_access=overrides.enable_internet_access,
        )

    async def resolve_runtime_agent_config(
        self,
        config: AgentConfig,
    ) -> AgentConfig:
        """Canonicalize an agent config to the catalog-backed runtime shape when possible."""
        if (
            config.model_name
            and config.model_provider
            and await self.is_model_enabled(
                self._selection_key(
                    source_id=config.source_id,
                    model_provider=config.model_provider,
                    model_name=config.model_name,
                ),
                workspace_id=self.role.workspace_id,
            )
        ):
            selection = self._selection_key(
                source_id=config.source_id,
                model_provider=config.model_provider,
                model_name=config.model_name,
            )
            resolved_config, _ = await self._resolve_catalog_agent_config(
                selection=selection
            )
            return self._overlay_runtime_config(
                resolved_config=resolved_config,
                overrides=config,
            )

        if selection := await self._resolve_preset_selection(config):
            resolved_config, _ = await self._resolve_catalog_agent_config(
                selection=selection
            )
            return self._overlay_runtime_config(
                resolved_config=resolved_config,
                overrides=config,
            )

        return config

    async def is_model_enabled(
        self,
        selection: ModelSelectionKey,
        *,
        workspace_id: uuid.UUID | None = None,
    ) -> bool:
        effective_workspace_id = workspace_id or self.role.workspace_id
        enabled_rows = await self._filter_enabled_rows_for_workspace(
            await self._list_org_enabled_rows(),
            effective_workspace_id,
        )
        enabled_keys = {
            self._selection_key_from_enabled_row(row) for row in enabled_rows
        }
        return selection in enabled_keys

    async def require_enabled_model_selection(
        self,
        selection: ModelSelection,
        *,
        workspace_id: uuid.UUID | None = None,
    ) -> None:
        selection_key = self._selection_key_from_model_selection(selection)
        if not await self.is_model_enabled(selection_key, workspace_id=workspace_id):
            raise TracecatNotFoundError(
                f"Model {selection.model_provider}/{selection.model_name} is not enabled"
            )

    async def _resolve_preset_selection(
        self, preset_config: AgentConfig
    ) -> ModelSelectionKey | None:
        if preset_config.source_id is not None or (
            preset_config.model_provider and preset_config.model_name
        ):
            selection = self._selection_key(
                source_id=preset_config.source_id,
                model_provider=preset_config.model_provider,
                model_name=preset_config.model_name,
            )
            if await self.is_model_enabled(
                selection, workspace_id=self.role.workspace_id
            ):
                return selection
            try:
                await self._get_catalog_row_model(selection)
            except TracecatNotFoundError:
                if preset_config.source_id is not None:
                    raise TracecatNotFoundError(
                        "Source-backed model selection "
                        f"{preset_config.model_provider}/{preset_config.model_name} "
                        "is no longer available"
                    ) from None
                pass
            else:
                raise TracecatNotFoundError(
                    f"Model {preset_config.model_provider}/{preset_config.model_name} is not enabled"
                )
        match await resolve_enabled_catalog_match_for_provider_model(
            self.session,
            organization_id=self.organization_id,
            workspace_id=self.role.workspace_id,
            model_provider=preset_config.model_provider,
            model_name=preset_config.model_name,
        ):
            case match_result:
                if selection := self._selection_from_legacy_match(match_result):
                    return self._selection_key_from_model_selection(selection)
                return None

    async def repair_legacy_model_selections(self) -> LegacyModelRepairSummary:
        summary = LegacyModelRepairSummary()
        await self._ensure_legacy_custom_provider_source_synced()
        auto_enabled_selections: set[ModelSelectionKey] = set()
        if legacy_setting := await self.settings_service.get_org_setting(
            "agent_default_model"
        ):
            legacy_value = self.settings_service.get_value(legacy_setting)
            if isinstance(legacy_value, str) and legacy_value:
                match await self._resolve_accessible_legacy_default_model_match(
                    legacy_model_name=legacy_value,
                ):
                    case match_result if (
                        selection_obj := self._selection_from_legacy_match(match_result)
                    ) is not None:
                        selection = self._selection_key_from_model_selection(
                            selection_obj
                        )
                        try:
                            if selection not in auto_enabled_selections:
                                await self._enable_catalog_rows([selection])
                                auto_enabled_selections.add(selection)
                            await self.set_default_model_selection(selection_obj)
                            summary = replace(
                                summary,
                                migrated_defaults=summary.migrated_defaults + 1,
                            )
                        except TracecatNotFoundError:
                            summary = replace(
                                summary,
                                unresolved_defaults=summary.unresolved_defaults + 1,
                            )
                    case LegacyCatalogMatch(status="ambiguous"):
                        summary = replace(
                            summary,
                            ambiguous_defaults=summary.ambiguous_defaults + 1,
                        )
                    case _:
                        summary = replace(
                            summary,
                            unresolved_defaults=summary.unresolved_defaults + 1,
                        )

        preset_rows = (
            (
                await self.session.execute(
                    select(AgentPreset, Workspace.id)
                    .join(Workspace, Workspace.id == AgentPreset.workspace_id)
                    .where(
                        Workspace.organization_id == self.organization_id,
                    )
                )
            )
            .tuples()
            .all()
        )
        for preset, _workspace_id in preset_rows:
            match await self._resolve_accessible_legacy_catalog_match(
                model_provider=preset.model_provider,
                model_name=preset.model_name,
            ):
                case match_result if (
                    selection_obj := self._selection_from_legacy_match(match_result)
                ) is not None:
                    selection = self._selection_key_from_model_selection(selection_obj)
                    try:
                        if selection not in auto_enabled_selections:
                            await self._enable_catalog_rows([selection])
                            auto_enabled_selections.add(selection)
                        if (
                            preset.source_id != selection_obj.source_id
                            or preset.model_provider != selection_obj.model_provider
                            or preset.model_name != selection_obj.model_name
                        ):
                            preset.source_id = selection_obj.source_id
                            preset.model_provider = selection_obj.model_provider
                            preset.model_name = selection_obj.model_name
                            self.session.add(preset)
                            summary = replace(
                                summary,
                                migrated_presets=summary.migrated_presets + 1,
                            )
                    except TracecatNotFoundError:
                        summary = replace(
                            summary,
                            unresolved_presets=summary.unresolved_presets + 1,
                        )
                case LegacyCatalogMatch(status="ambiguous"):
                    summary = replace(
                        summary,
                        ambiguous_presets=summary.ambiguous_presets + 1,
                    )
                case _:
                    summary = replace(
                        summary,
                        unresolved_presets=summary.unresolved_presets + 1,
                    )

        version_rows = (
            (
                await self.session.execute(
                    select(AgentPresetVersion, Workspace.id)
                    .join(Workspace, Workspace.id == AgentPresetVersion.workspace_id)
                    .where(
                        Workspace.organization_id == self.organization_id,
                    )
                )
            )
            .tuples()
            .all()
        )
        for version, _workspace_id in version_rows:
            match await self._resolve_accessible_legacy_catalog_match(
                model_provider=version.model_provider,
                model_name=version.model_name,
            ):
                case match_result if (
                    selection_obj := self._selection_from_legacy_match(match_result)
                ) is not None:
                    selection = self._selection_key_from_model_selection(selection_obj)
                    try:
                        if selection not in auto_enabled_selections:
                            await self._enable_catalog_rows([selection])
                            auto_enabled_selections.add(selection)
                        if (
                            version.source_id != selection_obj.source_id
                            or version.model_provider != selection_obj.model_provider
                            or version.model_name != selection_obj.model_name
                        ):
                            version.source_id = selection_obj.source_id
                            version.model_provider = selection_obj.model_provider
                            version.model_name = selection_obj.model_name
                            self.session.add(version)
                            summary = replace(
                                summary,
                                migrated_versions=summary.migrated_versions + 1,
                            )
                    except TracecatNotFoundError:
                        summary = replace(
                            summary,
                            unresolved_versions=summary.unresolved_versions + 1,
                        )
                case LegacyCatalogMatch(status="ambiguous"):
                    summary = replace(
                        summary,
                        ambiguous_versions=summary.ambiguous_versions + 1,
                    )
                case _:
                    summary = replace(
                        summary,
                        unresolved_versions=summary.unresolved_versions + 1,
                    )

        if (
            summary.migrated_defaults
            or summary.migrated_presets
            or summary.migrated_versions
        ):
            await self.session.commit()

        return summary

    async def prune_stale_builtin_model_selections(self) -> set[ModelSelectionKey]:
        """Remove enabled builtin selections whose shared catalog rows were pruned."""
        builtin_keys = {
            (model_provider, model_name)
            for model_provider, model_name in (
                await self.session.execute(
                    select(
                        AgentCatalog.model_provider,
                        AgentCatalog.model_name,
                    ).where(
                        AgentCatalog.organization_id.is_(None),
                        AgentCatalog.source_id.is_(None),
                    )
                )
            )
            .tuples()
            .all()
        }
        stale_selections = [
            self._selection_key_from_enabled_row(row)
            for row in await self._list_org_enabled_rows()
            if row.source_id is None
            and (row.model_provider, row.model_name) not in builtin_keys
        ]
        if not stale_selections:
            return set()
        disabled = await self._disable_model_selections(stale_selections)
        self.logger.info(
            "Pruned stale builtin model selections",
            organization_id=str(self.organization_id),
            disabled_rows=len(disabled),
        )
        return disabled

    @contextlib.asynccontextmanager
    async def with_model_config(
        self,
        *,
        selection: ModelSelection | None = None,
    ) -> AsyncIterator[AgentConfig]:
        """Yield the resolved default model configuration and runtime credentials."""
        if selection is None:
            if not (default_selection := await self.get_default_model()):
                raise TracecatNotFoundError("No default model set")
            await self.require_enabled_model_selection(
                default_selection,
                workspace_id=self.role.workspace_id,
            )
            selection_key = self._selection_key_from_model_selection(default_selection)
        else:
            await self.require_enabled_model_selection(selection)
            selection_key = self._selection_key_from_model_selection(selection)
        model_config, credentials = await self._resolve_catalog_agent_config(
            selection=selection_key
        )
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
        """Yield an agent preset configuration with org-scoped provider credentials."""
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
        if selection := await self._resolve_preset_selection(preset_config):
            resolved_config, credentials = await self._resolve_catalog_agent_config(
                selection=selection
            )
            resolved_config = self._overlay_runtime_config(
                resolved_config=resolved_config,
                overrides=preset_config,
            )
            with self._credentials_sandbox(credentials):
                yield resolved_config
            return

        # Legacy fallback for presets that have not been migrated into the catalog yet.
        credentials = await self.get_provider_credentials(preset_config.model_provider)
        if not credentials:
            raise TracecatNotFoundError(
                f"No credentials found for provider '{preset_config.model_provider}'. "
                "Please configure credentials for this provider first."
            )
        with self._credentials_sandbox(credentials):
            yield preset_config


def _bootstrap_org_role(organization_id: uuid.UUID) -> Role:
    return Role(
        type="service",
        service_id="tracecat-bootstrap",
        organization_id=organization_id,
        is_platform_superuser=True,
        scopes=frozenset({"*"}),
    )


async def _list_active_organization_ids(session: AsyncSession) -> list[uuid.UUID]:
    stmt = (
        select(Organization.id)
        .where(Organization.is_active.is_(True))
        .order_by(Organization.created_at.asc())
    )
    return list((await session.execute(stmt)).scalars().all())


async def _sync_model_catalogs_as_leader(session: AsyncSession) -> None:
    org_ids = await _list_active_organization_ids(session)
    if not org_ids:
        logger.info(
            "Skipping model catalog gateway-startup sync because no organizations exist"
        )
        return

    platform_service = AgentManagementService(
        session,
        role=_bootstrap_org_role(org_ids[0]),
    )
    builtins_inventory = await platform_service.refresh_builtin_catalog()
    logger.info(
        "Completed platform catalog gateway-startup sync",
        litellm_version=LITELLM_PINNED_VERSION,
        models=len(builtins_inventory.models),
    )

    for org_id in org_ids:
        service = AgentManagementService(session, role=_bootstrap_org_role(org_id))
        source_ids = [source.id for source in await service.list_model_sources()]
        if not source_ids:
            logger.debug(
                "No org model sources to sync during gateway startup",
                organization_id=str(org_id),
            )
        else:
            for source_id in source_ids:
                try:
                    refreshed = await service.refresh_model_source(source_id)
                    logger.info(
                        "Completed org model source gateway-startup sync",
                        organization_id=str(org_id),
                        source_id=str(source_id),
                        discovered_models=len(refreshed),
                    )
                except Exception as exc:
                    logger.warning(
                        "Org model source gateway-startup sync failed",
                        organization_id=str(org_id),
                        source_id=str(source_id),
                        error=str(exc),
                    )
                    await session.rollback()
        await service.prune_stale_builtin_model_selections()
        await service.prune_unconfigured_builtin_model_selections()
        await service._ensure_default_enabled_models()
        if repair_legacy := getattr(service, "repair_legacy_model_selections", None):
            repair_summary = await repair_legacy()
            logger.info(
                "Completed legacy agent model compatibility repair",
                organization_id=str(org_id),
                migrated_defaults=repair_summary.migrated_defaults,
                migrated_presets=repair_summary.migrated_presets,
                migrated_versions=repair_summary.migrated_versions,
                unresolved_defaults=repair_summary.unresolved_defaults,
                unresolved_presets=repair_summary.unresolved_presets,
                unresolved_versions=repair_summary.unresolved_versions,
                ambiguous_defaults=repair_summary.ambiguous_defaults,
                ambiguous_presets=repair_summary.ambiguous_presets,
                ambiguous_versions=repair_summary.ambiguous_versions,
            )


async def sync_model_catalogs_on_startup() -> None:
    logger.info("Attempting model catalog gateway-startup sync")
    try:
        async with get_async_session_bypass_rls_context_manager() as session:
            acquired = await try_pg_advisory_lock(
                session,
                MODEL_CATALOG_STARTUP_SYNC_LOCK_KEY,
            )
            if not acquired:
                logger.info(
                    "Another process is handling model catalog gateway-startup sync, waiting to continue"
                )
                async with pg_advisory_lock(
                    session,
                    MODEL_CATALOG_STARTUP_SYNC_LOCK_KEY,
                ):
                    logger.info(
                        "Model catalog gateway-startup sync completed by another process"
                    )
                return

            try:
                await _sync_model_catalogs_as_leader(session)
            finally:
                await pg_advisory_unlock(session, MODEL_CATALOG_STARTUP_SYNC_LOCK_KEY)
    except Exception as exc:
        logger.warning("Model catalog gateway-startup sync failed", error=str(exc))
