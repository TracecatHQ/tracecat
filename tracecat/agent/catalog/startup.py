from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.agent.builtin_catalog import get_builtin_catalog_metadata
from tracecat.agent.catalog.service import AdminAgentCatalogService
from tracecat.agent.selections.service import AgentSelectionsService
from tracecat.agent.sources.service import AgentSourceService
from tracecat.auth.types import PlatformRole, Role
from tracecat.db.engine import get_async_session_bypass_rls_context_manager
from tracecat.db.locks import (
    derive_lock_key_from_parts,
    pg_advisory_lock,
    pg_advisory_unlock,
    try_pg_advisory_lock,
)
from tracecat.db.models import AgentSource, Organization
from tracecat.logger import logger

MODEL_CATALOG_STARTUP_SYNC_LOCK_KEY = derive_lock_key_from_parts(
    "agent_model_catalog_startup_sync"
)


def _bootstrap_org_role(organization_id: uuid.UUID) -> Role:
    return Role(
        type="service",
        service_id="tracecat-bootstrap",
        organization_id=organization_id,
        is_platform_superuser=True,
        scopes=frozenset({"*"}),
    )


def _bootstrap_platform_role() -> PlatformRole:
    return PlatformRole(
        type="service",
        user_id=uuid.UUID(int=0),
        service_id="tracecat-bootstrap",
    )


async def _list_active_organization_ids(session: AsyncSession) -> list[uuid.UUID]:
    stmt = (
        select(Organization.id)
        .where(Organization.is_active.is_(True))
        .order_by(Organization.created_at.asc())
    )
    return list((await session.execute(stmt)).scalars().all())


async def _sync_model_catalogs_as_leader(session: AsyncSession) -> None:
    org_ids = await _list_active_organization_ids(session)
    platform_service = AdminAgentCatalogService(
        session, role=_bootstrap_platform_role()
    )
    builtin_inventory = await platform_service.refresh_platform_catalog()
    catalog_metadata = get_builtin_catalog_metadata()
    logger.info(
        "Completed platform catalog startup sync",
        catalog_version=catalog_metadata["catalog_version"],
        catalog_sha256=catalog_metadata["catalog_sha256"],
        models=len(builtin_inventory.models),
    )
    if not org_ids:
        logger.info(
            "Completed platform catalog startup sync without org source refresh because no organizations exist"
        )
        return

    for org_id in org_ids:
        # Each organization gets a bootstrap role so the sync path exercises the
        # same org-scoped permissions and RLS rules that normal service calls use.
        role = _bootstrap_org_role(org_id)
        source_service = AgentSourceService(session, role=role)
        selections_service = AgentSelectionsService(session, role=role)
        source_ids = list(
            (
                await session.execute(
                    select(AgentSource.id).where(AgentSource.organization_id == org_id)
                )
            )
            .scalars()
            .all()
        )
        for source_id in source_ids:
            try:
                refreshed = await source_service.refresh_model_source(source_id)
                logger.info(
                    "Completed org model source startup sync",
                    organization_id=str(org_id),
                    source_id=str(source_id),
                    discovered_models=len(refreshed),
                )
            except Exception as exc:
                logger.warning(
                    "Org model source startup sync failed",
                    organization_id=str(org_id),
                    source_id=str(source_id),
                    error=str(exc),
                )
                await session.rollback()
        # Source refresh can change which catalog rows remain valid, so cleanup
        # always runs in a fixed order after the per-source refreshes finish.
        await selections_service.prune_stale_builtin_model_selections()
        await selections_service.prune_unconfigured_builtin_model_selections()
        await selections_service.ensure_default_enabled_models()
        repair_summary = await selections_service.repair_legacy_model_selections()
        logger.info(
            "Completed legacy agent model compatibility repair",
            organization_id=str(org_id),
            migrated_defaults=repair_summary.migrated_defaults,
            migrated_presets=repair_summary.migrated_presets,
            migrated_versions=repair_summary.migrated_versions,
            unresolved_defaults=repair_summary.unresolved_defaults,
            unresolved_presets=repair_summary.unresolved_presets,
            unresolved_versions=repair_summary.unresolved_versions,
            ambiguous_defaults=repair_summary.ambiguous_defaults,
            ambiguous_presets=repair_summary.ambiguous_presets,
            ambiguous_versions=repair_summary.ambiguous_versions,
        )


async def sync_model_catalogs_on_startup() -> None:
    logger.info("Attempting model catalog startup sync")
    try:
        async with get_async_session_bypass_rls_context_manager() as session:
            # Only one process should perform startup sync; everyone else waits
            # for the advisory lock holder to finish before returning.
            acquired = await try_pg_advisory_lock(
                session,
                MODEL_CATALOG_STARTUP_SYNC_LOCK_KEY,
            )
            if not acquired:
                logger.info(
                    "Another process is handling model catalog startup sync, waiting to continue"
                )
                async with pg_advisory_lock(
                    session,
                    MODEL_CATALOG_STARTUP_SYNC_LOCK_KEY,
                ):
                    logger.info(
                        "Model catalog startup sync completed by another process"
                    )
                return
            try:
                await _sync_model_catalogs_as_leader(session)
            finally:
                await pg_advisory_unlock(session, MODEL_CATALOG_STARTUP_SYNC_LOCK_KEY)
    except Exception as exc:
        logger.warning("Model catalog startup sync failed", error=str(exc))
