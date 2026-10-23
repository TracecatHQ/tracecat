"""Service for managing agent model catalog."""

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal, TypedDict
from urllib.parse import urlsplit
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy import and_, exists, select, tuple_
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError

from tracecat.agent.access.effective import effective_catalog_ids
from tracecat.agent.bedrock import (
    BEDROCK_DISCOVERY_SOURCE,
    BEDROCK_FOUNDATION_MODEL_METADATA_KEY,
    BEDROCK_SUBSCRIPTION_METADATA_KEY,
    BedrockDiscoveredModel,
    BedrockSubscription,
    resolve_foundation_model_id,
)
from tracecat.agent.catalog.bundled import get_retired_platform_models
from tracecat.agent.catalog.lifecycle import ModelLifecycle, read_model_lifecycle
from tracecat.agent.catalog.schemas import AgentCatalogRead
from tracecat.agent.catalog.types import ModelKey, ModelReplacement
from tracecat.agent.default_model import list_saved_default_models
from tracecat.audit.logger import audit_log
from tracecat.auth.types import Role
from tracecat.authz.controls import require_scope
from tracecat.db.models import AgentCatalog, AgentCustomProvider, AgentModelAccess
from tracecat.exceptions import (
    AgentModelRetiredError,
    TracecatAuthorizationError,
    TracecatNotFoundError,
    TracecatValidationError,
)
from tracecat.pagination import BaseCursorPaginator, CursorPaginationParams
from tracecat.service import BaseService
from tracecat.sync import CatalogMappingCandidate


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


def _safe_hostname(base_url: str | None) -> str | None:
    """Return a display-safe hostname without URL userinfo or path data."""
    if not base_url:
        return None
    try:
        return urlsplit(base_url).hostname
    except ValueError:
        return None


class AgentCatalogService(BaseService):
    """Manage model catalog entries."""

    service_name = "agent_catalog"

    async def prune_retired_platform_models(
        self, retired: Collection[ModelKey]
    ) -> None:
        """Delete platform rows named in ``retired`` that nothing references.

        Only listed rows are deleted, never rows merely missing from the bundle,
        so an unreadable bundle cannot empty the catalog. A row stays while a
        workspace subset or a saved org default references it. Requires a
        bypass-RLS session so those checks see every organization.
        """
        if not retired:
            return
        defaults = await list_saved_default_models(self.session)
        # Deleting the last row of a workspace subset would widen it to the org
        # set, since an empty subset means "inherit".
        in_workspace_subset = (
            select(AgentModelAccess.id)
            .where(
                AgentModelAccess.catalog_id == AgentCatalog.id,
                AgentModelAccess.workspace_id.is_not(None),
            )
            .exists()
        )
        # Org access rows cascade; preset catalog_id is set null and swaps by name.
        await self.session.execute(
            sa.delete(AgentCatalog).where(
                AgentCatalog.organization_id.is_(None),
                AgentCatalog.custom_provider_id.is_(None),
                tuple_(AgentCatalog.model_provider, AgentCatalog.model_name).in_(
                    [tuple(key) for key in retired]
                ),
                ~in_workspace_subset,
                AgentCatalog.id.not_in(defaults.catalog_ids),
                AgentCatalog.model_name.not_in(defaults.model_names),
            )
        )
        await self.session.commit()

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
        enabled_catalog_ids = effective_catalog_ids(org_id, workspace_id)
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

    async def enabled_catalog_models(
        self,
        *,
        org_id: UUID,
        catalog_ids: Collection[UUID],
        workspace_id: UUID | None = None,
    ) -> dict[UUID, ModelKey]:
        """Return visible, enabled input ids and their local model identities.

        Workspace-sync correlation needs the tuple as well as the enabled state
        so an incoming deployment-local UUID can only be preserved when its
        manifest model identity matches the local catalog row.
        """
        if not catalog_ids:
            return {}

        enabled_catalog_ids = effective_catalog_ids(org_id, workspace_id)
        stmt = (
            select(
                AgentCatalog.id,
                AgentCatalog.model_provider,
                AgentCatalog.model_name,
            )
            .where(
                AgentCatalog.id.in_(catalog_ids),
                AgentCatalog.id.in_(enabled_catalog_ids),
                sa.or_(
                    AgentCatalog.organization_id == org_id,
                    AgentCatalog.organization_id.is_(None),
                ),
            )
            .order_by(AgentCatalog.id.asc())
        )
        return {
            catalog_id: ModelKey(model_provider, model_name)
            for catalog_id, model_provider, model_name in (
                await self.session.execute(stmt)
            ).tuples()
        }

    async def catalog_candidates_by_models(
        self,
        *,
        org_id: UUID,
        models: Collection[ModelKey],
        workspace_id: UUID | None = None,
    ) -> dict[ModelKey, list[CatalogMappingCandidate]]:
        """Return every visible, enabled local candidate for each model tuple.

        Unlike the best-effort tuple resolvers, this method preserves duplicate
        candidates so callers at an interactive trust boundary can require an
        explicit choice instead of silently selecting an arbitrary provider.
        """
        if not models:
            return {}

        enabled_catalog_ids = effective_catalog_ids(org_id, workspace_id)
        stmt = (
            select(
                AgentCatalog.model_provider,
                AgentCatalog.model_name,
                AgentCatalog.id,
                AgentCatalog.organization_id,
                AgentCatalog.custom_provider_id,
                AgentCatalog.model_metadata,
                AgentCustomProvider.display_name,
                AgentCustomProvider.base_url,
            )
            .outerjoin(
                AgentCustomProvider,
                and_(
                    AgentCustomProvider.organization_id == AgentCatalog.organization_id,
                    AgentCustomProvider.id == AgentCatalog.custom_provider_id,
                ),
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

        candidates: dict[ModelKey, list[CatalogMappingCandidate]] = {}
        for (
            model_provider,
            model_name,
            catalog_id,
            organization_id,
            custom_provider_id,
            model_metadata,
            custom_provider_name,
            custom_provider_base_url,
        ) in (await self.session.execute(stmt)).tuples():
            model_display_name = None
            if model_metadata and isinstance(
                raw_display_name := model_metadata.get("display_name"), str
            ):
                model_display_name = raw_display_name

            if custom_provider_id is not None:
                origin: Literal["platform", "organization", "custom_provider"] = (
                    "custom_provider"
                )
                provider_name = custom_provider_name or "Custom provider"
            elif organization_id is not None:
                origin = "organization"
                provider_name = "Organization catalog"
            else:
                origin = "platform"
                provider_name = "Tracecat catalog"

            candidates.setdefault(ModelKey(model_provider, model_name), []).append(
                CatalogMappingCandidate(
                    catalog_id=catalog_id,
                    model_provider=model_provider,
                    model_name=model_name,
                    provider_name=provider_name,
                    model_display_name=model_display_name,
                    endpoint_hostname=_safe_hostname(custom_provider_base_url),
                    origin=origin,
                )
            )
        return candidates

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
        ``is_catalog_enabled``, both via ``effective_catalog_ids``): a
        workspace's subset narrows the org-enabled set, otherwise the org-level
        set applies. This
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
        enabled_catalog_ids = effective_catalog_ids(org_id, workspace_id)

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

    async def resolve_model_replacement(
        self,
        *,
        role: Role,
        model_provider: str,
        model_name: str,
        catalog_id: UUID | None,
    ) -> ModelReplacement | None:
        """Return where a retired platform model's runs move, or ``None``.

        Reads the lifecycle from the platform row selected by ``catalog_id``,
        or by provider and name for legacy configs. Once a retired row is
        deleted, the bundled ``retired`` list keeps the swap working. The
        replacement must be enabled for the workspace, so a swap never widens
        model access.

        Raises:
            AgentModelRetiredError: The model is retired and has no replacement
                enabled for the workspace.
        """
        if role.organization_id is None:
            raise TracecatAuthorizationError("Role has no organization_id")
        found = await self._platform_lifecycle(
            model_provider=model_provider,
            model_name=model_name,
            catalog_id=catalog_id,
        )
        if found is None:
            return None
        (model_provider, model_name), lifecycle = found
        if lifecycle.status_on() != "retired":
            return None
        target = lifecycle.replacement_key(model_provider)
        if target is None:
            raise AgentModelRetiredError(model_name, replacement=None)
        replacement_id = await self.resolve_catalog_id_by_model(
            org_id=role.organization_id,
            model_provider=target.model_provider,
            model_name=target.model_name,
            workspace_id=role.workspace_id,
        )
        if replacement_id is None:
            raise AgentModelRetiredError(model_name, replacement=target.model_name)
        self.logger.warning(
            "Running retired model on its replacement",
            retired_model_provider=model_provider,
            retired_model_name=model_name,
            model_provider=target.model_provider,
            model_name=target.model_name,
        )
        return ModelReplacement(
            model_provider=target.model_provider,
            model_name=target.model_name,
            catalog_id=replacement_id if catalog_id is not None else None,
            reason="retired",
        )

    async def _platform_lifecycle(
        self,
        *,
        model_provider: str,
        model_name: str,
        catalog_id: UUID | None,
    ) -> tuple[ModelKey, ModelLifecycle] | None:
        """Return the platform model's identity and lifecycle, if it has one."""
        row_filter = (
            AgentCatalog.id == catalog_id
            if catalog_id is not None
            else and_(
                AgentCatalog.organization_id.is_(None),
                AgentCatalog.model_provider == model_provider,
                AgentCatalog.model_name == model_name,
            )
        )
        row = (
            await self.session.execute(
                select(
                    AgentCatalog.organization_id,
                    AgentCatalog.model_provider,
                    AgentCatalog.model_name,
                    AgentCatalog.model_metadata,
                ).where(row_filter)
            )
        ).one_or_none()
        if row is None:
            key = ModelKey(model_provider, model_name)
            lifecycle = get_retired_platform_models().get(key)
            return (key, lifecycle) if lifecycle is not None else None
        if row.organization_id is not None:
            # Org-owned rows have no provider lifecycle.
            return None
        # The row, not the caller's fields, names the model.
        key = ModelKey(row.model_provider, row.model_name)
        # The bundle wins: a kept retired row is no longer refreshed, so its
        # stored lifecycle can name a replacement that has since retired.
        lifecycle = get_retired_platform_models().get(key) or read_model_lifecycle(
            row.model_metadata
        )
        return (key, lifecycle) if lifecycle is not None else None

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
        current_metadata = dict(row.model_metadata or {})
        if (
            row.model_provider == "bedrock"
            and metadata
            and ("inference_profile_id" in metadata or "model_id" in metadata)
        ):
            # A retargeted row must be re-resolved on the next Bedrock refresh.
            current_metadata.pop(BEDROCK_FOUNDATION_MODEL_METADATA_KEY, None)
            current_metadata.pop(BEDROCK_SUBSCRIPTION_METADATA_KEY, None)
        row.model_metadata = {**current_metadata, **(metadata or {})}
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
        custom_provider_id: UUID | None,
        models: Sequence[Mapping[str, Any]],
        model_provider: str,
    ) -> int:
        """Bulk upsert discovered models for a provider.

        Pass ``custom_provider_id`` for rows linked to an ``AgentCustomProvider``.
        Built-in gateway providers (Ollama, vLLM, LiteLLM, OpenRouter) pass
        ``None`` and are scoped by ``model_provider`` instead.
        """
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
        provider_scope = (
            AgentCatalog.custom_provider_id == custom_provider_id
            if custom_provider_id is not None
            else and_(
                AgentCatalog.custom_provider_id.is_(None),
                AgentCatalog.model_provider == model_provider,
            )
        )
        delete_stmt = sa.delete(AgentCatalog).where(
            and_(
                AgentCatalog.organization_id == org_id,
                provider_scope,
                AgentCatalog.model_name.not_in(current_model_names),
            )
        )
        await self.session.execute(delete_stmt)
        await self.session.commit()
        return len(values)

    async def list_bedrock_rows(self, *, org_id: UUID) -> Sequence[AgentCatalog]:
        """List the org's Bedrock catalog rows."""
        result = await self.session.execute(
            select(AgentCatalog).where(
                AgentCatalog.organization_id == org_id,
                AgentCatalog.custom_provider_id.is_(None),
                AgentCatalog.model_provider == "bedrock",
            )
        )
        return result.scalars().all()

    async def sync_bedrock_models(
        self,
        *,
        org_id: UUID,
        models: Sequence[BedrockDiscoveredModel],
        subscriptions: Mapping[str, BedrockSubscription],
    ) -> int:
        """Merge discovered Bedrock models into the org's Bedrock catalog.

        Manually authored rows are never removed; they only receive the
        subscription status of the foundation model they reference.
        Previously discovered rows that AWS no longer returns are removed.
        """
        rows = await self.list_bedrock_rows(org_id=org_id)
        now = datetime.now(UTC)
        discovered_by_name = {model.model_name: model for model in models}
        foundation_by_ref = {
            ref: model.foundation_model_id
            for model in models
            for ref in (model.inference_profile_id, model.model_id)
            if ref is not None
        }
        existing_names: set[str] = set()
        for row in rows:
            metadata = dict(row.model_metadata or {})
            if (
                metadata.get("discovery_source") == BEDROCK_DISCOVERY_SOURCE
                and row.model_name not in discovered_by_name
            ):
                await self.session.delete(row)
                continue
            existing_names.add(row.model_name)
            ref = metadata.get("inference_profile_id") or metadata.get("model_id")
            foundation_id = resolve_foundation_model_id(metadata)
            if foundation_id is None and isinstance(ref, str):
                foundation_id = foundation_by_ref.get(ref)
            if foundation_id is None:
                continue
            metadata[BEDROCK_FOUNDATION_MODEL_METADATA_KEY] = foundation_id
            if subscription := subscriptions.get(foundation_id):
                metadata[BEDROCK_SUBSCRIPTION_METADATA_KEY] = subscription
            row.model_metadata = metadata
            row.last_refreshed_at = now

        for model in models:
            if model.model_name in existing_names:
                continue
            subscription = subscriptions.get(model.foundation_model_id)
            self.session.add(
                AgentCatalog(
                    organization_id=org_id,
                    custom_provider_id=None,
                    model_provider="bedrock",
                    model_name=model.model_name,
                    model_metadata=model.to_metadata(
                        subscription
                        or {"status": "unknown", "checked_at": now.isoformat()}
                    ),
                    last_refreshed_at=now,
                )
            )
        await self.session.commit()
        return len(models)

    async def set_bedrock_subscription(
        self,
        *,
        org_id: UUID,
        catalog_id: UUID,
        foundation_model_id: str,
        subscription: BedrockSubscription,
    ) -> None:
        """Record a subscription status on every org row for the foundation model."""
        rows = await self.list_bedrock_rows(org_id=org_id)
        for row in rows:
            metadata = dict(row.model_metadata or {})
            if row.id == catalog_id:
                metadata[BEDROCK_FOUNDATION_MODEL_METADATA_KEY] = foundation_model_id
            elif resolve_foundation_model_id(metadata) != foundation_model_id:
                continue
            metadata[BEDROCK_SUBSCRIPTION_METADATA_KEY] = subscription
            row.model_metadata = metadata
        await self.session.commit()
