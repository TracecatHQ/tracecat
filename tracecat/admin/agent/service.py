"""Platform-level agent catalog management service."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import ClassVar

import orjson
from sqlalchemy import delete, func, or_, select, tuple_
from sqlalchemy.dialects.postgresql import insert as pg_insert

from tracecat import config
from tracecat.admin.agent.schemas import PlatformCatalogEntry, PlatformCatalogRead
from tracecat.agent.builtin_catalog import get_builtin_catalog_models
from tracecat.agent.types import ModelDiscoveryStatus
from tracecat.db.models import (
    AgentCatalog,
    AgentEnabledModel,
    OrganizationSetting,
    PlatformSetting,
)
from tracecat.secrets.encryption import decrypt_value, encrypt_value
from tracecat.service import BasePlatformService

PLATFORM_CATALOG_STATE_SETTINGS = {
    "discovery_status": "agent_builtin_catalog_discovery_status",
    "last_refreshed_at": "agent_builtin_catalog_last_refreshed_at",
    "last_error": "agent_builtin_catalog_last_error",
}


class AdminAgentService(BasePlatformService):
    """Platform-level agent catalog management."""

    service_name: ClassVar[str] = "admin_agent"

    async def _get_platform_setting_value(self, key: str) -> object | None:
        stmt = select(PlatformSetting).where(PlatformSetting.key == key)
        setting = (await self.session.execute(stmt)).scalar_one_or_none()
        if setting is None:
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
        if setting is None:
            self.session.add(
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

    async def _get_catalog_state(
        self,
    ) -> tuple[ModelDiscoveryStatus, datetime | None, str | None]:
        status = await self._get_platform_setting_value(
            PLATFORM_CATALOG_STATE_SETTINGS["discovery_status"]
        )
        refreshed_at = await self._get_platform_setting_value(
            PLATFORM_CATALOG_STATE_SETTINGS["last_refreshed_at"]
        )
        last_error = await self._get_platform_setting_value(
            PLATFORM_CATALOG_STATE_SETTINGS["last_error"]
        )
        return (
            ModelDiscoveryStatus(str(status or ModelDiscoveryStatus.NEVER.value)),
            datetime.fromisoformat(str(refreshed_at)) if refreshed_at else None,
            str(last_error) if last_error else None,
        )

    async def _upsert_platform_catalog_rows(self) -> None:
        builtin_rows = list(get_builtin_catalog_models())
        current_keys = {(row.model_provider, row.model_id) for row in builtin_rows}
        stale_rows_stmt = select(
            AgentCatalog.model_provider,
            AgentCatalog.model_name,
        ).where(AgentCatalog.organization_id.is_(None))

        if current_keys:
            stale_rows_stmt = stale_rows_stmt.where(
                ~tuple_(AgentCatalog.model_provider, AgentCatalog.model_name).in_(
                    current_keys
                )
            )
        stale_keys = set((await self.session.execute(stale_rows_stmt)).tuples().all())
        await self._prune_stale_platform_model_selections(stale_keys)

        if current_keys:
            stale_stmt = delete(AgentCatalog).where(
                AgentCatalog.organization_id.is_(None),
                ~tuple_(AgentCatalog.model_provider, AgentCatalog.model_name).in_(
                    current_keys
                ),
            )
        else:
            stale_stmt = delete(AgentCatalog).where(
                AgentCatalog.organization_id.is_(None)
            )
        await self.session.execute(stale_stmt)

        if not builtin_rows:
            return

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
                "model_name": stmt.excluded.model_name,
                "model_metadata": stmt.excluded.model_metadata,
                "last_refreshed_at": stmt.excluded.last_refreshed_at,
                "updated_at": func.now(),
            },
        )
        await self.session.execute(stmt)

    async def _prune_stale_platform_model_selections(
        self,
        stale_keys: set[tuple[str, str]],
    ) -> None:
        if not stale_keys:
            return

        conditions = [
            (AgentEnabledModel.source_id.is_(None))
            & (AgentEnabledModel.model_provider == model_provider)
            & (AgentEnabledModel.model_name == model_name)
            for model_provider, model_name in stale_keys
        ]
        await self.session.execute(delete(AgentEnabledModel).where(or_(*conditions)))

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
        affected_org_ids: set[uuid.UUID] = set()
        for setting in default_settings:
            try:
                value = orjson.loads(setting.value)
            except orjson.JSONDecodeError:
                continue
            match value:
                case {
                    "source_id": None,
                    "model_provider": str(model_provider),
                    "model_name": str(model_name),
                } if (model_provider, model_name) in stale_keys:
                    affected_org_ids.add(setting.organization_id)
                case _:
                    continue

        if not affected_org_ids:
            return

        cleared_value = orjson.dumps(None)
        affected_settings = (
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
        for setting in affected_settings:
            setting.value = cleared_value
            setting.is_encrypted = False

    async def list_platform_catalog(
        self,
        *,
        query: str | None = None,
        provider: str | None = None,
        cursor: str | None = None,
        limit: int = 100,
    ) -> PlatformCatalogRead:
        status, refreshed_at, last_error = await self._get_catalog_state()
        bounded_limit = max(1, min(limit, 200))
        offset = int(cursor) if cursor else 0

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
            await self._upsert_platform_catalog_rows()
            now = datetime.now(UTC).isoformat()
            await self._set_platform_setting_value(
                key=PLATFORM_CATALOG_STATE_SETTINGS["discovery_status"],
                value=ModelDiscoveryStatus.READY.value,
            )
            await self._set_platform_setting_value(
                key=PLATFORM_CATALOG_STATE_SETTINGS["last_refreshed_at"],
                value=now,
            )
            await self._set_platform_setting_value(
                key=PLATFORM_CATALOG_STATE_SETTINGS["last_error"],
                value=None,
            )
            await self.session.commit()
        except Exception as exc:
            await self.session.rollback()
            now = datetime.now(UTC).isoformat()
            await self._set_platform_setting_value(
                key=PLATFORM_CATALOG_STATE_SETTINGS["discovery_status"],
                value=ModelDiscoveryStatus.FAILED.value,
            )
            await self._set_platform_setting_value(
                key=PLATFORM_CATALOG_STATE_SETTINGS["last_refreshed_at"],
                value=now,
            )
            await self._set_platform_setting_value(
                key=PLATFORM_CATALOG_STATE_SETTINGS["last_error"],
                value=str(exc),
            )
            await self.session.commit()
            raise
        return await self.list_platform_catalog()
