"""Service for managing agent model catalog."""

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, TypedDict
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy import and_, exists, select, tuple_
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.agent.catalog.schemas import AgentCatalogRead, ModelKey
from tracecat.audit.logger import audit_log
from tracecat.authz.controls import require_scope
from tracecat.db.models import AgentCatalog, AgentModelAccess
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


@dataclass(frozen=True, slots=True)
class PlatformCatalogEntry:
    """Input row for bulk platform-catalog seeding.

    Already validated at the trust boundary (see
    ``tracecat.agent.catalog.loader``), so fields are trusted here.
    """

    model_provider: str
    model_name: str
    metadata: dict[str, Any] = field(default_factory=dict)


class AgentCatalogService(BaseService):
    """Manage model catalog entries."""

    service_name = "agent_catalog"

    def __init__(self, session: AsyncSession):
        super().__init__(session)
        self._effective_access_workspace_ids: dict[
            tuple[UUID, UUID | None], UUID | None
        ] = {}

    async def upsert_platform_catalog(
        self,
        entries: Sequence[PlatformCatalogEntry],
    ) -> int:
        """Bulk upsert platform catalog rows."""
        if not entries:
            return 0

        now = datetime.now(UTC)
        values: list[_CatalogRowValues] = [
            {
                "organization_id": None,
                "custom_provider_id": None,
                "model_provider": entry.model_provider,
                "model_name": entry.model_name,
                "model_metadata": entry.metadata,
                "last_refreshed_at": now,
            }
            for entry in entries
        ]

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

    async def _enabled_catalog_ids_subquery(
        self,
        *,
        org_id: UUID,
        workspace_id: UUID | None,
    ) -> Any:
        """Subquery of catalog ids enabled for the importing workspace.

        Mirrors ``is_catalog_enabled``: a workspace's explicit access rows fully
        override the org-level set; otherwise the org-level (``workspace_id IS
        NULL``) set applies. The effective scope is cached for this short-lived
        service instance so adjacent sync-import batch queries perform the
        override existence check only once.
        """
        cache_key = (org_id, workspace_id)
        if cache_key in self._effective_access_workspace_ids:
            effective_workspace_id = self._effective_access_workspace_ids[cache_key]
        else:
            effective_workspace_id: UUID | None = None
            if workspace_id is not None:
                override_exists = await self.session.scalar(
                    select(
                        exists().where(
                            AgentModelAccess.organization_id == org_id,
                            AgentModelAccess.workspace_id == workspace_id,
                        )
                    )
                )
                if override_exists:
                    effective_workspace_id = workspace_id
            self._effective_access_workspace_ids[cache_key] = effective_workspace_id
        access_workspace_condition = (
            AgentModelAccess.workspace_id == effective_workspace_id
            if effective_workspace_id is not None
            else AgentModelAccess.workspace_id.is_(None)
        )
        return select(AgentModelAccess.catalog_id).where(
            AgentModelAccess.organization_id == org_id,
            access_workspace_condition,
        )

    async def is_catalog_id_enabled(
        self,
        *,
        org_id: UUID,
        catalog_id: UUID,
        workspace_id: UUID | None = None,
    ) -> bool:
        """Return whether ``catalog_id`` is visible to the org and enabled here.

        Used on import to short-circuit re-mapping when the incoming
        ``catalog_id`` already points at a row that is both visible to the org
        and enabled for the importing workspace — a same-environment pull must
        not rewrite the user's selected row to another enabled row that happens
        to win the ``(provider, name)`` tuple resolver's ordering.
        """
        enabled_catalog_ids = await self._enabled_catalog_ids_subquery(
            org_id=org_id, workspace_id=workspace_id
        )
        stmt = select(
            exists().where(
                AgentCatalog.id == catalog_id,
                AgentCatalog.id.in_(enabled_catalog_ids),
                sa.or_(
                    AgentCatalog.organization_id == org_id,
                    AgentCatalog.organization_id.is_(None),
                ),
            )
        )
        return bool(await self.session.scalar(stmt))

    async def enabled_catalog_ids(
        self,
        *,
        org_id: UUID,
        catalog_ids: Collection[UUID],
        workspace_id: UUID | None = None,
    ) -> set[UUID]:
        """Return the visible, enabled subset of ``catalog_ids``.

        This has parity with ``is_catalog_id_enabled`` for every input id. The
        batch exists so sync import correlation can resolve all incoming catalog
        ids with one query while evaluating the workspace override only once.
        """
        if not catalog_ids:
            return set()

        enabled_catalog_ids = await self._enabled_catalog_ids_subquery(
            org_id=org_id, workspace_id=workspace_id
        )
        stmt = select(AgentCatalog.id).where(
            AgentCatalog.id.in_(catalog_ids),
            AgentCatalog.id.in_(enabled_catalog_ids),
            sa.or_(
                AgentCatalog.organization_id == org_id,
                AgentCatalog.organization_id.is_(None),
            ),
        )
        return set((await self.session.execute(stmt)).scalars())

    async def resolve_catalog_id_by_model(
        self,
        *,
        org_id: UUID,
        model_provider: str,
        model_name: str,
        workspace_id: UUID | None = None,
    ) -> UUID | None:
        """Best-effort: find the local catalog row id for a (provider, name).

        The stable identifier for a model across environments is the
        ``(model_provider, model_name)`` tuple — ``catalog_id`` is a random
        per-environment UUID. This resolves that tuple to the local catalog row
        so an imported workflow can be re-pointed at the equivalent model.

        Candidates are restricted to the rows **enabled for the importing
        workspace** under the same effective-access rules the runtime enforces
        (``AgentManagementService.get_catalog_credentials`` →
        ``is_catalog_enabled``): a workspace's explicit access rows fully
        override the org-level set, otherwise the org-level set applies. This
        prevents rewriting to an org-owned row that isn't enabled here when an
        enabled platform row with the same model exists — which would otherwise
        make the agent fail immediately at execution.

        Among enabled candidates, prefers an org-owned row over a platform row.
        Returns ``None`` when no enabled row matches.

        Best-effort by design: the unique key includes ``custom_provider_id``,
        so one org can hold several enabled rows for the same
        ``(model_provider, model_name)`` backed by different custom providers.
        ``(model_provider, model_name)`` alone can't disambiguate, and the
        source ``custom_provider_id`` is itself environment-specific so it
        can't be matched either — the information needed to pick the exact row
        is genuinely unrecoverable. In that (rare) case we pick one
        deterministically rather than skip; an imported agent resolving to a
        plausible enabled model beats leaving it dangling.
        """
        enabled_catalog_ids = await self._enabled_catalog_ids_subquery(
            org_id=org_id, workspace_id=workspace_id
        )

        stmt = (
            select(AgentCatalog.id)
            .where(
                AgentCatalog.model_provider == model_provider,
                AgentCatalog.model_name == model_name,
                AgentCatalog.id.in_(enabled_catalog_ids),
                sa.or_(
                    AgentCatalog.organization_id == org_id,
                    AgentCatalog.organization_id.is_(None),
                ),
            )
            # Org-owned rows win over platform rows (NULL org). ``id`` is the
            # tiebreaker so the choice is stable across calls/replays.
            .order_by(
                AgentCatalog.organization_id.desc().nulls_last(),
                AgentCatalog.id.asc(),
            )
            .limit(1)
        )
        row_id = (await self.session.execute(stmt)).scalar_one_or_none()
        if row_id is None:
            return None
        return row_id

    async def resolve_catalog_ids_by_models(
        self,
        *,
        org_id: UUID,
        models: Collection[ModelKey],
        workspace_id: UUID | None = None,
    ) -> dict[ModelKey, UUID]:
        """Resolve enabled local catalog ids for multiple model tuples.

        This has parity with ``resolve_catalog_id_by_model`` for every input
        tuple, including org-row preference and deterministic id tie-breaking.
        The batch exists so sync import correlation can resolve all incoming
        model tuples with one query while evaluating the workspace override
        only once.
        """
        if not models:
            return {}

        enabled_catalog_ids = await self._enabled_catalog_ids_subquery(
            org_id=org_id, workspace_id=workspace_id
        )
        stmt = (
            select(
                AgentCatalog.model_provider,
                AgentCatalog.model_name,
                AgentCatalog.id,
            )
            .where(
                tuple_(
                    AgentCatalog.model_provider,
                    AgentCatalog.model_name,
                ).in_(models),
                AgentCatalog.id.in_(enabled_catalog_ids),
                sa.or_(
                    AgentCatalog.organization_id == org_id,
                    AgentCatalog.organization_id.is_(None),
                ),
            )
            .order_by(
                AgentCatalog.model_provider.asc(),
                AgentCatalog.model_name.asc(),
                AgentCatalog.organization_id.desc().nulls_last(),
                AgentCatalog.id.asc(),
            )
        )
        resolved: dict[ModelKey, UUID] = {}
        for model_provider, model_name, catalog_id in (
            await self.session.execute(stmt)
        ).tuples():
            resolved.setdefault(ModelKey(model_provider, model_name), catalog_id)
        return resolved

    async def list_catalog(
        self,
        org_id: UUID | None = None,
        provider_filter: str | None = None,
        model_name_filter: str | None = None,
        cursor_params: CursorPaginationParams | None = None,
    ) -> tuple[list[AgentCatalogRead], str | None]:
        """List catalog entries with filtering and cursor pagination."""
        conditions: list[Any] = []
        if org_id is not None:
            conditions.append(
                sa.or_(
                    AgentCatalog.organization_id == org_id,
                    AgentCatalog.organization_id.is_(None),
                )
            )
        return await self._list_catalog(
            conditions=conditions,
            provider_filter=provider_filter,
            model_name_filter=model_name_filter,
            cursor_params=cursor_params,
        )

    async def list_platform_catalog(
        self,
        provider_filter: str | None = None,
        model_name_filter: str | None = None,
        cursor_params: CursorPaginationParams | None = None,
    ) -> tuple[list[AgentCatalogRead], str | None]:
        """List platform-owned catalog entries."""
        return await self._list_catalog(
            conditions=[AgentCatalog.organization_id.is_(None)],
            provider_filter=provider_filter,
            model_name_filter=model_name_filter,
            cursor_params=cursor_params,
        )

    async def _list_catalog(
        self,
        *,
        conditions: list[Any],
        provider_filter: str | None,
        model_name_filter: str | None,
        cursor_params: CursorPaginationParams | None,
    ) -> tuple[list[AgentCatalogRead], str | None]:
        params = cursor_params or CursorPaginationParams(limit=50)
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
        if values:
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

        # Remove models that are no longer returned by the provider.
        # Runs even when values is empty so a provider returning no models
        # clears its entire catalog rather than leaving stale rows.
        current_model_names = [v["model_name"] for v in values]
        delete_stmt = sa.delete(AgentCatalog).where(
            and_(
                AgentCatalog.organization_id == org_id,
                AgentCatalog.custom_provider_id == custom_provider_id,
                AgentCatalog.model_name.not_in(current_model_names),
            )
        )
        await self.session.execute(delete_stmt)
        await self.session.commit()
        return len(values)
