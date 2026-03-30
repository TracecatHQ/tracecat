from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, cast
from urllib.parse import urlsplit, urlunsplit

import httpx
from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from tracecat.agent.provider_config import (
    deserialize_source_config,
    openai_compatible_runtime_base_url,
    serialize_source_config,
    source_runtime_base_url,
    source_type_from_row,
)
from tracecat.agent.schemas import (
    AgentModelSourceCreate,
    AgentModelSourceRead,
    AgentModelSourceUpdate,
    ManualDiscoveredModel,
    ModelCatalogEntry,
)
from tracecat.agent.selections.service import AgentSelectionsService
from tracecat.agent.types import (
    CustomModelSourceFlavor,
    CustomModelSourceType,
    ModelDiscoveryStatus,
    ModelSourceType,
    parse_custom_source_flavor,
)
from tracecat.authz.controls import require_scope
from tracecat.db.models import AgentCatalog, AgentSource
from tracecat.exceptions import TracecatNotFoundError
from tracecat.service import BaseOrgService


@dataclass(frozen=True, slots=True)
class SourceDiscoveryResult:
    """Raw discovery payload plus an optional runtime base URL override."""

    models: list[dict[str, object]]
    runtime_base_url: str | None = None


class AgentSourceService(BaseOrgService):
    """Custom source CRUD, discovery, and catalog synchronization."""

    service_name = "agent-sources"

    def __init__(self, session, role=None):
        super().__init__(session, role=role)
        self.selections = AgentSelectionsService(session, role=role)

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
        self,
        source_config: dict[str, str],
    ) -> CustomModelSourceFlavor | None:
        return parse_custom_source_flavor(source_config.get("flavor"))

    def _sanitize_url_for_log(self, url: str) -> str:
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
            "custom-model-provider",
            "direct_endpoint",
        }:
            return normalized_provider_hint
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
        return "direct_endpoint"

    def _model_entry(
        self,
        *,
        catalog: AgentCatalog,
        source: AgentSource,
        enabled: bool,
    ) -> ModelCatalogEntry:
        source_config = deserialize_source_config(source.encrypted_config)
        return ModelCatalogEntry(
            model_provider=catalog.model_provider,
            model_name=catalog.model_name,
            source_type=(
                ModelSourceType.OPENAI_COMPATIBLE_GATEWAY.value
                if source_type_from_row(source)
                == CustomModelSourceType.OPENAI_COMPATIBLE_GATEWAY
                else ModelSourceType.MANUAL_CUSTOM.value
            ),
            source_name=source.display_name,
            source_id=source.id,
            base_url=source_runtime_base_url(source, source_config=source_config),
            enabled=enabled,
            last_refreshed_at=catalog.last_refreshed_at,
            metadata=catalog.model_metadata,
            enabled_config=None,
        )

    def _to_model_source_read(self, row: AgentSource) -> AgentModelSourceRead:
        source_config = deserialize_source_config(row.encrypted_config)
        return AgentModelSourceRead(
            id=row.id,
            type=source_type_from_row(row),
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

    async def _validate_source_type(self, source_type: CustomModelSourceType) -> None:
        if source_type not in {
            CustomModelSourceType.OPENAI_COMPATIBLE_GATEWAY,
            CustomModelSourceType.MANUAL_CUSTOM,
        }:
            raise ValueError(
                f"{source_type.value} is a built-in inventory input, not a user-created custom source."
            )

    async def get_model_source(self, source_id: uuid.UUID) -> AgentSource:
        stmt = select(AgentSource).where(
            AgentSource.organization_id == self.organization_id,
            AgentSource.id == source_id,
        )
        source = (await self.session.execute(stmt)).scalar_one_or_none()
        if source is None:
            raise TracecatNotFoundError(f"Source {source_id} not found")
        return source

    def _build_auth_headers(
        self,
        *,
        api_key: str | None,
        api_key_header: str | None,
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
            add(f"{prefix}/v1")
            add(f"{prefix}/v1/")
            add(prefix)
            add(f"{prefix}/")
        elif base.endswith("/models"):
            prefix = base.removesuffix("/models")
            add(f"{prefix}/v1")
            add(f"{prefix}/v1/")
            add(prefix)
            add(f"{prefix}/")
        elif base.endswith("/v1"):
            prefix = base.removesuffix("/v1")
            add(f"{base}/models")
            add(f"{base}/models/")
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
    ) -> SourceDiscoveryResult:
        headers = self._build_auth_headers(
            api_key=api_key,
            api_key_header=api_key_header,
        )
        async with httpx.AsyncClient(timeout=20.0) as client:
            errors: list[str] = []
            for url in self._openai_compatible_discovery_urls(base_url):
                sanitized_url = self._sanitize_url_for_log(url)
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
                normalized_items = [item for item in items if isinstance(item, dict)]
                return SourceDiscoveryResult(
                    models=normalized_items,
                    runtime_base_url=openai_compatible_runtime_base_url(url),
                )
        detail = ", ".join(errors[:4]) if errors else "no discovery endpoints tried"
        self.logger.warning(
            "OpenAI-compatible model discovery failed",
            organization_id=str(self.organization_id),
            base_url=self._sanitize_url_for_log(base_url),
            detail=detail,
        )
        raise TracecatNotFoundError("Failed to discover models from gateway")

    def _normalize_openai_compatible_entries(
        self,
        *,
        source_type: ModelSourceType,
        items: list[dict[str, object]],
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
        self, source: AgentSource
    ) -> SourceDiscoveryResult:
        # Discovery splits on the stored source type because gateways and manual
        # declarations produce different payload shapes and runtime metadata.
        source_type = (
            ModelSourceType.OPENAI_COMPATIBLE_GATEWAY
            if source_type_from_row(source)
            == CustomModelSourceType.OPENAI_COMPATIBLE_GATEWAY
            else ModelSourceType.MANUAL_CUSTOM
        )
        source_config = deserialize_source_config(source.encrypted_config)
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
                        items=cast(list[dict[str, object]], discovery.models),
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

    def _format_refresh_error(self, exc: Exception) -> str:
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

    async def _upsert_catalog_rows(
        self,
        *,
        source_id: uuid.UUID,
        models: list[dict[str, object]],
    ) -> list[AgentCatalog]:
        existing_rows = list(
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
        existing_by_identity = {
            (row.model_provider, row.model_name): row for row in existing_rows
        }
        models_by_identity: dict[tuple[str, str], dict[str, object]] = {}
        ordered_identities: list[tuple[str, str]] = []
        for model in models:
            identity = (str(model["model_provider"]), str(model["model_id"]))
            if identity not in models_by_identity:
                ordered_identities.append(identity)
            models_by_identity[identity] = model

        stale_rows = [
            row
            for identity, row in existing_by_identity.items()
            if identity not in models_by_identity
        ]
        if stale_rows:
            # Remove catalog rows that disappeared first so downstream cleanup can
            # invalidate only the now-stale identities.
            stale_catalog_ids = {row.id for row in stale_rows}
            await self.selections._invalidate_disabled_dependents(stale_catalog_ids)
            await self.selections._revalidate_default_model_setting(stale_catalog_ids)
            await self.session.execute(
                delete(AgentCatalog).where(AgentCatalog.id.in_(stale_catalog_ids))
            )

        if not models_by_identity:
            return []
        now = datetime.now(UTC)
        stmt = pg_insert(AgentCatalog).values(
            [
                {
                    "organization_id": self.organization_id,
                    "source_id": source_id,
                    "model_provider": model_provider,
                    "model_name": model_name,
                    "model_metadata": cast(
                        dict[str, Any] | None, model.get("metadata")
                    ),
                    "last_refreshed_at": now,
                }
                for model_provider, model_name in ordered_identities
                if (model := models_by_identity.get((model_provider, model_name)))
                is not None
            ]
        )
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
        rows = list(
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
        rows_by_identity = {(row.model_provider, row.model_name): row for row in rows}
        return [rows_by_identity[identity] for identity in ordered_identities]

    @require_scope("agent:read")
    async def list_model_sources(self) -> list[AgentModelSourceRead]:
        stmt = (
            select(AgentSource)
            .where(AgentSource.organization_id == self.organization_id)
            .order_by(AgentSource.display_name.asc())
        )
        rows = (await self.session.execute(stmt)).scalars().all()
        return [self._to_model_source_read(row) for row in rows]

    @require_scope("agent:update")
    async def create_model_source(
        self,
        params: AgentModelSourceCreate,
    ) -> AgentModelSourceRead:
        await self._validate_source_type(params.type)
        source = AgentSource(
            organization_id=self.organization_id,
            display_name=params.display_name,
            model_provider=(
                "openai_compatible_gateway"
                if params.type == CustomModelSourceType.OPENAI_COMPATIBLE_GATEWAY
                else None
            ),
            base_url=params.base_url,
            encrypted_config=serialize_source_config(
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
        self,
        source_id: uuid.UUID,
        params: AgentModelSourceUpdate,
    ) -> AgentModelSourceRead:
        source = await self.get_model_source(source_id)
        source_config = deserialize_source_config(source.encrypted_config)
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
        source.encrypted_config = serialize_source_config(source_config)
        self.session.add(source)
        await self.session.commit()
        await self.selections.ensure_default_enabled_models()
        await self.session.refresh(source)
        return self._to_model_source_read(source)

    @require_scope("agent:update")
    async def delete_model_source(self, source_id: uuid.UUID) -> None:
        source = await self.get_model_source(source_id)
        source_catalog_ids = set(
            (
                await self.session.execute(
                    select(AgentCatalog.id).where(
                        AgentCatalog.organization_id == self.organization_id,
                        AgentCatalog.source_id == source_id,
                    )
                )
            )
            .scalars()
            .all()
        )
        if source_catalog_ids:
            await self.selections._invalidate_disabled_dependents(source_catalog_ids)
            await self.selections._revalidate_default_model_setting(source_catalog_ids)
        await self.session.delete(source)
        await self.session.commit()

    @require_scope("agent:update")
    async def refresh_model_source(
        self,
        source_id: uuid.UUID,
    ) -> list[ModelCatalogEntry]:
        source = await self.get_model_source(source_id)
        try:
            discovery = await self._discover_source_models(source)
            source_config = deserialize_source_config(source.encrypted_config)
            if discovery.runtime_base_url:
                source_config["runtime_base_url"] = discovery.runtime_base_url
            else:
                source_config.pop("runtime_base_url", None)
            source.encrypted_config = serialize_source_config(source_config)
            persisted = await self._upsert_catalog_rows(
                source_id=source.id,
                models=discovery.models,
            )
            source.discovery_status = ModelDiscoveryStatus.READY.value
            source.last_error = None
            source.last_refreshed_at = datetime.now(UTC)
            self.session.add(source)
            await self.session.commit()
            await self.selections.ensure_default_enabled_models()
            # Recompute enabled state after the catalog write so the response
            # reflects the current org-level selections, not the pre-refresh view.
            enabled_catalog_ids = {
                catalog.id
                for _link, catalog, _source in await self.selections._list_selection_links(
                    workspace_id=None
                )
            }
            return [
                self._model_entry(
                    catalog=row,
                    source=source,
                    enabled=row.id in enabled_catalog_ids,
                )
                for row in persisted
            ]
        except Exception as exc:
            await self.session.rollback()
            source.discovery_status = ModelDiscoveryStatus.FAILED.value
            source.last_error = self._format_refresh_error(exc)
            source.last_refreshed_at = datetime.now(UTC)
            self.session.add(source)
            await self.session.commit()
            raise
