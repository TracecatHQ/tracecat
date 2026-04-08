"""Service for managing agent model catalog."""

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy import and_, select
from sqlalchemy.dialects.postgresql import insert

from tracecat.agent.catalog.schemas import AgentCatalogRead
from tracecat.db.models import AgentCatalog
from tracecat.exceptions import TracecatNotFoundError
from tracecat.pagination import BaseCursorPaginator, CursorPaginationParams
from tracecat.service import BaseService


class AgentCatalogService(BaseService):
    """Manage model catalog entries."""

    service_name = "agent_catalog"

    async def upsert_platform_catalog(
        self,
        entries: Sequence[Mapping[str, Any]],
    ) -> int:
        """Bulk upsert platform catalog rows."""
        if not entries:
            return 0

        now = datetime.now(UTC)
        values: list[dict[str, Any]] = []
        for entry in entries:
            model_provider = entry.get("model_provider")
            model_name = entry.get("model_name")
            if not isinstance(model_provider, str) or not isinstance(model_name, str):
                continue
            values.append(
                {
                    "organization_id": None,
                    "custom_provider_id": None,
                    "model_provider": model_provider,
                    "model_name": model_name,
                    "model_metadata": entry.get("metadata") or {},
                    "last_refreshed_at": now,
                }
            )

        if not values:
            return 0

        stmt = insert(AgentCatalog).values(values)
        stmt = stmt.on_conflict_do_update(
            index_elements=[
                "organization_id",
                "custom_provider_id",
                "model_provider",
                "model_name",
            ],
            set_={
                "model_metadata": stmt.excluded.model_metadata,
                "last_refreshed_at": stmt.excluded.last_refreshed_at,
            },
        )
        await self.session.execute(stmt)
        await self.session.commit()
        return len(values)

    async def get_catalog_entry(
        self,
        org_id: UUID | None,
        catalog_id: UUID,
    ) -> AgentCatalogRead:
        """Get a specific catalog entry."""
        del org_id
        stmt = select(AgentCatalog).where(AgentCatalog.id == catalog_id)
        row = (await self.session.execute(stmt)).scalar_one_or_none()
        if row is None:
            raise TracecatNotFoundError(f"Catalog entry {catalog_id} not found")
        return AgentCatalogRead.model_validate(row)

    async def list_catalog(
        self,
        org_id: UUID | None = None,
        provider_filter: str | None = None,
        model_name_filter: str | None = None,
        cursor_params: CursorPaginationParams | None = None,
    ) -> tuple[list[AgentCatalogRead], str | None]:
        """List catalog entries with filtering and cursor pagination."""
        params = cursor_params or CursorPaginationParams(limit=50)
        conditions: list[Any] = []
        if org_id is not None:
            conditions.append(
                sa.or_(
                    AgentCatalog.organization_id == org_id,
                    AgentCatalog.organization_id.is_(None),
                )
            )
        if provider_filter:
            conditions.append(AgentCatalog.model_provider == provider_filter)
        if model_name_filter:
            conditions.append(AgentCatalog.model_name.ilike(f"%{model_name_filter}%"))

        stmt = select(AgentCatalog)
        if conditions:
            stmt = stmt.where(and_(*conditions))
        stmt = stmt.order_by(
            AgentCatalog.created_at.desc(),
            AgentCatalog.id.desc(),
        )

        paginator = BaseCursorPaginator(self.session)
        if params.cursor:
            try:
                cursor_data = paginator.decode_cursor(params.cursor)
            except (ValueError, AttributeError) as err:
                raise ValueError("Invalid cursor") from err
            stmt = stmt.where(AgentCatalog.created_at < cursor_data.sort_value)

        rows = (
            (await self.session.execute(stmt.limit(params.limit + 1))).scalars().all()
        )
        has_more = len(rows) > params.limit
        if has_more:
            rows = rows[: params.limit]

        next_cursor = None
        if has_more and rows:
            last_row = rows[-1]
            next_cursor = paginator.encode_cursor(
                last_row.id,
                sort_column="created_at",
                sort_value=last_row.created_at,
            )

        return [AgentCatalogRead.model_validate(row) for row in rows], next_cursor

    async def upsert_catalog_entry(
        self,
        *,
        org_id: UUID | None,
        custom_provider_id: UUID | None,
        model_provider: str,
        model_name: str,
        metadata: dict[str, Any] | None = None,
    ) -> AgentCatalogRead:
        """Insert or update one catalog row."""
        now = datetime.now(UTC)
        stmt = insert(AgentCatalog).values(
            organization_id=org_id,
            custom_provider_id=custom_provider_id,
            model_provider=model_provider,
            model_name=model_name,
            model_metadata=metadata or {},
            last_refreshed_at=now,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[
                "organization_id",
                "custom_provider_id",
                "model_provider",
                "model_name",
            ],
            set_={
                "model_metadata": stmt.excluded.model_metadata,
                "last_refreshed_at": stmt.excluded.last_refreshed_at,
            },
        ).returning(AgentCatalog)
        row = (await self.session.execute(stmt)).scalar_one()
        await self.session.commit()
        return AgentCatalogRead.model_validate(row)

    async def upsert_discovered_models(
        self,
        *,
        org_id: UUID,
        custom_provider_id: UUID,
        models: Sequence[Mapping[str, Any]],
        model_provider: str,
    ) -> int:
        """Bulk upsert discovered models for a custom provider."""
        values: list[dict[str, Any]] = []
        now = datetime.now(UTC)
        for model in models:
            model_name = model.get("id") or model.get("model_name")
            if not isinstance(model_name, str):
                continue
            values.append(
                {
                    "organization_id": org_id,
                    "custom_provider_id": custom_provider_id,
                    "model_provider": model_provider,
                    "model_name": model_name,
                    "model_metadata": dict(model),
                    "last_refreshed_at": now,
                }
            )
        if not values:
            return 0

        stmt = insert(AgentCatalog).values(values)
        stmt = stmt.on_conflict_do_update(
            index_elements=[
                "organization_id",
                "custom_provider_id",
                "model_provider",
                "model_name",
            ],
            set_={
                "model_metadata": stmt.excluded.model_metadata,
                "last_refreshed_at": stmt.excluded.last_refreshed_at,
            },
        )
        await self.session.execute(stmt)
        await self.session.commit()
        return len(values)
