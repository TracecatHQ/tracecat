from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Literal

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from tracecat.db.models import AgentCatalog, AgentEnabledModel

LegacyCatalogMatchStatus = Literal["matched", "missing", "ambiguous"]


@dataclass(frozen=True, slots=True)
class LegacyCatalogMatch:
    status: LegacyCatalogMatchStatus
    source_id: uuid.UUID | None = None
    model_provider: str | None = None
    model_name: str | None = None


async def resolve_catalog_match_for_provider_model(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    workspace_id: uuid.UUID | None,
    model_provider: str,
    model_name: str,
) -> LegacyCatalogMatch:
    return await resolve_enabled_catalog_match_for_provider_model(
        session,
        organization_id=organization_id,
        workspace_id=workspace_id,
        model_provider=model_provider,
        model_name=model_name,
    )


async def resolve_accessible_catalog_match_for_provider_model(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    model_provider: str,
    model_name: str,
) -> LegacyCatalogMatch:
    provider = model_provider.strip()
    name = model_name.strip()
    if not provider or not name:
        return LegacyCatalogMatch(status="missing")

    return await _resolve_unique_catalog_match(
        session,
        stmt=_accessible_catalog_match_stmt(
            organization_id=organization_id,
            model_provider=provider,
            model_name=name,
        ),
    )


async def resolve_enabled_catalog_match_for_provider_model(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    workspace_id: uuid.UUID | None,
    model_provider: str,
    model_name: str,
) -> LegacyCatalogMatch:
    provider = model_provider.strip()
    name = model_name.strip()
    if not provider or not name:
        return LegacyCatalogMatch(status="missing")

    workspace_subset_enabled = await _workspace_subset_enabled(
        session,
        workspace_id=workspace_id,
    )
    return await _resolve_unique_catalog_match(
        session,
        stmt=_enabled_catalog_match_stmt(
            organization_id=organization_id,
            workspace_id=workspace_id,
            workspace_subset_enabled=workspace_subset_enabled,
            model_provider=provider,
            model_name=name,
        ),
    )


async def resolve_catalog_match_for_model_name(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    model_name: str,
) -> LegacyCatalogMatch:
    return await resolve_enabled_catalog_match_for_model_name(
        session,
        organization_id=organization_id,
        model_name=model_name,
    )


async def resolve_accessible_catalog_match_for_model_name(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    model_name: str,
) -> LegacyCatalogMatch:
    name = model_name.strip()
    if not name:
        return LegacyCatalogMatch(status="missing")

    return await _resolve_unique_catalog_match(
        session,
        stmt=_accessible_catalog_match_stmt(
            organization_id=organization_id,
            model_name=name,
        ),
    )


async def resolve_enabled_catalog_match_for_model_name(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    model_name: str,
) -> LegacyCatalogMatch:
    name = model_name.strip()
    if not name:
        return LegacyCatalogMatch(status="missing")

    return await _resolve_unique_catalog_match(
        session,
        stmt=_enabled_catalog_match_stmt(
            organization_id=organization_id,
            workspace_id=None,
            model_name=name,
        ),
    )


def _enabled_catalog_match_stmt(
    *,
    organization_id: uuid.UUID,
    workspace_id: uuid.UUID | None,
    workspace_subset_enabled: bool = False,
    model_name: str,
    model_provider: str | None = None,
):
    stmt = select(
        AgentEnabledModel.source_id,
        AgentEnabledModel.model_provider,
        AgentEnabledModel.model_name,
    ).where(
        AgentEnabledModel.organization_id == organization_id,
        AgentEnabledModel.workspace_id.is_(None),
        AgentEnabledModel.model_name == model_name,
    )
    if model_provider is not None:
        stmt = stmt.where(AgentEnabledModel.model_provider == model_provider)
    if workspace_id is not None and workspace_subset_enabled:
        workspace_enabled = aliased(AgentEnabledModel)
        stmt = stmt.join(
            workspace_enabled,
            and_(
                workspace_enabled.organization_id == organization_id,
                workspace_enabled.workspace_id == workspace_id,
                workspace_enabled.source_id == AgentEnabledModel.source_id,
                workspace_enabled.model_provider == AgentEnabledModel.model_provider,
                workspace_enabled.model_name == AgentEnabledModel.model_name,
            ),
        )
    return stmt.order_by(
        AgentEnabledModel.source_id.asc().nulls_first(),
        AgentEnabledModel.model_provider.asc(),
        AgentEnabledModel.model_name.asc(),
    ).limit(2)


def _accessible_catalog_match_stmt(
    *,
    organization_id: uuid.UUID,
    model_name: str,
    model_provider: str | None = None,
):
    stmt = select(
        AgentCatalog.source_id,
        AgentCatalog.model_provider,
        AgentCatalog.model_name,
    ).where(
        AgentCatalog.model_name == model_name,
        or_(
            AgentCatalog.organization_id == organization_id,
            AgentCatalog.organization_id.is_(None),
        ),
    )
    if model_provider is not None:
        stmt = stmt.where(AgentCatalog.model_provider == model_provider)
    return stmt.order_by(
        AgentCatalog.source_id.asc().nulls_first(),
        AgentCatalog.model_provider.asc(),
        AgentCatalog.model_name.asc(),
    ).limit(2)


async def _resolve_unique_catalog_match(
    session: AsyncSession,
    *,
    stmt,
) -> LegacyCatalogMatch:
    matches = list((await session.execute(stmt)).tuples().all())
    if not matches:
        return LegacyCatalogMatch(status="missing")
    unique_matches = list(dict.fromkeys(matches))
    if len(unique_matches) > 1:
        return LegacyCatalogMatch(status="ambiguous")
    source_id, model_provider, model_name = unique_matches[0]
    return LegacyCatalogMatch(
        status="matched",
        source_id=source_id,
        model_provider=model_provider,
        model_name=model_name,
    )


async def _workspace_subset_enabled(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID | None,
) -> bool:
    if workspace_id is None:
        return False
    stmt = select(AgentEnabledModel.id).where(
        AgentEnabledModel.workspace_id == workspace_id
    )
    return (await session.execute(stmt.limit(1))).scalar_one_or_none() is not None
