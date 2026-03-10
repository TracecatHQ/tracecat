from __future__ import annotations

import asyncio
import contextlib
import hashlib
import uuid
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, cast

import httpx
import orjson
from google.auth.transport.requests import Request as GoogleAuthRequest
from google.oauth2 import service_account
from pydantic import SecretStr
from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from tracecat_registry._internal import secrets as registry_secrets

from tracecat import config
from tracecat.agent.builtin_catalog import (
    LITELLM_PINNED_VERSION,
    BuiltInCatalogModel,
    get_builtin_catalog_by_provider,
    get_builtin_catalog_by_ref,
    get_builtin_catalog_models,
)
from tracecat.agent.config import MODEL_CONFIGS, PROVIDER_CREDENTIAL_CONFIGS
from tracecat.agent.preset.service import AgentPresetService
from tracecat.agent.schemas import (
    AgentModelSourceCreate,
    AgentModelSourceRead,
    AgentModelSourceUpdate,
    BuiltInCatalogEntry,
    BuiltInCatalogRead,
    BuiltInProviderRead,
    DefaultModelInventoryRead,
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
    ProviderCredentialConfig,
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
from tracecat.db.engine import get_async_session_context_manager
from tracecat.db.locks import (
    derive_lock_key_from_parts,
    pg_advisory_unlock,
    try_pg_advisory_lock,
)
from tracecat.db.models import (
    AgentDiscoveredModel,
    AgentEnabledModel,
    AgentModelSource,
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
from tracecat.service import BaseOrgService
from tracecat.settings.schemas import SettingCreate, SettingUpdate, ValueType
from tracecat.settings.service import SettingsService

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
_CUSTOM_SOURCE_TYPES = {
    ModelSourceType.OPENAI_COMPATIBLE_GATEWAY,
    ModelSourceType.MANUAL_CUSTOM,
}
_BUILT_IN_PROVIDER_ORDER = (
    ModelSourceType.OPENAI,
    ModelSourceType.ANTHROPIC,
    ModelSourceType.GEMINI,
    ModelSourceType.VERTEX_AI,
    ModelSourceType.BEDROCK,
    ModelSourceType.AZURE_OPENAI,
    ModelSourceType.AZURE_AI,
)


@dataclass(frozen=True, slots=True)
class ResolvedCatalogRecord:
    catalog_ref: str
    model_name: str
    model_provider: str
    runtime_provider: str
    display_name: str
    source_type: ModelSourceType
    source_name: str
    source_id: uuid.UUID | None
    base_url: str | None
    last_refreshed_at: datetime | None
    metadata: dict[str, Any] | None


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
        decrypted = decrypt_value(
            payload,
            key=config.TRACECAT__DB_ENCRYPTION_KEY or "",
        )
        return orjson.loads(decrypted)

    def _provider_from_source_type(self, source_type: ModelSourceType) -> str:
        match source_type:
            case ModelSourceType.DEFAULT_SIDECAR:
                return "default_sidecar"
            case ModelSourceType.OPENAI_COMPATIBLE_GATEWAY:
                return "openai_compatible_gateway"
            case ModelSourceType.MANUAL_CUSTOM:
                return "manual_custom"
            case _:
                return source_type.value

    def _provider_label(self, provider: str) -> str:
        if provider in PROVIDER_CREDENTIAL_CONFIGS:
            return PROVIDER_CREDENTIAL_CONFIGS[provider].label
        return provider.replace("_", " ").title()

    def _ensure_builtin_provider(self, provider: str) -> None:
        if provider not in {
            source_type.value for source_type in _BUILT_IN_PROVIDER_ORDER
        }:
            raise TracecatNotFoundError(f"Provider {provider} not found")

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

    def _display_provider_for_model(
        self,
        *,
        source_type: ModelSourceType,
        model_name: str,
        provider_hint: str | None,
    ) -> str:
        if provider_hint:
            return provider_hint
        if source_type in _BUILT_IN_PROVIDER_SOURCE_TYPES:
            return source_type.value
        lowered = model_name.lower()
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

    def _catalog_ref(
        self,
        *,
        source_type: ModelSourceType,
        raw_model_id: str,
        source_id: uuid.UUID | None = None,
    ) -> str:
        if source_id is not None:
            source_ref = str(source_id)
        elif source_type == ModelSourceType.DEFAULT_SIDECAR:
            source_ref = "default"
        else:
            source_ref = source_type.value
        slug_input = f"{source_type.value}:{source_ref}:{raw_model_id}".encode()
        digest = hashlib.sha256(slug_input).hexdigest()[:16]
        return f"{source_type.value}:{source_ref}:{digest}:{raw_model_id}"

    def _catalog_entry_from_row(
        self,
        row: AgentDiscoveredModel,
        *,
        enabled: bool,
    ) -> ModelCatalogEntry:
        return ModelCatalogEntry(
            catalog_ref=row.catalog_ref,
            model_name=row.model_name,
            model_provider=row.model_provider,
            runtime_provider=row.runtime_provider,
            display_name=row.display_name,
            source_type=ModelSourceType(row.source_type),
            source_name=row.source_name,
            source_id=row.source_id,
            base_url=row.base_url,
            enabled=enabled,
            last_refreshed_at=row.last_refreshed_at,
            metadata=row.model_metadata,
            enabled_config=None,
        )

    def _catalog_entry_from_enabled_row(
        self, row: AgentEnabledModel
    ) -> ModelCatalogEntry:
        return ModelCatalogEntry(
            catalog_ref=row.catalog_ref,
            model_name=row.model_name,
            model_provider=row.model_provider,
            runtime_provider=row.runtime_provider,
            display_name=row.display_name,
            source_type=ModelSourceType(row.source_type),
            source_name=self._provider_label(row.runtime_provider),
            source_id=row.source_id,
            base_url=row.base_url,
            enabled=True,
            last_refreshed_at=row.updated_at,
            metadata=None,
            enabled_config=(
                EnabledModelRuntimeConfig.model_validate(row.enabled_config)
                if row.enabled_config
                else None
            ),
        )

    def _resolved_from_builtin(self, row: BuiltInCatalogModel) -> ResolvedCatalogRecord:
        return ResolvedCatalogRecord(
            catalog_ref=row.catalog_ref,
            model_name=row.model_name,
            model_provider=row.model_provider,
            runtime_provider=row.runtime_provider,
            display_name=row.display_name,
            source_type=row.source_type,
            source_name=self._provider_label(row.runtime_provider),
            source_id=None,
            base_url=None,
            last_refreshed_at=None,
            metadata=row.metadata,
        )

    def _resolved_from_discovered(
        self, row: AgentDiscoveredModel
    ) -> ResolvedCatalogRecord:
        return ResolvedCatalogRecord(
            catalog_ref=row.catalog_ref,
            model_name=row.model_name,
            model_provider=row.model_provider,
            runtime_provider=row.runtime_provider,
            display_name=row.display_name,
            source_type=ModelSourceType(row.source_type),
            source_name=row.source_name,
            source_id=row.source_id,
            base_url=row.base_url,
            last_refreshed_at=row.last_refreshed_at,
            metadata=row.model_metadata,
        )

    def _get_default_sidecar_config(self) -> dict[str, str | None]:
        return {
            "base_url": config.TRACECAT__AGENT_DEPLOYMENT_GATEWAY_BASE_URL,
            "api_key": config.TRACECAT__AGENT_DEPLOYMENT_GATEWAY_API_KEY,
            "api_key_header": config.TRACECAT__AGENT_DEPLOYMENT_GATEWAY_API_KEY_HEADER,
        }

    async def _get_default_sidecar_state_value(self, field: str) -> object | None:
        return await self._get_platform_setting_value(
            DEFAULT_SIDECAR_STATE_SETTINGS[field]
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

    def _provider_state_setting_key(self, *, provider: str, field: str) -> str:
        return f"agent_provider_{provider}_{field}"

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

    async def _upsert_discovered_models(
        self,
        *,
        source_type: ModelSourceType,
        source_name: str,
        models: list[dict[str, object]],
        source_id: uuid.UUID | None = None,
        organization_scoped: bool,
    ) -> list[AgentDiscoveredModel]:
        existing_stmt = select(AgentDiscoveredModel.catalog_ref).where(
            AgentDiscoveredModel.source_type == source_type.value,
            AgentDiscoveredModel.source_id == source_id,
        )
        if organization_scoped:
            existing_stmt = existing_stmt.where(
                AgentDiscoveredModel.organization_id == self.organization_id
            )
        else:
            existing_stmt = existing_stmt.where(
                AgentDiscoveredModel.organization_id.is_(None)
            )
        existing_refs = set((await self.session.execute(existing_stmt)).scalars().all())
        models_by_ref: dict[str, dict[str, object]] = {}
        ordered_refs: list[str] = []
        now = datetime.now(UTC)

        for model in models:
            catalog_ref = str(model["catalog_ref"])
            if catalog_ref not in models_by_ref:
                ordered_refs.append(catalog_ref)
            models_by_ref[catalog_ref] = model

        stale_refs = existing_refs - set(ordered_refs)
        if stale_refs:
            await self.session.execute(
                delete(AgentDiscoveredModel).where(
                    AgentDiscoveredModel.catalog_ref.in_(stale_refs)
                )
            )
            delete_enabled = delete(AgentEnabledModel).where(
                AgentEnabledModel.catalog_ref.in_(stale_refs)
            )
            if organization_scoped:
                delete_enabled = delete_enabled.where(
                    AgentEnabledModel.organization_id == self.organization_id
                )
            await self.session.execute(delete_enabled)

        if not models_by_ref:
            return []

        values = [
            {
                "id": uuid.uuid4(),
                "organization_id": self.organization_id
                if organization_scoped
                else None,
                "source_id": source_id,
                "source_type": source_type.value,
                "source_name": source_name,
                "catalog_ref": catalog_ref,
                "model_name": str(model["model_name"]),
                "model_provider": str(model["model_provider"]),
                "runtime_provider": str(model["runtime_provider"]),
                "display_name": str(model["display_name"]),
                "raw_model_id": str(model["raw_model_id"]),
                "base_url": cast(str | None, model.get("base_url")),
                "model_metadata": cast(dict[str, Any] | None, model.get("metadata")),
                "last_refreshed_at": now,
            }
            for catalog_ref in ordered_refs
            if (model := models_by_ref.get(catalog_ref)) is not None
        ]
        stmt = pg_insert(AgentDiscoveredModel).values(values)
        stmt = stmt.on_conflict_do_update(
            index_elements=["catalog_ref"],
            set_={
                "organization_id": stmt.excluded.organization_id,
                "source_id": stmt.excluded.source_id,
                "source_type": stmt.excluded.source_type,
                "source_name": stmt.excluded.source_name,
                "model_name": stmt.excluded.model_name,
                "model_provider": stmt.excluded.model_provider,
                "runtime_provider": stmt.excluded.runtime_provider,
                "display_name": stmt.excluded.display_name,
                "raw_model_id": stmt.excluded.raw_model_id,
                "base_url": stmt.excluded.base_url,
                "model_metadata": stmt.excluded.model_metadata,
                "last_refreshed_at": stmt.excluded.last_refreshed_at,
                "updated_at": func.now(),
            },
        )
        await self.session.execute(stmt)
        persisted_rows = (
            (
                await self.session.execute(
                    select(AgentDiscoveredModel).where(
                        AgentDiscoveredModel.catalog_ref.in_(ordered_refs)
                    )
                )
            )
            .scalars()
            .all()
        )
        persisted_by_ref = {row.catalog_ref: row for row in persisted_rows}
        return [persisted_by_ref[catalog_ref] for catalog_ref in ordered_refs]

    async def _list_default_sidecar_rows(self) -> list[AgentDiscoveredModel]:
        stmt = (
            select(AgentDiscoveredModel)
            .where(
                AgentDiscoveredModel.source_type
                == ModelSourceType.DEFAULT_SIDECAR.value,
                AgentDiscoveredModel.organization_id.is_(None),
            )
            .order_by(AgentDiscoveredModel.display_name.asc())
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def _list_org_discovered_rows(self) -> list[AgentDiscoveredModel]:
        stmt = (
            select(AgentDiscoveredModel)
            .where(AgentDiscoveredModel.organization_id == self.organization_id)
            .order_by(
                AgentDiscoveredModel.source_name.asc(),
                AgentDiscoveredModel.display_name.asc(),
            )
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def _list_enabled_rows(self) -> list[AgentEnabledModel]:
        stmt = select(AgentEnabledModel).where(
            AgentEnabledModel.organization_id == self.organization_id
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def _get_enabled_row(self, catalog_ref: str) -> AgentEnabledModel | None:
        stmt = select(AgentEnabledModel).where(
            AgentEnabledModel.organization_id == self.organization_id,
            AgentEnabledModel.catalog_ref == catalog_ref,
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def _ensure_default_enabled_models(self) -> None:
        if await self._list_enabled_rows():
            return
        default_rows = await self._list_default_sidecar_rows()
        if not default_rows:
            return
        stmt = pg_insert(AgentEnabledModel).values(
            [
                {
                    "organization_id": self.organization_id,
                    "catalog_ref": row.catalog_ref,
                    "source_id": row.source_id,
                    "source_type": row.source_type,
                    "model_name": row.model_name,
                    "model_provider": row.model_provider,
                    "runtime_provider": row.runtime_provider,
                    "display_name": row.display_name,
                    "base_url": row.base_url,
                    "enabled_config": None,
                }
                for row in default_rows
            ]
        )
        await self.session.execute(
            stmt.on_conflict_do_nothing(
                index_elements=[
                    AgentEnabledModel.organization_id,
                    AgentEnabledModel.catalog_ref,
                ]
            )
        )
        await self.session.commit()

    async def _list_provider_rows(self, provider: str) -> list[AgentDiscoveredModel]:
        stmt = (
            select(AgentDiscoveredModel)
            .where(
                AgentDiscoveredModel.organization_id == self.organization_id,
                AgentDiscoveredModel.source_type == provider,
                AgentDiscoveredModel.source_id.is_(None),
            )
            .order_by(AgentDiscoveredModel.display_name.asc())
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def _get_provider_state(
        self, provider: str
    ) -> tuple[ModelDiscoveryStatus, datetime | None, str | None]:
        status = await self._get_org_setting_value(
            self._provider_state_setting_key(
                provider=provider, field="discovery_status"
            )
        )
        refreshed_at = await self._get_org_setting_value(
            self._provider_state_setting_key(
                provider=provider, field="last_refreshed_at"
            )
        )
        last_error = await self._get_org_setting_value(
            self._provider_state_setting_key(provider=provider, field="last_error")
        )
        return (
            ModelDiscoveryStatus(str(status or ModelDiscoveryStatus.NEVER.value)),
            datetime.fromisoformat(str(refreshed_at)) if refreshed_at else None,
            str(last_error) if last_error else None,
        )

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

    async def _get_workspace_enabled_model_refs(
        self, workspace_id: uuid.UUID
    ) -> set[str] | None:
        stmt = select(Workspace.settings).where(
            Workspace.id == workspace_id,
            Workspace.organization_id == self.organization_id,
        )
        settings = (await self.session.execute(stmt)).scalar_one_or_none()
        if settings is None:
            raise TracecatNotFoundError(f"Workspace {workspace_id} not found")
        if not isinstance(settings, dict):
            return None
        refs = settings.get("agent_enabled_model_refs")
        if refs is None:
            return None
        if not isinstance(refs, list):
            return None
        return {str(ref) for ref in refs if isinstance(ref, str)}

    async def _filter_enabled_rows_for_workspace(
        self,
        rows: list[AgentEnabledModel],
        workspace_id: uuid.UUID | None,
    ) -> list[AgentEnabledModel]:
        if workspace_id is None:
            return rows
        allowed_refs = await self._get_workspace_enabled_model_refs(workspace_id)
        if allowed_refs is None:
            return rows
        return [row for row in rows if row.catalog_ref in allowed_refs]

    def _build_builtin_catalog_entry(
        self,
        *,
        row: BuiltInCatalogModel,
        enabled_rows_by_ref: dict[str, AgentEnabledModel],
        discovered_refs: set[str],
        credentials: dict[str, str] | None,
    ) -> BuiltInCatalogEntry:
        provider = row.runtime_provider
        credential_config = PROVIDER_CREDENTIAL_CONFIGS[provider]
        credentials_configured = self._provider_credentials_complete(
            provider=provider,
            credentials=credentials,
        )
        ready = row.enableable and credentials_configured
        if row.readiness_message is not None:
            readiness_message = row.readiness_message
        elif not credentials_configured:
            readiness_message = (
                f"Configure {credential_config.label} credentials to enable this model."
            )
        else:
            readiness_message = None
        enabled_row = enabled_rows_by_ref.get(row.catalog_ref)
        return BuiltInCatalogEntry(
            catalog_ref=row.catalog_ref,
            model_name=row.model_name,
            model_provider=row.model_provider,
            runtime_provider=row.runtime_provider,
            display_name=row.display_name,
            source_type=row.source_type,
            source_name=self._provider_label(row.runtime_provider),
            enabled=enabled_row is not None,
            last_refreshed_at=None,
            metadata=row.metadata,
            enabled_config=(
                EnabledModelRuntimeConfig.model_validate(enabled_row.enabled_config)
                if enabled_row and enabled_row.enabled_config
                else None
            ),
            credential_provider=provider,
            credential_label=credential_config.label,
            credential_fields=credential_config.fields,
            credentials_configured=credentials_configured,
            discovered=row.catalog_ref in discovered_refs,
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
        enabled_refs = {row.catalog_ref for row in await self._list_enabled_rows()}
        base_url = None
        if credentials and (base_url_key := self._provider_base_url_key(provider)):
            base_url = credentials.get(base_url_key)
        rows = await self._list_provider_rows(provider)
        return BuiltInProviderRead(
            provider=provider,
            label=PROVIDER_CREDENTIAL_CONFIGS[provider].label,
            source_type=source_type,
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
                    row, enabled=row.catalog_ref in enabled_refs
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
        status, refreshed_at, last_error = await self._get_builtin_catalog_state()
        enabled_rows_by_ref = {
            row.catalog_ref: row for row in await self._list_enabled_rows()
        }
        discovered_refs = {
            row.catalog_ref for row in await self._list_org_discovered_rows()
        }
        credentials_by_provider = {
            provider.value: await self.get_provider_credentials(provider.value)
            for provider in _BUILT_IN_PROVIDER_ORDER
        }
        items = [
            self._build_builtin_catalog_entry(
                row=row,
                enabled_rows_by_ref=enabled_rows_by_ref,
                discovered_refs=discovered_refs,
                credentials=credentials_by_provider[row.runtime_provider],
            )
            for row in get_builtin_catalog_models()
        ]
        normalized_query = query.strip().lower() if query else None
        if normalized_query:
            items = [
                item
                for item in items
                if normalized_query in item.display_name.lower()
                or normalized_query in item.model_name.lower()
                or normalized_query in item.runtime_provider.lower()
            ]
        if provider:
            items = [item for item in items if item.runtime_provider == provider]
        start = int(cursor) if cursor else 0
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
        enabled_rows = await self._filter_enabled_rows_for_workspace(
            await self._list_enabled_rows(), workspace_id
        )
        items = [self._catalog_entry_from_enabled_row(row) for row in enabled_rows]
        items.sort(key=lambda item: (item.source_name, item.display_name))
        return items

    async def list_discovered_models(self) -> list[ModelCatalogEntry]:
        """List the discovered catalog with org enablement flags."""
        discovered = [
            *await self._list_default_sidecar_rows(),
            *await self._list_org_discovered_rows(),
        ]
        enabled_refs = {row.catalog_ref for row in await self._list_enabled_rows()}
        items = [
            self._catalog_entry_from_row(row, enabled=row.catalog_ref in enabled_refs)
            for row in discovered
        ]
        items.sort(key=lambda item: (item.source_name, item.display_name))
        return items

    async def get_model_config(self, model_name: str) -> ModelConfig:
        """Get configuration for a specific model."""
        if model_name not in MODEL_CONFIGS:
            raise TracecatNotFoundError(f"Model {model_name} not found")
        return MODEL_CONFIGS[model_name]

    def _to_model_source_read(self, row: AgentModelSource) -> AgentModelSourceRead:
        source_config = self._deserialize_sensitive_config(row.encrypted_config)
        return AgentModelSourceRead(
            id=row.id,
            type=CustomModelSourceType(row.type),
            flavor=self._source_flavor(source_config),
            display_name=row.display_name,
            base_url=row.base_url,
            api_key_configured=bool(source_config.get("api_key")),
            api_key_header=row.api_key_header,
            api_version=row.api_version,
            discovery_status=ModelDiscoveryStatus(row.discovery_status),
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
                AgentModelSource.type.in_(
                    sorted(source_type.value for source_type in _CUSTOM_SOURCE_TYPES)
                ),
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
            raise TracecatNotFoundError(f"Model source {source_id} not found")
        return source

    @require_scope("agent:update")
    async def create_model_source(
        self, params: AgentModelSourceCreate
    ) -> AgentModelSourceRead:
        await self._validate_source_uniqueness(source_type=params.type)
        source = AgentModelSource(
            organization_id=self.organization_id,
            type=params.type.value,
            display_name=params.display_name,
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
            source_type=CustomModelSourceType(source.type), exclude_id=source.id
        )
        source_config = self._deserialize_sensitive_config(source.encrypted_config)
        if "display_name" in params.model_fields_set:
            source.display_name = params.display_name or source.display_name
        if "base_url" in params.model_fields_set:
            source.base_url = params.base_url
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
        await self.session.refresh(source)
        refreshed = await self.get_model_source(source.id)
        return self._to_model_source_read(refreshed)

    @require_scope("agent:update")
    async def delete_model_source(self, source_id: uuid.UUID) -> None:
        source = await self.get_model_source(source_id)
        await self._validate_source_uniqueness(
            source_type=CustomModelSourceType(source.type), exclude_id=source.id
        )
        await self.session.delete(source)
        await self.session.execute(
            delete(AgentEnabledModel).where(
                AgentEnabledModel.organization_id == self.organization_id,
                AgentEnabledModel.source_id == source_id,
            )
        )
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

    async def _fetch_openai_compatible_models(
        self,
        *,
        base_url: str,
        api_key: str | None,
        api_key_header: str | None,
        extra_headers: dict[str, str] | None = None,
    ) -> list[dict[str, object]]:
        headers = {
            **self._build_auth_headers(
                api_key=api_key,
                api_key_header=api_key_header,
            ),
            **(extra_headers or {}),
        }
        async with httpx.AsyncClient(timeout=20.0) as client:
            response: httpx.Response | None = None
            for path in ("/v1/models", "/models"):
                try:
                    response = await client.get(
                        f"{base_url.rstrip('/')}{path}", headers=headers
                    )
                    response.raise_for_status()
                    break
                except httpx.HTTPError:
                    response = None
            if response is None:
                raise TracecatNotFoundError("Failed to discover models from gateway")
        payload = response.json()
        if isinstance(payload, dict):
            items = payload.get("data") or payload.get("models") or []
        else:
            items = payload
        return [item for item in items if isinstance(item, dict)]

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
            raw_model_id = str(item.get("id") or item.get("name") or "").strip()
            if not raw_model_id:
                continue
            provider = self._display_provider_for_model(
                source_type=source_type,
                model_name=raw_model_id,
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
                    "catalog_ref": self._catalog_ref(
                        source_type=source_type,
                        source_id=source_id,
                        raw_model_id=raw_model_id,
                    ),
                    "model_name": raw_model_id,
                    "model_provider": provider,
                    "runtime_provider": self._provider_from_source_type(source_type),
                    "display_name": str(item.get("name") or raw_model_id),
                    "source_name": source_name,
                    "raw_model_id": raw_model_id,
                    "base_url": base_url,
                    "metadata": item,
                }
            )
        return normalized

    async def _discover_provider_models(
        self, source_type: ModelSourceType
    ) -> list[dict[str, object]]:
        provider = source_type.value
        credentials = await self.get_provider_credentials(provider) or {}
        source_name = PROVIDER_CREDENTIAL_CONFIGS[provider].label
        match source_type:
            case ModelSourceType.OPENAI:
                if not credentials:
                    raise TracecatNotFoundError("OpenAI credentials not configured")
                items = await self._fetch_openai_compatible_models(
                    base_url=credentials.get("OPENAI_BASE_URL")
                    or "https://api.openai.com",
                    api_key=credentials.get("OPENAI_API_KEY"),
                    api_key_header="Authorization",
                )
                return self._normalize_openai_compatible_entries(
                    source_type=source_type,
                    source_name=source_name,
                    items=items,
                    base_url=credentials.get("OPENAI_BASE_URL"),
                )
            case ModelSourceType.ANTHROPIC:
                if not credentials:
                    raise TracecatNotFoundError("Anthropic credentials not configured")
                items = await self._fetch_openai_compatible_models(
                    base_url=credentials.get("ANTHROPIC_BASE_URL")
                    or "https://api.anthropic.com",
                    api_key=credentials.get("ANTHROPIC_API_KEY"),
                    api_key_header="x-api-key",
                    extra_headers={"anthropic-version": "2023-06-01"},
                )
                return self._normalize_openai_compatible_entries(
                    source_type=source_type,
                    source_name=source_name,
                    items=items,
                    base_url=credentials.get("ANTHROPIC_BASE_URL"),
                )
            case ModelSourceType.GEMINI:
                if not (api_key := credentials.get("GEMINI_API_KEY")):
                    raise TracecatNotFoundError("Gemini credentials not configured")
                async with httpx.AsyncClient(timeout=20.0) as client:
                    response = await client.get(
                        "https://generativelanguage.googleapis.com/v1beta/models",
                        params={"key": api_key},
                    )
                    response.raise_for_status()
                payload = response.json()
                items = []
                for item in payload.get("models", []):
                    if not isinstance(item, dict):
                        continue
                    model_id = str(item.get("name") or "").removeprefix("models/")
                    if not model_id:
                        continue
                    item["id"] = model_id
                    items.append(item)
                return self._normalize_openai_compatible_entries(
                    source_type=source_type,
                    source_name=source_name,
                    items=items,
                )
            case _:
                if not credentials:
                    raise TracecatNotFoundError(
                        f"{provider} credentials not configured for discovery"
                    )
                return [
                    {
                        "catalog_ref": row.catalog_ref,
                        "model_name": row.model_name,
                        "model_provider": row.model_provider,
                        "runtime_provider": row.runtime_provider,
                        "display_name": row.display_name,
                        "source_name": source_name,
                        "raw_model_id": row.raw_model_id,
                        "base_url": None,
                        "metadata": row.metadata,
                    }
                    for row in get_builtin_catalog_by_provider().get(provider, ())
                    if row.enableable
                ]

    async def _discover_source_models(
        self, source: AgentModelSource
    ) -> list[dict[str, object]]:
        source_type = ModelSourceType(source.type)
        source_config = self._deserialize_sensitive_config(source.encrypted_config)
        match source_type:
            case ModelSourceType.OPENAI_COMPATIBLE_GATEWAY:
                if not source.base_url:
                    raise TracecatNotFoundError("Gateway source requires a base URL")
                items = await self._fetch_openai_compatible_models(
                    base_url=source.base_url,
                    api_key=source_config.get("api_key"),
                    api_key_header=source.api_key_header,
                )
                return self._normalize_openai_compatible_entries(
                    source_type=source_type,
                    source_name=source.display_name,
                    items=items,
                    source_id=source.id,
                    base_url=source.base_url,
                )
            case ModelSourceType.MANUAL_CUSTOM:
                declared = source.declared_models or []
                return [
                    {
                        "catalog_ref": self._catalog_ref(
                            source_type=source_type,
                            source_id=source.id,
                            raw_model_id=item["model_name"],
                        ),
                        "model_name": item["model_name"],
                        "model_provider": item.get("model_provider")
                        or self._display_provider_for_model(
                            source_type=source_type,
                            model_name=item["model_name"],
                            provider_hint=None,
                        ),
                        "runtime_provider": "manual_custom",
                        "display_name": item.get("display_name") or item["model_name"],
                        "source_name": source.display_name,
                        "raw_model_id": item["model_name"],
                        "base_url": source.base_url,
                        "metadata": {"declared": True},
                    }
                    for item in declared
                ]
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
            source_type=CustomModelSourceType(source.type), exclude_id=source.id
        )
        try:
            discovered = await self._discover_source_models(source)
            persisted = await self._upsert_discovered_models(
                source_type=ModelSourceType(source.type),
                source_name=source.display_name,
                source_id=source.id,
                models=discovered,
                organization_scoped=True,
            )
            source.discovery_status = ModelDiscoveryStatus.READY.value
            source.last_error = None
            source.last_refreshed_at = datetime.now(UTC)
            self.session.add(source)
            await self.session.commit()
            enabled_refs = {row.catalog_ref for row in await self._list_enabled_rows()}
            return [
                self._catalog_entry_from_row(
                    row, enabled=row.catalog_ref in enabled_refs
                )
                for row in persisted
            ]
        except Exception as exc:
            await self.session.rollback()
            source.discovery_status = ModelDiscoveryStatus.FAILED.value
            source.last_error = str(exc)
            source.last_refreshed_at = datetime.now(UTC)
            self.session.add(source)
            await self.session.commit()
            raise

    @require_scope("agent:update")
    async def refresh_default_sidecar_inventory(
        self, *, populate_defaults: bool = False
    ) -> DefaultModelInventoryRead:
        current_rows = await self._list_default_sidecar_rows()
        if populate_defaults and current_rows:
            return await self.get_default_sidecar_inventory()
        try:
            default_sidecar_cfg = self._get_default_sidecar_config()
            if default_sidecar_cfg["base_url"]:
                items = await self._fetch_openai_compatible_models(
                    base_url=str(default_sidecar_cfg["base_url"]),
                    api_key=(
                        str(default_sidecar_cfg["api_key"])
                        if default_sidecar_cfg["api_key"]
                        else None
                    ),
                    api_key_header=str(
                        default_sidecar_cfg["api_key_header"] or "Authorization"
                    ),
                )
                discovered = self._normalize_openai_compatible_entries(
                    source_type=ModelSourceType.DEFAULT_SIDECAR,
                    source_name=DEFAULT_SIDECAR_SOURCE_NAME,
                    items=items,
                    base_url=str(default_sidecar_cfg["base_url"]),
                )
            else:
                discovered = []
            await self._upsert_discovered_models(
                source_type=ModelSourceType.DEFAULT_SIDECAR,
                source_name=DEFAULT_SIDECAR_SOURCE_NAME,
                models=discovered,
                organization_scoped=False,
            )
            await self._set_platform_setting_value(
                key=DEFAULT_SIDECAR_STATE_SETTINGS["discovery_status"],
                value=ModelDiscoveryStatus.READY.value,
            )
            await self._set_platform_setting_value(
                key=DEFAULT_SIDECAR_STATE_SETTINGS["last_refreshed_at"],
                value=datetime.now(UTC).isoformat(),
            )
            await self._set_platform_setting_value(
                key=DEFAULT_SIDECAR_STATE_SETTINGS["last_error"],
                value=None,
            )
            await self.session.commit()
        except Exception as exc:
            await self.session.rollback()
            await self._set_platform_setting_value(
                key=DEFAULT_SIDECAR_STATE_SETTINGS["discovery_status"],
                value=ModelDiscoveryStatus.FAILED.value,
            )
            await self._set_platform_setting_value(
                key=DEFAULT_SIDECAR_STATE_SETTINGS["last_refreshed_at"],
                value=datetime.now(UTC).isoformat(),
            )
            await self._set_platform_setting_value(
                key=DEFAULT_SIDECAR_STATE_SETTINGS["last_error"],
                value=str(exc),
            )
            await self.session.commit()
            if not populate_defaults:
                raise
        return await self.get_default_sidecar_inventory()

    async def get_default_sidecar_inventory(self) -> DefaultModelInventoryRead:
        status = await self._get_default_sidecar_state_value("discovery_status")
        refreshed_at = await self._get_default_sidecar_state_value("last_refreshed_at")
        last_error = await self._get_default_sidecar_state_value("last_error")
        rows = await self._list_default_sidecar_rows()
        enabled_refs = {row.catalog_ref for row in await self._list_enabled_rows()}
        return DefaultModelInventoryRead(
            discovery_status=ModelDiscoveryStatus(
                str(status or ModelDiscoveryStatus.NEVER.value)
            ),
            last_refreshed_at=(
                datetime.fromisoformat(str(refreshed_at)) if refreshed_at else None
            ),
            last_error=str(last_error) if last_error else None,
            discovered_models=[
                self._catalog_entry_from_row(
                    row, enabled=row.catalog_ref in enabled_refs
                )
                for row in rows
            ],
        )

    @require_scope("agent:update")
    async def refresh_provider_inventory(self, provider: str) -> BuiltInProviderRead:
        try:
            source_type = ModelSourceType(provider)
        except ValueError as exc:
            raise TracecatNotFoundError(f"Provider {provider} not found") from exc
        if source_type not in _BUILT_IN_PROVIDER_SOURCE_TYPES:
            raise TracecatNotFoundError(f"Provider {provider} not found")

        now = datetime.now(UTC)
        try:
            discovered = await self._discover_provider_models(source_type)
            await self._upsert_discovered_models(
                source_type=source_type,
                source_name=self._provider_label(provider),
                models=discovered,
                organization_scoped=True,
            )
            await self._set_org_setting_value(
                key=self._provider_state_setting_key(
                    provider=provider, field="discovery_status"
                ),
                value=ModelDiscoveryStatus.READY.value,
            )
            await self._set_org_setting_value(
                key=self._provider_state_setting_key(
                    provider=provider, field="last_refreshed_at"
                ),
                value=now.isoformat(),
            )
            await self._set_org_setting_value(
                key=self._provider_state_setting_key(
                    provider=provider, field="last_error"
                ),
                value=None,
            )
            await self.session.commit()
        except Exception as exc:
            await self.session.rollback()
            await self._set_org_setting_value(
                key=self._provider_state_setting_key(
                    provider=provider, field="discovery_status"
                ),
                value=ModelDiscoveryStatus.FAILED.value,
            )
            await self._set_org_setting_value(
                key=self._provider_state_setting_key(
                    provider=provider, field="last_refreshed_at"
                ),
                value=now.isoformat(),
            )
            await self._set_org_setting_value(
                key=self._provider_state_setting_key(
                    provider=provider, field="last_error"
                ),
                value=str(exc),
            )
            await self.session.commit()
            raise

        return await self._build_provider_read(source_type)

    async def _get_catalog_row(self, catalog_ref: str) -> ResolvedCatalogRecord:
        stmt = select(AgentDiscoveredModel).where(
            AgentDiscoveredModel.catalog_ref == catalog_ref,
            (AgentDiscoveredModel.organization_id == self.organization_id)
            | (
                (
                    AgentDiscoveredModel.source_type
                    == ModelSourceType.DEFAULT_SIDECAR.value
                )
                & AgentDiscoveredModel.organization_id.is_(None)
            ),
        )
        row = (await self.session.execute(stmt)).scalar_one_or_none()
        if row is not None:
            return self._resolved_from_discovered(row)
        if builtin_row := get_builtin_catalog_by_ref().get(catalog_ref):
            return self._resolved_from_builtin(builtin_row)
        raise TracecatNotFoundError(f"Catalog entry {catalog_ref} not found")

    async def _resolve_enableable_catalog_row(
        self,
        catalog_ref: str,
        provider_credentials_cache: dict[str, dict[str, str] | None],
    ) -> ResolvedCatalogRecord:
        row = await self._get_catalog_row(catalog_ref)
        if builtin_row := get_builtin_catalog_by_ref().get(catalog_ref):
            provider = builtin_row.runtime_provider
            if provider not in provider_credentials_cache:
                provider_credentials_cache[
                    provider
                ] = await self.get_provider_credentials(provider)
            credentials = provider_credentials_cache[provider]
            if not builtin_row.enableable:
                raise TracecatNotFoundError(
                    builtin_row.readiness_message
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
        self, catalog_refs: list[str]
    ) -> list[ResolvedCatalogRecord]:
        provider_credentials_cache: dict[str, dict[str, str] | None] = {}
        seen_refs: set[str] = set()
        rows: list[ResolvedCatalogRecord] = []
        for catalog_ref in catalog_refs:
            if catalog_ref in seen_refs:
                continue
            seen_refs.add(catalog_ref)
            rows.append(
                await self._resolve_enableable_catalog_row(
                    catalog_ref,
                    provider_credentials_cache,
                )
            )
        if not rows:
            return []
        await self.session.execute(
            pg_insert(AgentEnabledModel)
            .values(
                [
                    {
                        "organization_id": self.organization_id,
                        "catalog_ref": row.catalog_ref,
                        "source_id": row.source_id,
                        "source_type": row.source_type.value,
                        "model_name": row.model_name,
                        "model_provider": row.model_provider,
                        "runtime_provider": row.runtime_provider,
                        "display_name": row.display_name,
                        "base_url": row.base_url,
                        "enabled_config": None,
                    }
                    for row in rows
                ]
            )
            .on_conflict_do_nothing(index_elements=["organization_id", "catalog_ref"])
        )
        await self.session.commit()
        return rows

    @require_scope("agent:update")
    async def enable_model(self, params: EnabledModelOperation) -> ModelCatalogEntry:
        rows = await self._enable_catalog_rows([params.catalog_ref])
        row = rows[0]
        return ModelCatalogEntry(
            catalog_ref=row.catalog_ref,
            model_name=row.model_name,
            model_provider=row.model_provider,
            runtime_provider=row.runtime_provider,
            display_name=row.display_name,
            source_type=row.source_type,
            source_name=row.source_name,
            source_id=row.source_id,
            base_url=row.base_url,
            enabled=True,
            last_refreshed_at=row.last_refreshed_at,
            metadata=row.metadata,
            enabled_config=None,
        )

    @require_scope("agent:update")
    async def enable_models(
        self, params: EnabledModelsBatchOperation
    ) -> list[ModelCatalogEntry]:
        rows = await self._enable_catalog_rows(params.catalog_refs)
        return [
            ModelCatalogEntry(
                catalog_ref=row.catalog_ref,
                model_name=row.model_name,
                model_provider=row.model_provider,
                runtime_provider=row.runtime_provider,
                display_name=row.display_name,
                source_type=row.source_type,
                source_name=row.source_name,
                source_id=row.source_id,
                base_url=row.base_url,
                enabled=True,
                last_refreshed_at=row.last_refreshed_at,
                metadata=row.metadata,
                enabled_config=None,
            )
            for row in rows
        ]

    async def _disable_catalog_refs(self, catalog_refs: list[str]) -> set[str]:
        unique_refs = list(dict.fromkeys(catalog_refs))
        if not unique_refs:
            return set()

        result = await self.session.execute(
            delete(AgentEnabledModel)
            .where(
                AgentEnabledModel.organization_id == self.organization_id,
                AgentEnabledModel.catalog_ref.in_(unique_refs),
            )
            .returning(AgentEnabledModel.catalog_ref)
        )
        disabled_refs = set(result.scalars().all())
        default_setting = await self.settings_service.get_org_setting(
            "agent_default_model_ref"
        )
        if (
            disabled_refs
            and default_setting
            and self.settings_service.get_value(default_setting) in disabled_refs
        ):
            await self.settings_service.update_org_setting(
                default_setting, SettingUpdate(value=None)
            )
        await self.session.commit()
        return disabled_refs

    def _normalize_enabled_model_config(
        self,
        *,
        runtime_provider: str,
        config: EnabledModelRuntimeConfig,
    ) -> dict[str, Any] | None:
        match runtime_provider:
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
    async def disable_model(self, catalog_ref: str) -> None:
        await self._disable_catalog_refs([catalog_ref])

    @require_scope("agent:update")
    async def disable_models(self, params: EnabledModelsBatchOperation) -> None:
        await self._disable_catalog_refs(params.catalog_refs)

    @require_scope("agent:update")
    async def update_enabled_model_config(
        self, params: EnabledModelRuntimeConfigUpdate
    ) -> ModelCatalogEntry:
        row = await self._get_enabled_row(params.catalog_ref)
        if row is None:
            raise TracecatNotFoundError(f"Enabled model {params.catalog_ref} not found")
        row.enabled_config = self._normalize_enabled_model_config(
            runtime_provider=row.runtime_provider,
            config=params.config,
        )
        self.session.add(row)
        await self.session.commit()
        await self.session.refresh(row)
        return self._catalog_entry_from_enabled_row(row)

    async def _resolve_legacy_default_model_ref(self) -> str | None:
        legacy_setting = await self.settings_service.get_org_setting(
            "agent_default_model"
        )
        if legacy_setting is None:
            return None
        legacy_model_name = self.settings_service.get_value(legacy_setting)
        if not legacy_model_name:
            return None
        discovered = await self.list_discovered_models()
        for item in discovered:
            if item.model_name == legacy_model_name:
                await self.set_default_model_ref(item.catalog_ref)
                return item.catalog_ref
        return None

    async def get_default_model(self) -> DefaultModelSelection | None:
        setting = await self.settings_service.get_org_setting("agent_default_model_ref")
        catalog_ref = self.settings_service.get_value(setting) if setting else None
        if not catalog_ref:
            catalog_ref = await self._resolve_legacy_default_model_ref()
        if not catalog_ref:
            return None
        row = await self._get_catalog_row(str(catalog_ref))
        return DefaultModelSelection(
            catalog_ref=row.catalog_ref,
            model_name=row.model_name,
            model_provider=row.model_provider,
            display_name=row.display_name,
        )

    @require_scope("agent:update")
    async def set_default_model_ref(self, catalog_ref: str) -> DefaultModelSelection:
        enabled_refs = {row.catalog_ref for row in await self._list_enabled_rows()}
        if catalog_ref not in enabled_refs:
            raise TracecatNotFoundError(f"Model {catalog_ref} is not enabled")
        setting = await self.settings_service.get_org_setting("agent_default_model_ref")
        if setting:
            await self.settings_service.update_org_setting(
                setting, SettingUpdate(value=catalog_ref)
            )
        else:
            await self.settings_service.create_org_setting(
                SettingCreate(
                    key="agent_default_model_ref",
                    value=catalog_ref,
                    value_type=ValueType.JSON,
                    is_sensitive=False,
                )
            )
        row = await self._get_catalog_row(catalog_ref)
        return DefaultModelSelection(
            catalog_ref=row.catalog_ref,
            model_name=row.model_name,
            model_provider=row.model_provider,
            display_name=row.display_name,
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
        self._ensure_builtin_provider(provider)
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
        self._ensure_builtin_provider(provider)
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
                credentials=await self.get_provider_credentials(provider),
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
        catalog_ref: str | None = None,
    ) -> dict[str, str]:
        source_type = catalog_entry.source_type
        if source_type == ModelSourceType.DEFAULT_SIDECAR:
            return {}
        if source_type in {
            ModelSourceType.OPENAI_COMPATIBLE_GATEWAY,
            ModelSourceType.MANUAL_CUSTOM,
        }:
            if catalog_entry.source_id is None:
                return {}
            source = await self.get_model_source(catalog_entry.source_id)
            source_config = self._deserialize_sensitive_config(source.encrypted_config)
            credentials = {}
            if api_key := source_config.get("api_key"):
                credentials["OPENAI_API_KEY"] = api_key
            return credentials
        credentials = (
            await self.get_provider_credentials(catalog_entry.runtime_provider) or {}
        )
        if catalog_ref is None:
            return credentials
        enabled_row = await self._get_enabled_row(catalog_ref)
        if (
            enabled_row
            and enabled_row.runtime_provider == ModelSourceType.BEDROCK.value
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

    async def get_runtime_credentials_for_catalog_ref(
        self,
        *,
        catalog_ref: str,
    ) -> dict[str, str]:
        row = await self._get_catalog_row(catalog_ref)
        return await self._get_runtime_credentials(
            catalog_entry=row,
            catalog_ref=catalog_ref,
        )

    async def _resolve_catalog_agent_config(
        self,
        *,
        catalog_ref: str,
    ) -> tuple[AgentConfig, dict[str, str]]:
        row = await self._get_catalog_row(catalog_ref)
        credentials = await self._get_runtime_credentials(
            catalog_entry=row,
            catalog_ref=catalog_ref,
        )
        if not credentials and row.source_type not in {
            ModelSourceType.DEFAULT_SIDECAR,
            ModelSourceType.OPENAI_COMPATIBLE_GATEWAY,
            ModelSourceType.MANUAL_CUSTOM,
        }:
            raise TracecatNotFoundError(
                f"No credentials found for provider '{row.runtime_provider}' at organization level. "
                "Please configure credentials for this provider first."
            )
        return (
            AgentConfig(
                model_name=row.model_name,
                model_provider=row.runtime_provider,
                model_catalog_ref=row.catalog_ref,
                model_source_type=row.source_type.value,
                model_source_id=row.source_id,
                base_url=row.base_url,
            ),
            credentials,
        )

    async def require_enabled_catalog_ref(
        self,
        catalog_ref: str,
        *,
        workspace_id: uuid.UUID | None = None,
    ) -> None:
        effective_workspace_id = workspace_id or self.role.workspace_id
        enabled_rows = await self._filter_enabled_rows_for_workspace(
            await self._list_enabled_rows(),
            effective_workspace_id,
        )
        enabled_refs = {row.catalog_ref for row in enabled_rows}
        if catalog_ref not in enabled_refs:
            raise TracecatNotFoundError(f"Model {catalog_ref} is not enabled")

    async def _resolve_preset_catalog_ref(
        self, preset_config: AgentConfig
    ) -> str | None:
        if preset_config.model_catalog_ref:
            await self.require_enabled_catalog_ref(
                preset_config.model_catalog_ref,
                workspace_id=self.role.workspace_id,
            )
            return preset_config.model_catalog_ref
        for item in await self.list_models(workspace_id=self.role.workspace_id):
            if (
                item.model_name == preset_config.model_name
                and item.model_provider == preset_config.model_provider
            ):
                return item.catalog_ref
        for item in await self.list_discovered_models():
            if (
                item.model_name == preset_config.model_name
                and item.model_provider == preset_config.model_provider
            ):
                return item.catalog_ref
        return None

    @contextlib.asynccontextmanager
    async def with_model_config(
        self,
        *,
        catalog_ref: str | None = None,
    ) -> AsyncIterator[AgentConfig]:
        """Yield the resolved default model configuration and runtime credentials."""
        if catalog_ref is None:
            if not (selection := await self.get_default_model()):
                raise TracecatNotFoundError("No default model set")
            target_catalog_ref = selection.catalog_ref
        else:
            await self.require_enabled_catalog_ref(catalog_ref)
            target_catalog_ref = catalog_ref
        model_config, credentials = await self._resolve_catalog_agent_config(
            catalog_ref=target_catalog_ref
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
        if catalog_ref := await self._resolve_preset_catalog_ref(preset_config):
            resolved_config, credentials = await self._resolve_catalog_agent_config(
                catalog_ref=catalog_ref
            )
            resolved_config.instructions = preset_config.instructions
            resolved_config.output_type = preset_config.output_type
            resolved_config.actions = preset_config.actions
            resolved_config.namespaces = preset_config.namespaces
            resolved_config.tool_approvals = preset_config.tool_approvals
            resolved_config.mcp_servers = preset_config.mcp_servers
            resolved_config.retries = preset_config.retries
            resolved_config.enable_internet_access = (
                preset_config.enable_internet_access
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

    sidecar_service = AgentManagementService(
        session,
        role=_bootstrap_org_role(org_ids[0]),
    )
    builtins_inventory = await sidecar_service.refresh_builtin_catalog()
    logger.info(
        "Completed built-in catalog gateway-startup sync",
        litellm_version=LITELLM_PINNED_VERSION,
        models=len(builtins_inventory.models),
    )
    sidecar_inventory = await sidecar_service.refresh_default_sidecar_inventory(
        populate_defaults=True
    )
    logger.info(
        "Completed default-sidecar gateway-startup sync",
        discovered_models=len(sidecar_inventory.discovered_models),
    )

    for org_id in org_ids:
        service = AgentManagementService(session, role=_bootstrap_org_role(org_id))
        await service._ensure_default_enabled_models()
        for provider in [
            provider_type.value for provider_type in _BUILT_IN_PROVIDER_ORDER
        ]:
            if not await service.check_provider_credentials(provider):
                continue
            try:
                provider_inventory = await service.refresh_provider_inventory(provider)
                logger.info(
                    "Completed built-in provider gateway-startup sync",
                    organization_id=str(org_id),
                    provider=provider,
                    discovered_models=len(provider_inventory.discovered_models),
                )
            except Exception as exc:
                logger.warning(
                    "Built-in provider gateway-startup sync failed",
                    organization_id=str(org_id),
                    provider=provider,
                    error=str(exc),
                )
                await session.rollback()
        source_ids = [source.id for source in await service.list_model_sources()]
        if not source_ids:
            logger.debug(
                "No org model sources to sync during gateway startup",
                organization_id=str(org_id),
            )
            continue
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


async def sync_model_catalogs_on_startup() -> None:
    logger.info("Attempting model catalog gateway-startup sync")
    try:
        async with get_async_session_context_manager() as session:
            acquired = await try_pg_advisory_lock(
                session,
                MODEL_CATALOG_STARTUP_SYNC_LOCK_KEY,
            )
            if not acquired:
                logger.info(
                    "Another process is handling model catalog gateway-startup sync, exiting"
                )
                return

            try:
                await _sync_model_catalogs_as_leader(session)
            finally:
                await pg_advisory_unlock(session, MODEL_CATALOG_STARTUP_SYNC_LOCK_KEY)
    except Exception as exc:
        logger.warning("Model catalog gateway-startup sync failed", error=str(exc))
