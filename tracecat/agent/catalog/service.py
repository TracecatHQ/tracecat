from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import ClassVar

import orjson
from sqlalchemy import case, delete, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat import config
from tracecat.admin.agent.schemas import PlatformCatalogEntry, PlatformCatalogRead
from tracecat.agent.builtin_catalog import (
    _is_agent_enableable,
    get_builtin_catalog_models,
)
from tracecat.agent.config import PROVIDER_CREDENTIAL_CONFIGS
from tracecat.agent.provider_config import (
    BUILT_IN_PROVIDER_ORDER,
    deserialize_secret_keyvalues,
    provider_base_url_key,
    provider_credentials_complete,
    provider_label,
    provider_runtime_target,
)
from tracecat.agent.schemas import (
    BuiltInCatalogEntry,
    BuiltInCatalogRead,
    BuiltInProviderRead,
    EnabledModelRuntimeConfig,
    ModelCatalogEntry,
    ModelSelection,
)
from tracecat.agent.types import ModelDiscoveryStatus, ModelSourceType
from tracecat.db.models import (
    AgentCatalog,
    AgentModelSelectionLink,
    AgentPreset,
    AgentPresetVersion,
    AgentSession,
    OrganizationSecret,
    OrganizationSetting,
    PlatformSetting,
)
from tracecat.secrets.constants import DEFAULT_SECRETS_ENVIRONMENT
from tracecat.secrets.encryption import decrypt_value, encrypt_value
from tracecat.service import BaseOrgService, BasePlatformService

BUILTIN_CATALOG_STATE_SETTINGS = {
    "discovery_status": "agent_builtin_catalog_discovery_status",
    "last_refreshed_at": "agent_builtin_catalog_last_refreshed_at",
    "last_error": "agent_builtin_catalog_last_error",
}
PRUNED_MODEL_NAME_SUFFIX = " [unavailable]"


def parse_catalog_offset(cursor: str | None) -> int:
    if cursor is None:
        return 0
    if not cursor.isdecimal():
        raise ValueError("Invalid cursor. Expected a non-negative integer offset.")
    return int(cursor)


# ---------------------------------------------------------------------------
# Platform-setting helpers (session-only, no org context required)
# ---------------------------------------------------------------------------


async def get_platform_setting_value(session: AsyncSession, key: str) -> object | None:
    stmt = select(PlatformSetting).where(PlatformSetting.key == key)
    setting = (await session.execute(stmt)).scalar_one_or_none()
    if setting is None:
        return None
    value_bytes = setting.value
    if setting.is_encrypted:
        value_bytes = decrypt_value(
            value_bytes,
            key=config.TRACECAT__DB_ENCRYPTION_KEY or "",
        )
    return orjson.loads(value_bytes)


async def set_platform_setting_value(
    session: AsyncSession,
    *,
    key: str,
    value: object,
    encrypted: bool = False,
) -> None:
    """Upsert a platform setting as a JSON blob, optionally encrypted."""
    stmt = select(PlatformSetting).where(PlatformSetting.key == key)
    setting = (await session.execute(stmt)).scalar_one_or_none()
    value_bytes = orjson.dumps(value)
    if encrypted:
        value_bytes = encrypt_value(
            value_bytes,
            key=config.TRACECAT__DB_ENCRYPTION_KEY or "",
        )
    if setting is None:
        session.add(
            PlatformSetting(
                key=key,
                value=value_bytes,
                value_type="json",
                is_encrypted=encrypted,
            )
        )
        return
    setting.value = value_bytes
    setting.is_encrypted = encrypted
    setting.value_type = "json"


async def load_catalog_state(
    session: AsyncSession,
) -> tuple[ModelDiscoveryStatus, datetime | None, str | None]:
    status = await get_platform_setting_value(
        session, BUILTIN_CATALOG_STATE_SETTINGS["discovery_status"]
    )
    refreshed_at = await get_platform_setting_value(
        session, BUILTIN_CATALOG_STATE_SETTINGS["last_refreshed_at"]
    )
    last_error = await get_platform_setting_value(
        session, BUILTIN_CATALOG_STATE_SETTINGS["last_error"]
    )
    return (
        ModelDiscoveryStatus(str(status or ModelDiscoveryStatus.NEVER.value)),
        datetime.fromisoformat(str(refreshed_at)) if refreshed_at else None,
        str(last_error) if last_error else None,
    )


class AgentCatalogService(BaseOrgService):
    """Builtin and platform catalog state, listing, and refresh behavior."""

    service_name: ClassVar[str] = "agent-catalog"

    async def get_builtin_catalog_state(
        self,
    ) -> tuple[ModelDiscoveryStatus, datetime | None, str | None]:
        return await load_catalog_state(self.session)

    async def _load_provider_credentials(
        self,
        provider: str,
    ) -> dict[str, str] | None:
        stmt = select(OrganizationSecret).where(
            OrganizationSecret.organization_id == self.organization_id,
            OrganizationSecret.name == f"agent-{provider}-credentials",
            OrganizationSecret.environment == DEFAULT_SECRETS_ENVIRONMENT,
        )
        secret = (await self.session.execute(stmt)).scalar_one_or_none()
        if secret is None:
            return None
        return deserialize_secret_keyvalues(secret.encrypted_keys)

    async def _list_builtin_catalog_rows(self) -> list[AgentCatalog]:
        stmt = (
            select(AgentCatalog)
            .where(AgentCatalog.organization_id.is_(None))
            .order_by(AgentCatalog.model_provider.asc(), AgentCatalog.model_name.asc())
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def _list_org_selection_link_map(
        self,
    ) -> dict[uuid.UUID, AgentModelSelectionLink]:
        stmt = select(AgentModelSelectionLink).where(
            AgentModelSelectionLink.organization_id == self.organization_id,
            AgentModelSelectionLink.workspace_id.is_(None),
        )
        links = list((await self.session.execute(stmt)).scalars().all())
        return {link.catalog_id: link for link in links}

    def _invalidated_model_name(self, model_name: str) -> str:
        if model_name.endswith(PRUNED_MODEL_NAME_SUFFIX):
            return model_name
        max_name_length = 500 - len(PRUNED_MODEL_NAME_SUFFIX)
        return f"{model_name[:max_name_length]}{PRUNED_MODEL_NAME_SUFFIX}"

    async def _prune_stale_platform_selection_links(
        self,
        stale_catalog_ids: set[uuid.UUID],
    ) -> None:
        if not stale_catalog_ids:
            return
        await self.session.execute(
            delete(AgentModelSelectionLink).where(
                AgentModelSelectionLink.catalog_id.in_(stale_catalog_ids)
            )
        )

    async def _clear_default_model_settings_for_stale_catalog_ids(
        self,
        stale_catalog_ids: set[uuid.UUID],
        stale_keys: set[tuple[str, str]],
    ) -> None:
        """Reset any org's default-model setting that points at a now-removed catalog entry.

        The default model is stored in two org settings:
        - "agent_default_model": either a JSON dict {source_id, model_provider, model_name}
          (new format) or a bare model-name string (legacy format).
        - "agent_default_model_ref": a JSON-encoded ModelSelection used to disambiguate
          the legacy bare-string format.

        We scan both across all orgs, collect those whose default points at a stale
        (provider, model_name) pair, and null them out so the UI doesn't show a
        dangling selection.
        """
        if not stale_catalog_ids and not stale_keys:
            return
        default_settings = (
            (
                await self.session.execute(
                    select(OrganizationSetting).where(
                        OrganizationSetting.key == "agent_default_model"
                    )
                )
            )
            .scalars()
            .all()
        )
        default_ref_settings = (
            (
                await self.session.execute(
                    select(OrganizationSetting).where(
                        OrganizationSetting.key == "agent_default_model_ref"
                    )
                )
            )
            .scalars()
            .all()
        )
        default_refs_by_org = {
            setting.organization_id: _decode_default_model_ref(setting.value)
            for setting in default_ref_settings
        }
        affected_org_ids: set[uuid.UUID] = set()
        for setting in default_settings:
            try:
                value = orjson.loads(setting.value)
            except orjson.JSONDecodeError:
                continue
            match value:
                # New format: full selection dict with explicit provider+model
                case {
                    "source_id": None,
                    "model_provider": str(model_provider),
                    "model_name": str(model_name),
                } if (model_provider, model_name) in stale_keys:
                    affected_org_ids.add(setting.organization_id)
                # Legacy format: bare model-name string — cross-ref against the
                # companion "agent_default_model_ref" setting to get the full identity
                case str(model_name):
                    ref_selection = default_refs_by_org.get(setting.organization_id)
                    if (
                        ref_selection is not None
                        and ref_selection.source_id is None
                        and (
                            ref_selection.model_provider,
                            ref_selection.model_name,
                        )
                        in stale_keys
                        and ref_selection.model_name == model_name
                    ):
                        affected_org_ids.add(setting.organization_id)
                    elif any(
                        stale_model_name == model_name
                        for _, stale_model_name in stale_keys
                    ):
                        affected_org_ids.add(setting.organization_id)
                case _:
                    continue
        if not affected_org_ids:
            return
        cleared_value = orjson.dumps(None)
        settings = (
            (
                await self.session.execute(
                    select(OrganizationSetting).where(
                        OrganizationSetting.organization_id.in_(affected_org_ids),
                        OrganizationSetting.key.in_(
                            ("agent_default_model", "agent_default_model_ref")
                        ),
                    )
                )
            )
            .scalars()
            .all()
        )
        for setting in settings:
            setting.value = cleared_value
            setting.is_encrypted = False

    async def _invalidate_stale_platform_dependents(
        self,
        stale_rows: list[AgentCatalog],
    ) -> None:
        """Clean up references when builtin catalog rows are removed during a refresh.

        When the committed platform catalog snapshot changes, some previously-known
        models disappear.
        This method cascades that removal across every surface that could reference
        those catalog rows:
        1. Delete org-level selection links (AgentModelSelectionLink) for the stale IDs.
        2. Clear any org's default-model setting that pointed at a stale model.
        3. Append " [unavailable]" to the model_name in AgentPreset and
           AgentPresetVersion so users can see the preset needs updating.
        4. Null out model_provider/model_name on AgentSession rows that referenced
           the stale models (sessions are ephemeral, so clearing is safe).
        """
        if not stale_rows:
            return
        stale_catalog_ids = {row.id for row in stale_rows}
        stale_keys = {(row.model_provider, row.model_name) for row in stale_rows}
        # Built-in rows can be referenced from multiple state surfaces, so the
        # invalidation pass prunes selection links first and then rewrites legacy
        # presets, versions, and sessions in one batch.
        await self._prune_stale_platform_selection_links(stale_catalog_ids)
        await self._clear_default_model_settings_for_stale_catalog_ids(
            stale_catalog_ids,
            stale_keys,
        )

        ordered_rows = list(stale_rows)
        preset_conditions = [
            (AgentPreset.source_id.is_(None))
            & (AgentPreset.model_provider == row.model_provider)
            & (AgentPreset.model_name == row.model_name)
            for row in ordered_rows
        ]
        await self.session.execute(
            update(AgentPreset)
            .where(or_(*preset_conditions))
            .values(
                model_name=case(
                    *[
                        (condition, self._invalidated_model_name(row.model_name))
                        for row, condition in zip(
                            ordered_rows, preset_conditions, strict=True
                        )
                    ],
                    else_=AgentPreset.model_name,
                ),
                base_url=None,
                updated_at=func.now(),
            )
        )

        version_conditions = [
            (AgentPresetVersion.source_id.is_(None))
            & (AgentPresetVersion.model_provider == row.model_provider)
            & (AgentPresetVersion.model_name == row.model_name)
            for row in ordered_rows
        ]
        await self.session.execute(
            update(AgentPresetVersion)
            .where(or_(*version_conditions))
            .values(
                model_name=case(
                    *[
                        (condition, self._invalidated_model_name(row.model_name))
                        for row, condition in zip(
                            ordered_rows, version_conditions, strict=True
                        )
                    ],
                    else_=AgentPresetVersion.model_name,
                ),
                base_url=None,
                updated_at=func.now(),
            )
        )

        session_conditions = [
            (AgentSession.source_id.is_(None))
            & (AgentSession.model_provider == row.model_provider)
            & (AgentSession.model_name == row.model_name)
            for row in ordered_rows
        ]
        await self.session.execute(
            update(AgentSession)
            .where(or_(*session_conditions))
            .values(
                model_provider=None,
                model_name=None,
                updated_at=func.now(),
            )
        )

    async def _upsert_builtin_catalog_rows(self) -> list[AgentCatalog]:
        builtin_rows = list(get_builtin_catalog_models())
        current_ids = {row.agent_catalog_id for row in builtin_rows}
        existing_rows = list(
            (
                await self.session.execute(
                    select(AgentCatalog).where(AgentCatalog.organization_id.is_(None))
                )
            )
            .scalars()
            .all()
        )
        stale_rows = [row for row in existing_rows if row.id not in current_ids]
        if stale_rows:
            await self._invalidate_stale_platform_dependents(stale_rows)
            await self.session.execute(
                delete(AgentCatalog).where(
                    AgentCatalog.id.in_([row.id for row in stale_rows])
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
        persisted_rows = list(
            (
                await self.session.execute(
                    select(AgentCatalog).where(AgentCatalog.id.in_(current_ids))
                )
            )
            .scalars()
            .all()
        )
        persisted_by_id = {row.id: row for row in persisted_rows}
        return [persisted_by_id[row.agent_catalog_id] for row in builtin_rows]

    def _build_builtin_catalog_entry(
        self,
        *,
        row: AgentCatalog,
        selection_links_by_catalog_id: dict[uuid.UUID, AgentModelSelectionLink],
        credentials: dict[str, str] | None,
        provider_status: ModelDiscoveryStatus,
    ) -> BuiltInCatalogEntry:
        credential_config = PROVIDER_CREDENTIAL_CONFIGS[row.model_provider]
        credentials_configured = provider_credentials_complete(
            provider=row.model_provider,
            credentials=credentials,
        )
        metadata = row.model_metadata or {}
        enableable, readiness_from_metadata = _is_agent_enableable(metadata)
        ready = enableable and credentials_configured
        if readiness_from_metadata is not None:
            readiness_message = readiness_from_metadata
        elif not credentials_configured:
            readiness_message = (
                f"Configure {credential_config.label} credentials to enable this model."
            )
        else:
            readiness_message = None
        selection_link = selection_links_by_catalog_id.get(row.id)
        return BuiltInCatalogEntry(
            model_provider=row.model_provider,
            model_name=row.model_name,
            source_type=ModelSourceType(row.model_provider).value,
            source_name=provider_label(row.model_provider),
            source_id=None,
            enabled=selection_link is not None,
            last_refreshed_at=row.last_refreshed_at,
            metadata=row.model_metadata,
            enabled_config=(
                EnabledModelRuntimeConfig.model_validate(selection_link.enabled_config)
                if selection_link and selection_link.enabled_config
                else None
            ),
            credential_provider=row.model_provider,
            credential_label=credential_config.label,
            credential_fields=credential_config.fields,
            credentials_configured=credentials_configured,
            discovered=provider_status == ModelDiscoveryStatus.READY,
            ready=ready,
            enableable=ready,
            runtime_target_configured=True,
            readiness_message=readiness_message,
        )

    def _catalog_entry_from_builtin_row(
        self,
        row: AgentCatalog,
        *,
        enabled: bool,
    ) -> ModelCatalogEntry:
        return ModelCatalogEntry(
            model_provider=row.model_provider,
            model_name=row.model_name,
            source_type=row.model_provider,
            source_name=provider_label(row.model_provider),
            source_id=None,
            base_url=None,
            enabled=enabled,
            last_refreshed_at=row.last_refreshed_at,
            metadata=row.model_metadata,
        )

    async def list_providers(
        self,
        *,
        configured_only: bool = True,
        include_discovered_models: bool = False,
    ) -> list[BuiltInProviderRead]:
        status, refreshed_at, last_error = await load_catalog_state(self.session)
        selection_links_by_catalog_id: dict[uuid.UUID, AgentModelSelectionLink] = {}
        rows_by_provider: dict[str, list[AgentCatalog]] = {}
        if include_discovered_models:
            selection_links_by_catalog_id = await self._list_org_selection_link_map()
            builtin_rows = await self._list_builtin_catalog_rows()
            for row in builtin_rows:
                rows_by_provider.setdefault(row.model_provider, []).append(row)
        providers: list[BuiltInProviderRead] = []
        # Provider cards follow a fixed provider order so the UI stays stable even
        # when the underlying catalog refresh changes model membership.
        for source_type in BUILT_IN_PROVIDER_ORDER:
            provider = source_type.value
            credentials = await self._load_provider_credentials(provider)
            credentials_configured = provider_credentials_complete(
                provider=provider,
                credentials=credentials,
            )
            if configured_only and not credentials_configured:
                continue
            providers.append(
                BuiltInProviderRead(
                    provider=provider,
                    label=PROVIDER_CREDENTIAL_CONFIGS[provider].label,
                    source_type=source_type,
                    credentials_configured=credentials_configured,
                    base_url=(
                        credentials.get(base_url_key)
                        if credentials
                        and (base_url_key := provider_base_url_key(provider))
                        else None
                    ),
                    runtime_target=provider_runtime_target(
                        provider=provider,
                        credentials=credentials,
                    ),
                    discovery_status=status,
                    last_refreshed_at=refreshed_at,
                    last_error=last_error,
                    discovered_models=[
                        self._catalog_entry_from_builtin_row(
                            row,
                            enabled=row.id in selection_links_by_catalog_id,
                        )
                        for row in rows_by_provider.get(provider, [])
                    ]
                    if include_discovered_models
                    else [],
                )
            )
        return providers

    async def list_builtin_catalog(
        self,
        *,
        query: str | None = None,
        provider: str | None = None,
        cursor: str | None = None,
        limit: int = 100,
    ) -> BuiltInCatalogRead:
        start = parse_catalog_offset(cursor)
        status, refreshed_at, last_error = await load_catalog_state(self.session)
        selection_links_by_catalog_id = await self._list_org_selection_link_map()
        credentials_by_provider = {
            source_type.value: await self._load_provider_credentials(source_type.value)
            for source_type in BUILT_IN_PROVIDER_ORDER
        }
        rows = await self._list_builtin_catalog_rows()
        items = [
            self._build_builtin_catalog_entry(
                row=row,
                selection_links_by_catalog_id=selection_links_by_catalog_id,
                credentials=credentials_by_provider[row.model_provider],
                provider_status=status,
            )
            for row in rows
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
            source_type.value: index
            for index, source_type in enumerate(BUILT_IN_PROVIDER_ORDER)
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
            now = datetime.now(UTC).isoformat()
            await set_platform_setting_value(
                self.session,
                key=BUILTIN_CATALOG_STATE_SETTINGS["discovery_status"],
                value=ModelDiscoveryStatus.READY.value,
            )
            await set_platform_setting_value(
                self.session,
                key=BUILTIN_CATALOG_STATE_SETTINGS["last_refreshed_at"],
                value=now,
            )
            await set_platform_setting_value(
                self.session,
                key=BUILTIN_CATALOG_STATE_SETTINGS["last_error"],
                value=None,
            )
            await self.session.commit()
        except Exception as exc:
            await self.session.rollback()
            now = datetime.now(UTC).isoformat()
            await set_platform_setting_value(
                self.session,
                key=BUILTIN_CATALOG_STATE_SETTINGS["discovery_status"],
                value=ModelDiscoveryStatus.FAILED.value,
            )
            await set_platform_setting_value(
                self.session,
                key=BUILTIN_CATALOG_STATE_SETTINGS["last_refreshed_at"],
                value=now,
            )
            await set_platform_setting_value(
                self.session,
                key=BUILTIN_CATALOG_STATE_SETTINGS["last_error"],
                value=str(exc),
            )
            await self.session.commit()
            raise
        return await self.list_builtin_catalog()


class AdminAgentCatalogService(BasePlatformService):
    """Platform-admin catalog operations (superuser, no org context)."""

    service_name: ClassVar[str] = "admin-agent-catalog"

    async def list_platform_catalog(
        self,
        *,
        query: str | None = None,
        provider: str | None = None,
        cursor: str | None = None,
        limit: int = 100,
    ) -> PlatformCatalogRead:
        status, refreshed_at, last_error = await load_catalog_state(self.session)
        bounded_limit = max(1, min(limit, 200))
        offset = parse_catalog_offset(cursor)
        stmt = (
            select(
                AgentCatalog.id,
                AgentCatalog.model_provider,
                AgentCatalog.model_name,
                AgentCatalog.model_metadata,
            )
            .where(AgentCatalog.organization_id.is_(None))
            .order_by(AgentCatalog.model_provider.asc(), AgentCatalog.model_name.asc())
            .offset(offset)
            .limit(bounded_limit + 1)
        )
        if provider:
            stmt = stmt.where(AgentCatalog.model_provider == provider)
        if normalized_query := query.strip().lower() if query else None:
            like_pattern = f"%{normalized_query}%"
            stmt = stmt.where(
                func.lower(AgentCatalog.model_name).like(like_pattern)
                | func.lower(AgentCatalog.model_provider).like(like_pattern)
            )
        rows = (await self.session.execute(stmt)).tuples().all()
        page_rows = rows[:bounded_limit]
        next_cursor = str(offset + bounded_limit) if len(rows) > bounded_limit else None
        return PlatformCatalogRead(
            discovery_status=status,
            last_refreshed_at=refreshed_at,
            last_error=last_error,
            next_cursor=next_cursor,
            models=[
                PlatformCatalogEntry(
                    id=id,
                    model_provider=model_provider,
                    model_name=model_name,
                    metadata=model_metadata,
                )
                for id, model_provider, model_name, model_metadata in page_rows
            ],
        )

    async def refresh_platform_catalog(self) -> PlatformCatalogRead:
        try:
            builtin_rows = list(get_builtin_catalog_models())
            current_ids = {row.agent_catalog_id for row in builtin_rows}
            existing_rows = list(
                (
                    await self.session.execute(
                        select(AgentCatalog).where(
                            AgentCatalog.organization_id.is_(None)
                        )
                    )
                )
                .scalars()
                .all()
            )
            stale_rows = [row for row in existing_rows if row.id not in current_ids]
            if stale_rows:
                stale_catalog_ids = {row.id for row in stale_rows}
                await self.session.execute(
                    delete(AgentModelSelectionLink).where(
                        AgentModelSelectionLink.catalog_id.in_(stale_catalog_ids)
                    )
                )
                await self.session.execute(
                    delete(AgentCatalog).where(AgentCatalog.id.in_(stale_catalog_ids))
                )
            if builtin_rows:
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
            now_iso = datetime.now(UTC).isoformat()
            await set_platform_setting_value(
                self.session,
                key=BUILTIN_CATALOG_STATE_SETTINGS["discovery_status"],
                value=ModelDiscoveryStatus.READY.value,
            )
            await set_platform_setting_value(
                self.session,
                key=BUILTIN_CATALOG_STATE_SETTINGS["last_refreshed_at"],
                value=now_iso,
            )
            await set_platform_setting_value(
                self.session,
                key=BUILTIN_CATALOG_STATE_SETTINGS["last_error"],
                value=None,
            )
            await self.session.commit()
        except Exception as exc:
            await self.session.rollback()
            now_iso = datetime.now(UTC).isoformat()
            await set_platform_setting_value(
                self.session,
                key=BUILTIN_CATALOG_STATE_SETTINGS["discovery_status"],
                value=ModelDiscoveryStatus.FAILED.value,
            )
            await set_platform_setting_value(
                self.session,
                key=BUILTIN_CATALOG_STATE_SETTINGS["last_refreshed_at"],
                value=now_iso,
            )
            await set_platform_setting_value(
                self.session,
                key=BUILTIN_CATALOG_STATE_SETTINGS["last_error"],
                value=str(exc),
            )
            await self.session.commit()
            raise
        return await self.list_platform_catalog()


def _decode_default_model_ref(value: bytes) -> ModelSelection | None:
    # The setting stores a JSON string that itself contains a serialized selection,
    # so we have to unwrap two decode layers before validation.
    try:
        payload = orjson.loads(value)
    except orjson.JSONDecodeError:
        return None
    if not isinstance(payload, str) or not payload:
        return None
    try:
        selection_payload = orjson.loads(payload)
    except orjson.JSONDecodeError:
        return None
    if not isinstance(selection_payload, dict):
        return None
    try:
        return ModelSelection.model_validate(selection_payload)
    except Exception:
        return None
