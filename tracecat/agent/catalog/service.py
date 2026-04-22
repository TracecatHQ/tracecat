"""Service for managing agent model catalog."""

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any, NotRequired, TypedDict
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy import and_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError

from tracecat.agent.catalog.schemas import AgentCatalogRead
from tracecat.audit.logger import audit_log
from tracecat.authz.controls import require_scope
from tracecat.db.models import AgentCatalog
from tracecat.exceptions import TracecatNotFoundError, TracecatValidationError
from tracecat.pagination import BaseCursorPaginator, CursorPaginationParams
from tracecat.service import BaseService


class _CatalogRowValues(TypedDict):
    """Row shape passed to ``insert(AgentCatalog).values(...)``."""

    organization_id: UUID | None
    custom_provider_id: UUID | None
    model_provider: str
    model_name: str
    model_metadata: dict[str, Any]
    last_refreshed_at: datetime


class PlatformCatalogEntry(TypedDict):
    """Input row for bulk platform-catalog seeding."""

    model_provider: str
    model_name: str
    metadata: NotRequired[dict[str, Any]]


class AgentCatalogService(BaseService):
    """Manage model catalog entries."""

    service_name = "agent_catalog"

    async def upsert_platform_catalog(
        self,
        entries: Sequence[PlatformCatalogEntry],
    ) -> int:
        """Bulk upsert platform catalog rows."""
        if not entries:
            return 0

        now = datetime.now(UTC)
        values: list[_CatalogRowValues] = []
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
        *,
        org_id: UUID,
        catalog_id: UUID,
    ) -> AgentCatalog:
        """Get a catalog entry visible to the caller's org.

        Matches platform rows (``organization_id IS NULL``) and rows owned by
        ``org_id``; all other rows surface as not found.

        Raises:
            TracecatNotFoundError: No matching row.
        """
        stmt = select(AgentCatalog).where(
            and_(
                AgentCatalog.id == catalog_id,
                sa.or_(
                    AgentCatalog.organization_id == org_id,
                    AgentCatalog.organization_id.is_(None),
                ),
            )
        )
        row = (await self.session.execute(stmt)).scalar_one_or_none()
        if row is None:
            raise TracecatNotFoundError(f"Catalog entry {catalog_id} not found")
        return row

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
            if not isinstance(cursor_data.sort_value, datetime):
                raise ValueError("Invalid cursor")
            stmt = stmt.where(
                sa.or_(
                    AgentCatalog.created_at < cursor_data.sort_value,
                    sa.and_(
                        AgentCatalog.created_at == cursor_data.sort_value,
                        AgentCatalog.id < UUID(cursor_data.id),
                    ),
                )
            )

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

    @require_scope("agent:create")
    @audit_log(resource_type="agent_catalog", action="create")
    async def create_catalog_entry(
        self,
        *,
        org_id: UUID,
        model_provider: str,
        model_name: str,
        metadata: dict[str, Any] | None = None,
    ) -> AgentCatalog:
        """Create a new org-scoped catalog entry.

        Raises:
            TracecatValidationError: A row already exists for
                ``(org_id, model_provider, model_name)``.
        """
        now = datetime.now(UTC)
        row = AgentCatalog(
            organization_id=org_id,
            custom_provider_id=None,
            model_provider=model_provider,
            model_name=model_name,
            model_metadata=metadata or {},
            last_refreshed_at=now,
        )
        self.session.add(row)
        try:
            await self.session.commit()
        except IntegrityError as err:
            await self.session.rollback()
            raise TracecatValidationError(
                f"Catalog entry for provider {model_provider!r} and model "
                f"{model_name!r} already exists"
            ) from err
        await self.session.refresh(row)
        return row

    @require_scope("agent:update")
    @audit_log(resource_type="agent_catalog", action="update")
    async def update_catalog_entry(
        self,
        row: AgentCatalog,
        *,
        org_id: UUID,
        expected_provider: str,
        metadata: dict[str, Any] | None,
    ) -> AgentCatalog:
        """Update metadata on a pre-fetched org-scoped catalog row.

        The ``expected_provider`` must match the stored row; model_provider is
        immutable and the request body's discriminator is used purely to pick
        the correct metadata schema. Platform rows (``organization_id IS NULL``)
        and rows from other orgs are rejected.
        """
        if row.organization_id is None or row.organization_id != org_id:
            raise TracecatNotFoundError(f"Catalog entry {row.id} not found")
        if row.model_provider != expected_provider:
            raise TracecatValidationError(
                f"model_provider mismatch: expected {row.model_provider!r}, "
                f"got {expected_provider!r}"
            )
        row.model_metadata = {
            **(row.model_metadata or {}),
            **(metadata or {}),
        }
        row.last_refreshed_at = datetime.now(UTC)
        await self.session.commit()
        await self.session.refresh(row)
        return row

    @require_scope("agent:delete")
    @audit_log(resource_type="agent_catalog", action="delete")
    async def delete_catalog_entry(
        self,
        row: AgentCatalog,
        *,
        org_id: UUID,
    ) -> None:
        """Delete a pre-fetched org-scoped catalog row. Access rows cascade.

        Platform rows (``organization_id IS NULL``) and rows from other orgs
        are rejected.
        """
        if row.organization_id is None or row.organization_id != org_id:
            raise TracecatNotFoundError(f"Catalog entry {row.id} not found")
        await self.session.delete(row)
        await self.session.commit()

    async def upsert_discovered_models(
        self,
        *,
        org_id: UUID,
        custom_provider_id: UUID,
        models: Sequence[Mapping[str, Any]],
        model_provider: str,
    ) -> int:
        """Bulk upsert discovered models for a custom provider."""
        values: list[_CatalogRowValues] = []
        now = datetime.now(UTC)
        for raw in models:
            model_name = raw.get("id") or raw.get("model_name")
            if not isinstance(model_name, str):
                continue
            values.append(
                {
                    "organization_id": org_id,
                    "custom_provider_id": custom_provider_id,
                    "model_provider": model_provider,
                    "model_name": model_name,
                    "model_metadata": dict(raw),
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
