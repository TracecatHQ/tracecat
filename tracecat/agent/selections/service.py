from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import orjson
from sqlalchemy import delete, exists, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.agent.builtin_catalog import (
    _is_agent_enableable,
    get_builtin_catalog_models,
)
from tracecat.agent.legacy_model_matching import (
    LegacyCatalogMatch,
    resolve_accessible_catalog_match_for_model_name,
    resolve_accessible_catalog_match_for_provider_model,
    resolve_enabled_catalog_match_for_model_name,
)
from tracecat.agent.provider_config import (
    BUILT_IN_PROVIDER_SOURCE_TYPES,
    deserialize_secret_keyvalues,
    deserialize_source_config,
    provider_credentials_complete,
    provider_label,
    source_runtime_base_url,
    source_type_from_row,
)
from tracecat.agent.schemas import (
    DefaultModelSelection,
    EnabledModelOperation,
    EnabledModelRuntimeConfig,
    EnabledModelRuntimeConfigUpdate,
    EnabledModelsBatchOperation,
    ManualDiscoveredModel,
    ModelCatalogEntry,
    ModelSelection,
    WorkspaceModelSubsetRead,
    WorkspaceModelSubsetUpdate,
)
from tracecat.agent.types import (
    CustomModelSourceFlavor,
    CustomModelSourceType,
    ModelSourceType,
    parse_custom_source_flavor,
)
from tracecat.db.models import (
    AgentCatalog,
    AgentModelSelectionLink,
    AgentPreset,
    AgentPresetVersion,
    AgentSession,
    AgentSource,
    OrganizationSecret,
    Workspace,
)
from tracecat.exceptions import TracecatNotFoundError
from tracecat.secrets.constants import DEFAULT_SECRETS_ENVIRONMENT
from tracecat.service import BaseOrgService, requires_entitlement
from tracecat.settings.schemas import SettingCreate, SettingUpdate, ValueType
from tracecat.settings.service import SettingsService
from tracecat.tiers.enums import Entitlement

from .types import CatalogSelectionLookup, LegacyModelRepairSummary

ENABLE_ALL_MODELS_ON_UPGRADE_SETTING = "agent_enable_all_models_on_upgrade"
PRUNED_MODEL_NAME_SUFFIX = " [unavailable]"
LEGACY_CUSTOM_PROVIDER = "custom-model-provider"
LEGACY_CUSTOM_SOURCE_NAME = "Imported legacy custom model"


class AgentSelectionsService(BaseOrgService):
    """Organization and workspace model-selection links, defaults, and repair flows."""

    service_name = "agent-selections"

    def __init__(self, session: AsyncSession, role=None):
        super().__init__(session, role=role)
        self.settings_service = SettingsService(session, role=role)

    def _lookup_from_selection(
        self,
        selection: (
            ModelSelection | EnabledModelOperation | EnabledModelRuntimeConfigUpdate
        ),
    ) -> CatalogSelectionLookup:
        return CatalogSelectionLookup(
            source_id=selection.source_id,
            model_provider=selection.model_provider,
            model_name=selection.model_name,
        )

    def _selection_from_catalog(self, catalog: AgentCatalog) -> ModelSelection:
        return ModelSelection(
            source_id=catalog.source_id,
            model_provider=catalog.model_provider,
            model_name=catalog.model_name,
        )

    def _selection_from_match(
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

    def _source_flavor(
        self,
        source_config: dict[str, str],
    ) -> CustomModelSourceFlavor | None:
        return parse_custom_source_flavor(source_config.get("flavor"))

    def _catalog_source_type(
        self,
        *,
        catalog: AgentCatalog,
        source: AgentSource | None,
    ) -> ModelSourceType:
        if catalog.source_id is None:
            return ModelSourceType(catalog.model_provider)
        if (
            source is not None
            and source_type_from_row(source)
            == CustomModelSourceType.OPENAI_COMPATIBLE_GATEWAY
        ):
            return ModelSourceType.OPENAI_COMPATIBLE_GATEWAY
        return ModelSourceType.MANUAL_CUSTOM

    def _catalog_source_name(
        self,
        *,
        catalog: AgentCatalog,
        source: AgentSource | None,
    ) -> str:
        if catalog.source_id is None:
            return provider_label(catalog.model_provider)
        if source is not None:
            return source.display_name
        return "Custom source"

    def _model_entry(
        self,
        *,
        catalog: AgentCatalog,
        source: AgentSource | None,
        enabled: bool,
        enabled_config: dict[str, Any] | None = None,
        last_refreshed_at: datetime | None = None,
    ) -> ModelCatalogEntry:
        source_config = (
            deserialize_source_config(source.encrypted_config) if source else {}
        )
        metadata = catalog.model_metadata
        if source and (flavor := self._source_flavor(source_config)) is not None:
            metadata = {"source_flavor": flavor.value} | (catalog.model_metadata or {})
        return ModelCatalogEntry(
            model_provider=catalog.model_provider,
            model_name=catalog.model_name,
            source_type=self._catalog_source_type(catalog=catalog, source=source).value,
            source_name=self._catalog_source_name(catalog=catalog, source=source),
            source_id=catalog.source_id,
            base_url=(
                source_runtime_base_url(source, source_config=source_config)
                if source is not None
                else None
            ),
            enabled=enabled,
            last_refreshed_at=last_refreshed_at or catalog.last_refreshed_at,
            metadata=metadata,
            enabled_config=(
                EnabledModelRuntimeConfig.model_validate(enabled_config)
                if enabled_config
                else None
            ),
        )

    async def _load_provider_credentials(self, provider: str) -> dict[str, str] | None:
        stmt = select(OrganizationSecret).where(
            OrganizationSecret.organization_id == self.organization_id,
            OrganizationSecret.name == f"agent-{provider}-credentials",
            OrganizationSecret.environment == DEFAULT_SECRETS_ENVIRONMENT,
        )
        secret = (await self.session.execute(stmt)).scalar_one_or_none()
        if secret is None:
            return None
        return deserialize_secret_keyvalues(secret.encrypted_keys)

    async def _get_workspace(self, workspace_id: uuid.UUID) -> Workspace:
        stmt = select(Workspace).where(
            Workspace.id == workspace_id,
            Workspace.organization_id == self.organization_id,
        )
        workspace = (await self.session.execute(stmt)).scalar_one_or_none()
        if workspace is None:
            raise TracecatNotFoundError(f"Workspace {workspace_id} not found")
        return workspace

    async def _workspace_subset_exists(self, workspace_id: uuid.UUID) -> bool:
        stmt = select(
            exists().where(
                AgentModelSelectionLink.organization_id == self.organization_id,
                AgentModelSelectionLink.workspace_id == workspace_id,
            )
        )
        return bool((await self.session.execute(stmt)).scalar())

    async def _resolve_catalog_rows(
        self,
        lookups: list[CatalogSelectionLookup],
    ) -> dict[CatalogSelectionLookup, AgentCatalog]:
        if not lookups:
            return {}
        conditions = [
            (
                (
                    AgentCatalog.source_id.is_(None)
                    if lookup.source_id is None
                    else AgentCatalog.source_id == lookup.source_id
                )
                & (AgentCatalog.model_provider == lookup.model_provider)
                & (AgentCatalog.model_name == lookup.model_name)
            )
            for lookup in lookups
        ]
        stmt = (
            select(AgentCatalog)
            .where(
                or_(
                    AgentCatalog.organization_id == self.organization_id,
                    AgentCatalog.organization_id.is_(None),
                ),
                or_(*conditions),
            )
            .order_by(AgentCatalog.organization_id.is_(None).asc())
        )
        rows = list((await self.session.execute(stmt)).scalars().all())
        resolved: dict[CatalogSelectionLookup, AgentCatalog] = {}
        for row in rows:
            lookup = CatalogSelectionLookup(
                source_id=row.source_id,
                model_provider=row.model_provider,
                model_name=row.model_name,
            )
            resolved.setdefault(lookup, row)
        return resolved

    async def _resolve_catalog_row(
        self, lookup: CatalogSelectionLookup
    ) -> AgentCatalog:
        resolved = await self._resolve_catalog_rows([lookup])
        if row := resolved.get(lookup):
            return row
        raise TracecatNotFoundError(
            f"Catalog entry {lookup.model_provider}/{lookup.model_name} not found"
        )

    async def _load_sources_by_id(
        self,
        source_ids: set[uuid.UUID],
    ) -> dict[uuid.UUID, AgentSource]:
        if not source_ids:
            return {}
        stmt = select(AgentSource).where(AgentSource.id.in_(source_ids))
        rows = list((await self.session.execute(stmt)).scalars().all())
        return {row.id: row for row in rows}

    async def _list_selection_links(
        self,
        *,
        workspace_id: uuid.UUID | None,
    ) -> list[tuple[AgentModelSelectionLink, AgentCatalog, AgentSource | None]]:
        stmt = (
            select(AgentModelSelectionLink, AgentCatalog, AgentSource)
            .join(AgentCatalog, AgentCatalog.id == AgentModelSelectionLink.catalog_id)
            .outerjoin(AgentSource, AgentSource.id == AgentCatalog.source_id)
            .where(
                AgentModelSelectionLink.organization_id == self.organization_id,
                AgentModelSelectionLink.workspace_id == workspace_id,
            )
            .order_by(AgentCatalog.model_provider.asc(), AgentCatalog.model_name.asc())
        )
        return list((await self.session.execute(stmt)).tuples().all())

    async def _get_org_selection_link(
        self,
        *,
        catalog_id: uuid.UUID,
    ) -> AgentModelSelectionLink | None:
        stmt = select(AgentModelSelectionLink).where(
            AgentModelSelectionLink.organization_id == self.organization_id,
            AgentModelSelectionLink.workspace_id.is_(None),
            AgentModelSelectionLink.catalog_id == catalog_id,
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def _resolve_enabled_catalog(
        self,
        lookup: CatalogSelectionLookup,
        *,
        workspace_id: uuid.UUID | None,
    ) -> tuple[AgentCatalog, AgentModelSelectionLink | None]:
        catalog = await self._resolve_catalog_row(lookup)
        # Org-level links are the source of truth. Workspace links only narrow
        # the visible set further when a subset exists for that workspace.
        stmt = select(
            exists().where(
                AgentModelSelectionLink.organization_id == self.organization_id,
                AgentModelSelectionLink.workspace_id.is_(None),
                AgentModelSelectionLink.catalog_id == catalog.id,
            )
        )
        org_enabled = bool((await self.session.execute(stmt)).scalar())
        if not org_enabled:
            raise TracecatNotFoundError(
                f"Model {lookup.model_provider}/{lookup.model_name} is not enabled"
            )
        if workspace_id is None:
            return catalog, await self._get_org_selection_link(catalog_id=catalog.id)
        if not await self._workspace_subset_exists(workspace_id):
            return catalog, await self._get_org_selection_link(catalog_id=catalog.id)
        workspace_stmt = select(AgentModelSelectionLink).where(
            AgentModelSelectionLink.organization_id == self.organization_id,
            AgentModelSelectionLink.workspace_id == workspace_id,
            AgentModelSelectionLink.catalog_id == catalog.id,
        )
        workspace_link = (
            await self.session.execute(workspace_stmt)
        ).scalar_one_or_none()
        if workspace_link is None:
            raise TracecatNotFoundError(
                f"Model {lookup.model_provider}/{lookup.model_name} is not enabled"
            )
        return catalog, workspace_link

    async def _disable_catalog_ids(
        self,
        catalog_ids: list[uuid.UUID],
    ) -> set[uuid.UUID]:
        if not catalog_ids:
            return set()
        result = await self.session.execute(
            delete(AgentModelSelectionLink)
            .where(
                AgentModelSelectionLink.organization_id == self.organization_id,
                AgentModelSelectionLink.workspace_id.is_(None),
                AgentModelSelectionLink.catalog_id.in_(catalog_ids),
            )
            .returning(AgentModelSelectionLink.catalog_id)
        )
        disabled_catalog_ids = {catalog_id for (catalog_id,) in result.tuples().all()}
        if not disabled_catalog_ids:
            return set()
        await self._invalidate_disabled_dependents(disabled_catalog_ids)
        await self._revalidate_default_model_setting(disabled_catalog_ids)
        await self.session.commit()
        return disabled_catalog_ids

    async def _invalidate_disabled_dependents(
        self,
        disabled_catalog_ids: set[uuid.UUID],
    ) -> None:
        if not disabled_catalog_ids:
            return
        rows = list(
            (
                await self.session.execute(
                    select(AgentCatalog).where(
                        AgentCatalog.id.in_(disabled_catalog_ids)
                    )
                )
            )
            .scalars()
            .all()
        )
        if not rows:
            return
        workspace_ids = select(Workspace.id).where(
            Workspace.organization_id == self.organization_id
        )
        for row in rows:
            await self.session.execute(
                update(AgentPreset)
                .where(
                    AgentPreset.workspace_id.in_(workspace_ids),
                    (
                        AgentPreset.source_id.is_(None)
                        if row.source_id is None
                        else AgentPreset.source_id == row.source_id
                    ),
                    AgentPreset.model_provider == row.model_provider,
                    AgentPreset.model_name == row.model_name,
                )
                .values(
                    source_id=None,
                    model_name=self._invalidated_model_name(row.model_name),
                    base_url=None,
                    updated_at=func.now(),
                )
            )
            await self.session.execute(
                update(AgentPresetVersion)
                .where(
                    AgentPresetVersion.workspace_id.in_(workspace_ids),
                    (
                        AgentPresetVersion.source_id.is_(None)
                        if row.source_id is None
                        else AgentPresetVersion.source_id == row.source_id
                    ),
                    AgentPresetVersion.model_provider == row.model_provider,
                    AgentPresetVersion.model_name == row.model_name,
                )
                .values(
                    source_id=None,
                    model_name=self._invalidated_model_name(row.model_name),
                    base_url=None,
                    updated_at=func.now(),
                )
            )
            await self.session.execute(
                update(AgentSession)
                .where(
                    AgentSession.workspace_id.in_(workspace_ids),
                    (
                        AgentSession.source_id.is_(None)
                        if row.source_id is None
                        else AgentSession.source_id == row.source_id
                    ),
                    AgentSession.model_provider == row.model_provider,
                    AgentSession.model_name == row.model_name,
                )
                .values(
                    source_id=None,
                    model_provider=None,
                    model_name=None,
                    updated_at=func.now(),
                )
            )
            await self.session.execute(
                delete(AgentModelSelectionLink).where(
                    AgentModelSelectionLink.organization_id == self.organization_id,
                    AgentModelSelectionLink.workspace_id.is_not(None),
                    AgentModelSelectionLink.catalog_id == row.id,
                )
            )

    def _invalidated_model_name(self, model_name: str) -> str:
        if model_name.endswith(PRUNED_MODEL_NAME_SUFFIX):
            return model_name
        max_name_length = 500 - len(PRUNED_MODEL_NAME_SUFFIX)
        return f"{model_name[:max_name_length]}{PRUNED_MODEL_NAME_SUFFIX}"

    async def _revalidate_default_model_setting(
        self,
        disabled_catalog_ids: set[uuid.UUID],
    ) -> None:
        if not disabled_catalog_ids:
            return
        default_selection = await self._get_default_model_selection()
        if default_selection is None:
            return
        lookup = self._lookup_from_selection(default_selection)
        try:
            catalog = await self._resolve_catalog_row(lookup)
        except TracecatNotFoundError:
            await self._clear_default_model_selection()
            return
        if catalog.id in disabled_catalog_ids:
            await self._clear_default_model_selection()

    async def _get_default_model_ref_selection(self) -> ModelSelection | None:
        setting = await self.settings_service.get_org_setting("agent_default_model_ref")
        if setting is None:
            return None
        return self._decode_default_model_ref(self.settings_service.get_value(setting))

    async def _get_default_model_selection(self) -> ModelSelection | None:
        setting = await self.settings_service.get_org_setting("agent_default_model")
        if setting is None:
            return None
        value = self.settings_service.get_value(setting)
        if isinstance(value, dict):
            try:
                return ModelSelection.model_validate(value)
            except Exception:
                return None
        if not value:
            return None
        legacy_model_name = str(value)
        if ref_selection := await self._get_default_model_ref_selection():
            try:
                await self.require_enabled_model_selection(
                    ref_selection, workspace_id=None
                )
                await self._persist_default_model_selection(ref_selection)
                return ref_selection
            except TracecatNotFoundError:
                pass
        match_result = await resolve_enabled_catalog_match_for_model_name(
            self.session,
            organization_id=self.organization_id,
            model_name=legacy_model_name,
        )
        selection = self._selection_from_match(match_result)
        if selection is None:
            return None
        await self._persist_default_model_selection(selection)
        return selection

    async def _persist_default_model_selection(
        self,
        selection: ModelSelection,
    ) -> DefaultModelSelection:
        await self.require_enabled_model_selection(selection, workspace_id=None)
        value = selection.model_dump(mode="json")
        if setting := await self.settings_service.get_org_setting(
            "agent_default_model"
        ):
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
        ref_value = self._encode_default_model_ref(selection)
        if ref_setting := await self.settings_service.get_org_setting(
            "agent_default_model_ref"
        ):
            await self.settings_service.update_org_setting(
                ref_setting,
                SettingUpdate(value=ref_value),
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
        catalog = await self._resolve_catalog_row(
            self._lookup_from_selection(selection)
        )
        source = None
        if catalog.source_id is not None:
            source = (await self._load_sources_by_id({catalog.source_id})).get(
                catalog.source_id
            )
        return DefaultModelSelection(
            source_id=catalog.source_id,
            model_provider=catalog.model_provider,
            model_name=catalog.model_name,
            source_type=self._catalog_source_type(catalog=catalog, source=source),
            source_name=self._catalog_source_name(catalog=catalog, source=source),
        )

    async def _clear_default_model_selection(self) -> None:
        for key in ("agent_default_model", "agent_default_model_ref"):
            if setting := await self.settings_service.get_org_setting(key):
                await self.settings_service.update_org_setting(
                    setting, SettingUpdate(value=None)
                )

    async def list_models(
        self,
        *,
        workspace_id: uuid.UUID | None = None,
    ) -> list[ModelCatalogEntry]:
        effective_workspace_id = None
        if workspace_id is not None:
            await self._get_workspace(workspace_id)
            # Once a workspace has its own subset, listing should reflect only
            # those explicit links instead of the org-wide selection set.
            if await self._workspace_subset_exists(workspace_id):
                effective_workspace_id = workspace_id
        rows = await self._list_selection_links(workspace_id=effective_workspace_id)
        items = [
            self._model_entry(
                catalog=catalog,
                source=source,
                enabled=True,
                enabled_config=link.enabled_config,
                last_refreshed_at=link.updated_at,
            )
            for link, catalog, source in rows
        ]
        items.sort(
            key=lambda item: ((item.source_name or "").lower(), item.model_name.lower())
        )
        return items

    async def get_workspace_model_subset(
        self,
        workspace_id: uuid.UUID,
    ) -> WorkspaceModelSubsetRead:
        await self._get_workspace(workspace_id)
        rows = await self._list_selection_links(workspace_id=workspace_id)
        return WorkspaceModelSubsetRead(
            inherit_all=not rows,
            models=sorted(
                [
                    self._selection_from_catalog(catalog)
                    for _link, catalog, _source in rows
                ],
                key=lambda item: (
                    str(item.source_id) if item.source_id is not None else "",
                    item.model_provider,
                    item.model_name,
                ),
            ),
        )

    @requires_entitlement(Entitlement.AGENT_ADDONS)
    async def replace_workspace_model_subset(
        self,
        workspace_id: uuid.UUID,
        params: WorkspaceModelSubsetUpdate,
    ) -> WorkspaceModelSubsetRead:
        await self._get_workspace(workspace_id)
        # Replace is implemented as delete-then-insert so the resulting link set
        # is obvious and the API stays idempotent.
        await self.session.execute(
            delete(AgentModelSelectionLink).where(
                AgentModelSelectionLink.organization_id == self.organization_id,
                AgentModelSelectionLink.workspace_id == workspace_id,
            )
        )
        if params.inherit_all:
            await self.session.commit()
            return await self.get_workspace_model_subset(workspace_id)
        if not params.models:
            raise ValueError(
                "Workspace subsets must include at least one model when inherit_all is false."
            )
        unique_lookups = list(
            dict.fromkeys(self._lookup_from_selection(item) for item in params.models)
        )
        catalog_rows = await self._resolve_catalog_rows(unique_lookups)
        missing = [lookup for lookup in unique_lookups if lookup not in catalog_rows]
        if missing:
            missing_lookup = missing[0]
            raise TracecatNotFoundError(
                f"Model {missing_lookup.model_provider}/{missing_lookup.model_name} is not enabled for the organization"
            )
        catalog_ids = [catalog_rows[lookup].id for lookup in unique_lookups]
        org_stmt = select(AgentModelSelectionLink.catalog_id).where(
            AgentModelSelectionLink.organization_id == self.organization_id,
            AgentModelSelectionLink.workspace_id.is_(None),
            AgentModelSelectionLink.catalog_id.in_(catalog_ids),
        )
        org_enabled_ids = set((await self.session.execute(org_stmt)).scalars().all())
        if missing_org_ids := [
            catalog_id
            for catalog_id in catalog_ids
            if catalog_id not in org_enabled_ids
        ]:
            missing_catalog = next(
                catalog
                for catalog in catalog_rows.values()
                if catalog.id == missing_org_ids[0]
            )
            raise TracecatNotFoundError(
                f"Model {missing_catalog.model_provider}/{missing_catalog.model_name} is not enabled for the organization"
            )
        await self.session.execute(
            pg_insert(AgentModelSelectionLink).values(
                [
                    {
                        "organization_id": self.organization_id,
                        "workspace_id": workspace_id,
                        "catalog_id": catalog_id,
                        "enabled_config": None,
                    }
                    for catalog_id in catalog_ids
                ]
            )
        )
        await self.session.commit()
        return await self.get_workspace_model_subset(workspace_id)

    async def clear_workspace_model_subset(self, workspace_id: uuid.UUID) -> None:
        await self._get_workspace(workspace_id)
        await self.session.execute(
            delete(AgentModelSelectionLink).where(
                AgentModelSelectionLink.organization_id == self.organization_id,
                AgentModelSelectionLink.workspace_id == workspace_id,
            )
        )
        await self.session.commit()

    async def _resolve_enableable_catalog(
        self, lookup: CatalogSelectionLookup
    ) -> AgentCatalog:
        catalog = await self._resolve_catalog_row(lookup)
        if catalog.source_id is not None:
            return catalog
        enableable, readiness_message = _is_agent_enableable(
            catalog.model_metadata or {}
        )
        if not enableable:
            raise TracecatNotFoundError(
                readiness_message or "This model cannot be enabled for agents."
            )
        credentials = await self._load_provider_credentials(catalog.model_provider)
        if not provider_credentials_complete(
            provider=catalog.model_provider,
            credentials=credentials,
        ):
            raise TracecatNotFoundError(
                f"No complete credentials found for provider '{catalog.model_provider}'. "
                "Please configure this provider first."
            )
        return catalog

    async def _enable_catalog_rows(
        self,
        lookups: list[CatalogSelectionLookup],
    ) -> list[AgentCatalog]:
        unique_lookups = list(dict.fromkeys(lookups))
        catalogs = [
            await self._resolve_enableable_catalog(lookup) for lookup in unique_lookups
        ]
        await self.session.execute(
            pg_insert(AgentModelSelectionLink)
            .values(
                [
                    {
                        "organization_id": self.organization_id,
                        "workspace_id": None,
                        "catalog_id": catalog.id,
                        "enabled_config": None,
                    }
                    for catalog in catalogs
                ]
            )
            .on_conflict_do_nothing(
                index_elements=["organization_id", "workspace_id", "catalog_id"]
            )
        )
        await self.session.commit()
        return catalogs

    @requires_entitlement(Entitlement.AGENT_ADDONS)
    async def enable_model(self, params: EnabledModelOperation) -> ModelCatalogEntry:
        lookup = self._lookup_from_selection(params)
        catalogs = await self._enable_catalog_rows([lookup])
        catalog = catalogs[0]
        link = await self._get_org_selection_link(catalog_id=catalog.id)
        if link is None:
            raise TracecatNotFoundError(
                f"Enabled model {params.model_provider}/{params.model_name} not found"
            )
        sources = await self._load_sources_by_id(
            {catalog.source_id} if catalog.source_id else set()
        )
        return self._model_entry(
            catalog=catalog,
            source=sources.get(catalog.source_id) if catalog.source_id else None,
            enabled=True,
            enabled_config=link.enabled_config,
            last_refreshed_at=link.updated_at,
        )

    @requires_entitlement(Entitlement.AGENT_ADDONS)
    async def enable_models(
        self,
        params: EnabledModelsBatchOperation,
    ) -> list[ModelCatalogEntry]:
        lookups = [
            self._lookup_from_selection(selection) for selection in params.models
        ]
        catalogs = await self._enable_catalog_rows(lookups)
        source_ids = {
            catalog.source_id for catalog in catalogs if catalog.source_id is not None
        }
        sources = await self._load_sources_by_id(source_ids)
        links = await self._list_selection_links(workspace_id=None)
        links_by_catalog_id = {catalog.id: link for link, catalog, _source in links}
        items: list[ModelCatalogEntry] = []
        for catalog in catalogs:
            link = links_by_catalog_id.get(catalog.id)
            items.append(
                self._model_entry(
                    catalog=catalog,
                    source=sources.get(catalog.source_id)
                    if catalog.source_id
                    else None,
                    enabled=True,
                    enabled_config=link.enabled_config if link is not None else None,
                    last_refreshed_at=link.updated_at if link is not None else None,
                )
            )
        return items

    @requires_entitlement(Entitlement.AGENT_ADDONS)
    async def disable_model(self, selection: ModelSelection) -> None:
        catalog = await self._resolve_catalog_row(
            self._lookup_from_selection(selection)
        )
        await self._disable_catalog_ids([catalog.id])

    @requires_entitlement(Entitlement.AGENT_ADDONS)
    async def disable_models(self, params: EnabledModelsBatchOperation) -> None:
        catalogs = [
            await self._resolve_catalog_row(self._lookup_from_selection(selection))
            for selection in params.models
        ]
        await self._disable_catalog_ids([catalog.id for catalog in catalogs])

    @requires_entitlement(Entitlement.AGENT_ADDONS)
    async def update_enabled_model_config(
        self,
        params: EnabledModelRuntimeConfigUpdate,
    ) -> ModelCatalogEntry:
        catalog = await self._resolve_catalog_row(
            CatalogSelectionLookup(
                source_id=params.source_id,
                model_provider=params.model_provider,
                model_name=params.model_name,
            )
        )
        link = await self._get_org_selection_link(catalog_id=catalog.id)
        if link is None:
            raise TracecatNotFoundError(
                f"Enabled model {params.model_provider}/{params.model_name} not found"
            )
        if (
            catalog.model_provider != "bedrock"
            and params.config.bedrock_inference_profile_id is not None
        ):
            raise ValueError(
                "Per-model inference profiles are currently only supported for Bedrock models."
            )
        normalized = {}
        if params.config.bedrock_inference_profile_id:
            normalized["bedrock_inference_profile_id"] = (
                params.config.bedrock_inference_profile_id.strip()
            )
        link.enabled_config = normalized or None
        self.session.add(link)
        await self.session.commit()
        await self.session.refresh(link)
        sources = await self._load_sources_by_id(
            {catalog.source_id} if catalog.source_id else set()
        )
        return self._model_entry(
            catalog=catalog,
            source=sources.get(catalog.source_id) if catalog.source_id else None,
            enabled=True,
            enabled_config=link.enabled_config,
            last_refreshed_at=link.updated_at,
        )

    async def is_model_enabled(
        self,
        lookup: CatalogSelectionLookup,
        *,
        workspace_id: uuid.UUID | None = None,
    ) -> bool:
        try:
            await self._resolve_enabled_catalog(lookup, workspace_id=workspace_id)
        except TracecatNotFoundError:
            return False
        return True

    async def require_enabled_model_selection(
        self,
        selection: ModelSelection,
        *,
        workspace_id: uuid.UUID | None = None,
    ) -> None:
        await self._resolve_enabled_catalog(
            self._lookup_from_selection(selection),
            workspace_id=workspace_id,
        )

    async def get_default_model(self) -> DefaultModelSelection | None:
        if not (selection := await self._get_default_model_selection()):
            return None
        catalog = await self._resolve_catalog_row(
            self._lookup_from_selection(selection)
        )
        source = None
        if catalog.source_id is not None:
            source = (await self._load_sources_by_id({catalog.source_id})).get(
                catalog.source_id
            )
        return DefaultModelSelection(
            source_id=catalog.source_id,
            model_provider=catalog.model_provider,
            model_name=catalog.model_name,
            source_type=self._catalog_source_type(catalog=catalog, source=source),
            source_name=self._catalog_source_name(catalog=catalog, source=source),
        )

    @requires_entitlement(Entitlement.AGENT_ADDONS)
    async def set_default_model_selection(
        self,
        selection: ModelSelection,
    ) -> DefaultModelSelection:
        return await self._persist_default_model_selection(selection)

    async def prune_unconfigured_builtin_model_selections(self) -> set[uuid.UUID]:
        rows = await self._list_selection_links(workspace_id=None)
        stale_catalog_ids: list[uuid.UUID] = []
        for _link, catalog, _source in rows:
            if catalog.source_id is None and catalog.model_provider in {
                source_type.value for source_type in BUILT_IN_PROVIDER_SOURCE_TYPES
            }:
                # Built-in provider rows are pruned here; source-backed rows are
                # handled by source refresh and legacy repair flows.
                credentials = await self._load_provider_credentials(
                    catalog.model_provider
                )
                if not provider_credentials_complete(
                    provider=catalog.model_provider,
                    credentials=credentials,
                ):
                    stale_catalog_ids.append(catalog.id)
        return await self._disable_catalog_ids(stale_catalog_ids)

    async def prune_stale_builtin_model_selections(self) -> set[uuid.UUID]:
        return set()

    async def ensure_default_enabled_models(self) -> None:
        upgrade_setting = await self.settings_service.get_org_setting(
            ENABLE_ALL_MODELS_ON_UPGRADE_SETTING
        )
        if not upgrade_setting:
            return
        rows = list(
            (
                await self.session.execute(
                    select(AgentCatalog).where(
                        or_(
                            AgentCatalog.organization_id == self.organization_id,
                            AgentCatalog.organization_id.is_(None),
                        )
                    )
                )
            )
            .scalars()
            .all()
        )
        if not rows:
            return
        eligible_catalog_ids: list[uuid.UUID] = []
        enableable_builtin_keys = {
            (row.model_provider, row.model_id)
            for row in get_builtin_catalog_models()
            if row.enableable
        }
        for row in rows:
            if row.source_id is not None:
                eligible_catalog_ids.append(row.id)
                continue
            credentials = await self._load_provider_credentials(row.model_provider)
            if (
                provider_credentials_complete(
                    provider=row.model_provider,
                    credentials=credentials,
                )
                and (row.model_provider, row.model_name) in enableable_builtin_keys
            ):
                eligible_catalog_ids.append(row.id)
        if not eligible_catalog_ids:
            return
        await self.session.execute(
            pg_insert(AgentModelSelectionLink)
            .values(
                [
                    {
                        "organization_id": self.organization_id,
                        "workspace_id": None,
                        "catalog_id": catalog_id,
                        "enabled_config": None,
                    }
                    for catalog_id in eligible_catalog_ids
                ]
            )
            .on_conflict_do_nothing(
                index_elements=["organization_id", "workspace_id", "catalog_id"]
            )
        )
        await self.session.commit()
        await self.settings_service.delete_org_setting(upgrade_setting)

    async def _get_legacy_custom_provider_secret_config(self) -> dict[str, str] | None:
        secret = await self._load_provider_credentials(LEGACY_CUSTOM_PROVIDER)
        if not secret:
            return None
        if not (base_url := secret.get("CUSTOM_MODEL_PROVIDER_BASE_URL", "").strip()):
            return None
        if not (
            model_name := secret.get("CUSTOM_MODEL_PROVIDER_MODEL_NAME", "").strip()
        ):
            return None
        result = {"base_url": base_url, "model_name": model_name}
        if api_key := secret.get("CUSTOM_MODEL_PROVIDER_API_KEY", "").strip():
            result["api_key"] = api_key
        return result

    async def _ensure_legacy_custom_provider_source_synced(self) -> None:
        secret_config = await self._get_legacy_custom_provider_secret_config()
        if not secret_config:
            return
        stmt = select(AgentSource).where(
            AgentSource.organization_id == self.organization_id,
            AgentSource.base_url == secret_config["base_url"],
        )
        rows = list((await self.session.execute(stmt)).scalars().all())
        source = None
        for row in rows:
            for item in row.declared_models or []:
                if (
                    item.get("model_name") == secret_config["model_name"]
                    and item.get("model_provider") == LEGACY_CUSTOM_PROVIDER
                ):
                    source = row
                    break
        declared_models = [
            ManualDiscoveredModel(
                model_name=secret_config["model_name"],
                display_name=secret_config["model_name"],
                model_provider=LEGACY_CUSTOM_PROVIDER,
            ).model_dump(mode="json")
        ]
        if source is None:
            source = AgentSource(
                organization_id=self.organization_id,
                display_name=LEGACY_CUSTOM_SOURCE_NAME,
                model_provider=LEGACY_CUSTOM_PROVIDER,
                base_url=secret_config["base_url"],
                declared_models=declared_models,
            )
            self.session.add(source)
            await self.session.flush()
        else:
            source.base_url = secret_config["base_url"]
            source.declared_models = declared_models
            self.session.add(source)
            await self.session.flush()
        # Ensure a catalog row exists for the legacy custom model so that
        # downstream match queries in repair_legacy_model_selections can
        # resolve it.
        catalog_stmt = pg_insert(AgentCatalog).values(
            organization_id=self.organization_id,
            source_id=source.id,
            model_provider=LEGACY_CUSTOM_PROVIDER,
            model_name=secret_config["model_name"],
            last_refreshed_at=datetime.now(UTC),
        )
        catalog_stmt = catalog_stmt.on_conflict_do_update(
            index_elements=["source_id", "model_provider", "model_name"],
            set_={
                "last_refreshed_at": catalog_stmt.excluded.last_refreshed_at,
                "updated_at": func.now(),
            },
        )
        await self.session.execute(catalog_stmt)
        await self.session.commit()

    async def repair_legacy_model_selections(self) -> LegacyModelRepairSummary:
        summary = LegacyModelRepairSummary()
        await self._ensure_legacy_custom_provider_source_synced()
        auto_enabled_catalog_ids: set[uuid.UUID] = set()

        async def ensure_enabled(selection: ModelSelection) -> AgentCatalog:
            # Legacy defaults and presets may point at a selection that has not
            # yet been recreated as a link-table row.
            catalog = await self._resolve_catalog_row(
                self._lookup_from_selection(selection)
            )
            if catalog.id not in auto_enabled_catalog_ids:
                await self._enable_catalog_rows(
                    [self._lookup_from_selection(selection)]
                )
                auto_enabled_catalog_ids.add(catalog.id)
            return catalog

        if legacy_setting := await self.settings_service.get_org_setting(
            "agent_default_model"
        ):
            legacy_value = self.settings_service.get_value(legacy_setting)
            if isinstance(legacy_value, str) and legacy_value:
                match_result = await resolve_accessible_catalog_match_for_model_name(
                    self.session,
                    organization_id=self.organization_id,
                    model_name=legacy_value,
                )
                selection = self._selection_from_match(match_result)
                if selection is not None:
                    try:
                        await ensure_enabled(selection)
                        await self.set_default_model_selection(selection)
                        summary.migrated_defaults += 1
                    except TracecatNotFoundError:
                        summary.unresolved_defaults += 1
                elif match_result.status == "ambiguous":
                    summary.ambiguous_defaults += 1
                else:
                    summary.unresolved_defaults += 1

        preset_rows = (
            (
                await self.session.execute(
                    select(AgentPreset, Workspace.id)
                    .join(Workspace, Workspace.id == AgentPreset.workspace_id)
                    .where(Workspace.organization_id == self.organization_id)
                )
            )
            .tuples()
            .all()
        )
        for preset, _workspace_id in preset_rows:
            match_result = await resolve_accessible_catalog_match_for_provider_model(
                self.session,
                organization_id=self.organization_id,
                model_provider=preset.model_provider,
                model_name=preset.model_name,
            )
            selection = self._selection_from_match(match_result)
            if selection is not None:
                try:
                    catalog = await ensure_enabled(selection)
                    if (
                        preset.source_id != catalog.source_id
                        or preset.model_provider != catalog.model_provider
                        or preset.model_name != catalog.model_name
                    ):
                        preset.source_id = catalog.source_id
                        preset.model_provider = catalog.model_provider
                        preset.model_name = catalog.model_name
                        self.session.add(preset)
                        summary.migrated_presets += 1
                except TracecatNotFoundError:
                    summary.unresolved_presets += 1
            elif match_result.status == "ambiguous":
                summary.ambiguous_presets += 1
            else:
                summary.unresolved_presets += 1

        version_rows = (
            (
                await self.session.execute(
                    select(AgentPresetVersion, Workspace.id)
                    .join(Workspace, Workspace.id == AgentPresetVersion.workspace_id)
                    .where(Workspace.organization_id == self.organization_id)
                )
            )
            .tuples()
            .all()
        )
        for version, _workspace_id in version_rows:
            match_result = await resolve_accessible_catalog_match_for_provider_model(
                self.session,
                organization_id=self.organization_id,
                model_provider=version.model_provider,
                model_name=version.model_name,
            )
            selection = self._selection_from_match(match_result)
            if selection is not None:
                try:
                    catalog = await ensure_enabled(selection)
                    if (
                        version.source_id != catalog.source_id
                        or version.model_provider != catalog.model_provider
                        or version.model_name != catalog.model_name
                    ):
                        version.source_id = catalog.source_id
                        version.model_provider = catalog.model_provider
                        version.model_name = catalog.model_name
                        self.session.add(version)
                        summary.migrated_versions += 1
                except TracecatNotFoundError:
                    summary.unresolved_versions += 1
            elif match_result.status == "ambiguous":
                summary.ambiguous_versions += 1
            else:
                summary.unresolved_versions += 1

        if (
            summary.migrated_defaults
            or summary.migrated_presets
            or summary.migrated_versions
        ):
            await self.session.commit()
        return summary
