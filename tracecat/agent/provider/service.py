"""Service for managing custom LLM providers."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import httpx
import orjson
import sqlalchemy as sa
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from tracecat.agent.catalog.service import AgentCatalogService
from tracecat.agent.provider.schemas import (
    AgentCustomProviderCreate,
    AgentCustomProviderRead,
    AgentCustomProviderUpdate,
)
from tracecat.agent.provider.types import ResolvedCustomProviderCredentials
from tracecat.audit.logger import audit_log
from tracecat.auth.secrets import get_db_encryption_key
from tracecat.authz.controls import require_scope
from tracecat.db.models import AgentCatalog, AgentCustomProvider, AgentModelAccess
from tracecat.exceptions import TracecatNotFoundError
from tracecat.pagination import (
    BaseCursorPaginator,
    CursorPaginatedResponse,
    CursorPaginationParams,
)
from tracecat.secrets.encryption import decrypt_keyvalues, decrypt_value, encrypt_value
from tracecat.service import BaseOrgService

CUSTOM_MODEL_PROVIDER_SLUG = "custom-model-provider"
_LEGACY_CUSTOM_PROVIDER_CONFIG_KEYS = {
    "CUSTOM_MODEL_PROVIDER_API_KEY": "api_key",
    "CUSTOM_MODEL_PROVIDER_BASE_URL": "base_url",
    "CUSTOM_MODEL_PROVIDER_MODEL_NAME": "model_name",
    "CUSTOM_MODEL_PROVIDER_PASSTHROUGH": "passthrough",
}


def _is_legacy_custom_provider_config(payload: Mapping[object, object]) -> bool:
    """Return true for migrated env-var-shaped custom provider config."""
    return any(key in payload for key in _LEGACY_CUSTOM_PROVIDER_CONFIG_KEYS)


def _normalize_legacy_custom_provider_config(
    payload: Mapping[object, object],
) -> dict[str, Any]:
    """Convert migrated custom-provider config to the CRUD config shape."""
    config: dict[str, Any] = {}
    for legacy_key, config_key in _LEGACY_CUSTOM_PROVIDER_CONFIG_KEYS.items():
        value = payload.get(legacy_key)
        if isinstance(value, str) and value:
            config[config_key] = value
    return config


class AgentCustomProviderService(BaseOrgService):
    """Manage organization-scoped custom LLM provider configurations."""

    service_name = "agent_custom_provider"

    @require_scope("agent:create")
    @audit_log(resource_type="agent_custom_provider", action="create")
    async def create_provider(
        self,
        provider: AgentCustomProviderCreate,
    ) -> AgentCustomProviderRead:
        """Create a new custom LLM provider for the organization."""
        encrypted_config = None
        secrets_dict: dict[str, object] = {}
        if provider.api_key:
            secrets_dict["api_key"] = provider.api_key
        if provider.custom_headers:
            secrets_dict["custom_headers"] = provider.custom_headers

        if secrets_dict:
            encrypted_config = encrypt_value(
                orjson.dumps(secrets_dict),
                key=get_db_encryption_key(),
            )

        model = AgentCustomProvider(
            organization_id=self.organization_id,
            display_name=provider.display_name,
            base_url=provider.base_url,
            passthrough=provider.passthrough,
            api_key_header=provider.api_key_header,
            encrypted_config=encrypted_config,
        )
        self.session.add(model)
        await self.session.commit()
        return AgentCustomProviderRead.model_validate(model)

    @require_scope("agent:read")
    async def get_provider(self, provider_id: UUID) -> AgentCustomProviderRead:
        """Get a specific provider by id scoped to this organization."""
        stmt = select(AgentCustomProvider).where(
            sa.and_(
                AgentCustomProvider.id == provider_id,
                AgentCustomProvider.organization_id == self.organization_id,
            )
        )
        model = (await self.session.execute(stmt)).scalar_one_or_none()
        if model is None:
            raise TracecatNotFoundError(
                f"Custom provider {provider_id} not found in organization"
            )
        return AgentCustomProviderRead.model_validate(model)

    @require_scope("agent:read")
    async def list_providers(
        self,
        params: CursorPaginationParams,
    ) -> CursorPaginatedResponse[AgentCustomProviderRead]:
        """List custom providers with cursor pagination."""
        stmt = (
            select(AgentCustomProvider)
            .where(AgentCustomProvider.organization_id == self.organization_id)
            .order_by(
                AgentCustomProvider.created_at.desc(),
                AgentCustomProvider.id.desc(),
            )
        )

        paginator = BaseCursorPaginator(self.session)
        if params.cursor:
            try:
                cursor_data = paginator.decode_cursor(params.cursor)
            except (ValueError, AttributeError) as err:
                raise ValueError("Invalid cursor") from err
            if not isinstance(cursor_data.sort_value, datetime):
                raise ValueError("Invalid cursor")
            stmt = stmt.where(
                sa.or_(
                    AgentCustomProvider.created_at < cursor_data.sort_value,
                    sa.and_(
                        AgentCustomProvider.created_at == cursor_data.sort_value,
                        AgentCustomProvider.id < UUID(cursor_data.id),
                    ),
                )
            )

        items = (
            (await self.session.execute(stmt.limit(params.limit + 1))).scalars().all()
        )
        has_more = len(items) > params.limit
        if has_more:
            items = items[: params.limit]

        next_cursor = None
        if has_more and items:
            last_item = items[-1]
            next_cursor = paginator.encode_cursor(
                last_item.id,
                sort_column="created_at",
                sort_value=last_item.created_at,
            )

        return CursorPaginatedResponse(
            items=[AgentCustomProviderRead.model_validate(m) for m in items],
            next_cursor=next_cursor,
            has_more=has_more,
        )

    def _decrypt_custom_provider_credentials(
        self,
        provider: AgentCustomProvider,
    ) -> ResolvedCustomProviderCredentials | None:
        """Decrypt the provider's stored execution credentials, if any."""
        raw_config = self._decrypt_custom_provider_config(provider)
        if not raw_config:
            return None

        api_key = raw_config.get("api_key")
        custom_headers = raw_config.get("custom_headers")
        resolved_headers: dict[str, str] | None = None
        if isinstance(custom_headers, dict):
            resolved_headers = {
                key: value
                for key, value in custom_headers.items()
                if isinstance(key, str) and isinstance(value, str)
            }

        return ResolvedCustomProviderCredentials(
            api_key=api_key if isinstance(api_key, str) else None,
            custom_headers=resolved_headers,
        )

    def _decrypt_custom_provider_config(
        self,
        provider: AgentCustomProvider,
    ) -> dict[str, Any]:
        """Decode CRUD and migrated custom-provider encrypted config blobs."""
        if provider.encrypted_config is None:
            return {}

        try:
            decrypted = decrypt_value(
                provider.encrypted_config,
                key=get_db_encryption_key(),
            )
            raw_config = orjson.loads(decrypted)
            if isinstance(raw_config, dict):
                if _is_legacy_custom_provider_config(raw_config):
                    return _normalize_legacy_custom_provider_config(raw_config)
                return raw_config
        except Exception:
            pass

        try:
            keyvalues = decrypt_keyvalues(
                provider.encrypted_config,
                key=get_db_encryption_key(),
            )
        except Exception:
            return {}

        legacy = {kv.key: kv.value.get_secret_value() for kv in keyvalues}
        config: dict[str, Any] = {}
        if api_key := legacy.get("CUSTOM_MODEL_PROVIDER_API_KEY"):
            config["api_key"] = api_key
        if base_url := legacy.get("CUSTOM_MODEL_PROVIDER_BASE_URL"):
            config["base_url"] = base_url
        if model_name := legacy.get("CUSTOM_MODEL_PROVIDER_MODEL_NAME"):
            config["model_name"] = model_name
        if passthrough := legacy.get("CUSTOM_MODEL_PROVIDER_PASSTHROUGH"):
            config["passthrough"] = passthrough
        return config

    @require_scope("agent:update")
    @audit_log(
        resource_type="agent_custom_provider",
        action="update",
        resource_id_attr="provider_id",
    )
    async def update_provider(
        self,
        provider_id: UUID,
        updates: AgentCustomProviderUpdate,
    ) -> AgentCustomProviderRead:
        """Update custom provider configuration."""
        stmt = select(AgentCustomProvider).where(
            sa.and_(
                AgentCustomProvider.id == provider_id,
                AgentCustomProvider.organization_id == self.organization_id,
            )
        )
        model = (await self.session.execute(stmt)).scalar_one_or_none()
        if model is None:
            raise TracecatNotFoundError(
                f"Custom provider {provider_id} not found in organization"
            )

        update_data = updates.model_dump(exclude_unset=True)
        incoming_secrets: dict[str, object] = {}
        if "api_key" in update_data:
            incoming_secrets["api_key"] = update_data.pop("api_key")
        if "custom_headers" in update_data:
            incoming_secrets["custom_headers"] = update_data.pop("custom_headers")

        if incoming_secrets:
            existing_secrets: dict[str, object] = self._decrypt_custom_provider_config(
                model
            )

            merged_secrets = {**existing_secrets}
            for key, value in incoming_secrets.items():
                if value:
                    merged_secrets[key] = value
                else:
                    merged_secrets.pop(key, None)

            if merged_secrets:
                model.encrypted_config = encrypt_value(
                    orjson.dumps(merged_secrets),
                    key=get_db_encryption_key(),
                )
            else:
                model.encrypted_config = None

        for key, value in update_data.items():
            setattr(model, key, value)
        await self.session.commit()
        await self.session.refresh(model)
        return AgentCustomProviderRead.model_validate(model)

    @require_scope("agent:delete")
    @audit_log(
        resource_type="agent_custom_provider",
        action="delete",
        resource_id_attr="provider_id",
    )
    async def delete_provider(self, provider_id: UUID) -> None:
        """Delete a custom provider and cascade its catalog rows."""
        stmt = select(AgentCustomProvider).where(
            sa.and_(
                AgentCustomProvider.id == provider_id,
                AgentCustomProvider.organization_id == self.organization_id,
            )
        )
        model = (await self.session.execute(stmt)).scalar_one_or_none()
        if model is None:
            raise TracecatNotFoundError(
                f"Custom provider {provider_id} not found in organization"
            )
        await self.session.delete(model)
        await self.session.commit()

    @require_scope("agent:update")
    @audit_log(
        resource_type="agent_custom_provider",
        action="update",
        resource_id_attr="provider_id",
    )
    async def refresh_provider_catalog(self, provider_id: UUID) -> None:
        """Discover models from the provider endpoint and upsert catalog rows."""
        provider_stmt = select(AgentCustomProvider).where(
            sa.and_(
                AgentCustomProvider.id == provider_id,
                AgentCustomProvider.organization_id == self.organization_id,
            )
        )
        provider = (await self.session.execute(provider_stmt)).scalar_one_or_none()
        if provider is None:
            raise TracecatNotFoundError(
                f"Custom provider {provider_id} not found in organization"
            )

        provider_config = self._decrypt_custom_provider_config(provider)
        fallback_base_url = provider_config.get("base_url")
        base_url = provider.base_url or (
            fallback_base_url if isinstance(fallback_base_url, str) else None
        )
        if not base_url:
            raise ValueError("Provider base_url not configured")

        api_key = provider_config.get("api_key")
        custom_headers = provider_config.get("custom_headers")

        models = await self._discover_models(
            base_url,
            api_key=api_key if isinstance(api_key, str) else None,
            custom_headers=(
                custom_headers if isinstance(custom_headers, dict) else None
            ),
            api_key_header=provider.api_key_header,
        )

        catalog_service = AgentCatalogService(session=self.session)
        await catalog_service.upsert_discovered_models(
            org_id=self.organization_id,
            custom_provider_id=provider_id,
            model_provider=CUSTOM_MODEL_PROVIDER_SLUG,
            models=models,
        )

        provider.last_refreshed_at = datetime.now(UTC)
        await self.session.commit()

        # Auto-grant org-wide access to every catalog row this custom provider
        # now exposes. Orgs without the ``agent_addons`` entitlement cannot
        # toggle per-model enablement, so discovering a model has to double as
        # enabling it; idempotent via the unique index on (org, workspace,
        # catalog).
        await self._auto_grant_custom_provider_access(provider_id)

    async def _auto_grant_custom_provider_access(self, provider_id: UUID) -> None:
        """Grant org-wide access to all catalog rows for a custom provider."""
        catalog_ids = (
            (
                await self.session.execute(
                    select(AgentCatalog.id).where(
                        AgentCatalog.organization_id == self.organization_id,
                        AgentCatalog.custom_provider_id == provider_id,
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
                "id": uuid4(),
                "organization_id": self.organization_id,
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

    @staticmethod
    async def _fetch_models(
        *,
        base_url: str,
        api_key: str | None,
        api_key_header: str | None,
        custom_headers: dict[str, str] | None,
        timeout: float,
    ) -> httpx.Response:
        """Make a GET /models request against a provider base URL."""
        headers = custom_headers.copy() if custom_headers else {}
        if api_key and api_key_header:
            headers[api_key_header] = api_key
        async with httpx.AsyncClient(timeout=timeout) as client:
            return await client.get(
                f"{base_url.rstrip('/')}/models",
                headers=headers,
            )

    async def validate_provider(
        self,
        base_url: str,
        api_key: str | None = None,
        api_key_header: str | None = None,
        custom_headers: dict[str, str] | None = None,
    ) -> bool:
        """Test provider connectivity."""
        if not base_url or not base_url.strip():
            return False
        try:
            response = await self._fetch_models(
                base_url=base_url,
                api_key=api_key,
                api_key_header=api_key_header,
                custom_headers=custom_headers,
                timeout=10.0,
            )
            return response.status_code == 200
        except Exception:
            return False

    async def _discover_models(
        self,
        base_url: str,
        api_key: str | None = None,
        custom_headers: dict[str, str] | None = None,
        api_key_header: str | None = None,
    ) -> list[dict[str, object]]:
        """Discover available models from a provider endpoint."""
        try:
            response = await self._fetch_models(
                base_url=base_url,
                api_key=api_key,
                api_key_header=api_key_header,
                custom_headers=custom_headers,
                timeout=30.0,
            )
            response.raise_for_status()
            data = response.json()
        except httpx.HTTPError as err:
            raise ValueError(f"Failed to discover models: {err}") from err
        if isinstance(data, dict) and isinstance(data.get("data"), list):
            return [item for item in data["data"] if isinstance(item, dict)]
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]
        raise ValueError(f"Unexpected response format: {type(data)}")
