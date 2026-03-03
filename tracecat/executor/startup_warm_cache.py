"""Executor startup warm cache.

Pre-warms the executor's registry cache on startup by downloading and extracting
tarball environments for workflow definitions that are likely to be needed soon.

The warmup strategy collects tarball URIs from three sources:
1. Published workflow definitions (platform origins only).
2. Online-scheduled workflow definitions (platform origins only).
3. Platform registry current versions (builtin registry tarballs).

These are deduplicated, capped, and concurrently downloaded with a configurable
timeout so that slow or failing downloads never block executor startup.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from sqlalchemy import and_, case, func, select

from tracecat import config
from tracecat.db.engine import get_async_session_context_manager
from tracecat.db.models import (
    PlatformRegistryRepository,
    PlatformRegistryVersion,
    Schedule,
    Workflow,
    WorkflowDefinition,
)
from tracecat.executor.action_runner import get_action_runner
from tracecat.logger import logger
from tracecat.registry.constants import DEFAULT_REGISTRY_ORIGIN

# SQL expression that extracts the origins dict from the JSONB registry_lock
# column. Handles both the current RegistryLock schema (registry_lock->'origins')
# and the legacy flat origin->version dict format.
_origins_jsonb = case(
    (
        WorkflowDefinition.registry_lock.has_key("origins"),
        WorkflowDefinition.registry_lock["origins"],
    ),
    else_=WorkflowDefinition.registry_lock,
)
_platform_lock_version = _origins_jsonb[DEFAULT_REGISTRY_ORIGIN].astext


@dataclass
class WarmCacheReport:
    """Summary of a startup warmup attempt."""

    enabled: bool
    skipped_reason: str | None = None
    timed_out: bool = False
    published_definition_rows: int = 0
    scheduled_definition_rows: int = 0
    definition_rows: int = 0
    definition_locks: int = 0
    platform_tarballs: int = 0
    candidate_tarballs: int = 0
    warmed_tarballs: int = 0
    failed_tarballs: int = 0


def _dedupe_versions(values: list[str]) -> list[str]:
    """Deduplicate version strings while preserving order."""
    return list(dict.fromkeys(values))


async def _collect_published_platform_lock_versions() -> list[str]:
    """Query latest published workflow definitions and return platform lock versions.

    Uses JSONB operators to extract origins directly in SQL, handling both
    the current RegistryLock schema and the legacy flat-dict format.
    """
    stmt = (
        select(_platform_lock_version.label("version"))
        .select_from(WorkflowDefinition)
        .join(Workflow, Workflow.id == WorkflowDefinition.workflow_id)
        .where(
            Workflow.version.is_not(None),
            WorkflowDefinition.version == Workflow.version,
            WorkflowDefinition.registry_lock.is_not(None),
            func.jsonb_typeof(_origins_jsonb) == "object",
            _platform_lock_version.is_not(None),
        )
        .order_by(WorkflowDefinition.workflow_id.asc())
    )
    async with get_async_session_context_manager() as session:
        rows = (await session.execute(stmt)).tuples().all()
    return [version for (version,) in rows if version]


async def _collect_online_schedule_platform_lock_versions() -> list[str]:
    """Query online-scheduled workflow definitions and return platform lock versions.

    Uses JSONB operators to extract origins directly in SQL, handling both
    the current RegistryLock schema and the legacy flat-dict format.
    """
    stmt = (
        select(_platform_lock_version.label("version"))
        .select_from(Schedule)
        .join(
            Workflow,
            and_(
                Workflow.id == Schedule.workflow_id,
                Workflow.workspace_id == Schedule.workspace_id,
            ),
        )
        .join(
            WorkflowDefinition,
            and_(
                WorkflowDefinition.workflow_id == Workflow.id,
                WorkflowDefinition.workspace_id == Workflow.workspace_id,
                WorkflowDefinition.version == Workflow.version,
            ),
        )
        .where(
            Schedule.status == "online",
            Workflow.version.is_not(None),
            WorkflowDefinition.registry_lock.is_not(None),
            func.jsonb_typeof(_origins_jsonb) == "object",
            _platform_lock_version.is_not(None),
        )
        .order_by(Schedule.workflow_id.asc())
    )
    async with get_async_session_context_manager() as session:
        rows = (await session.execute(stmt)).tuples().all()
    return [version for (version,) in rows if version]


async def _resolve_definition_tarball_uris(
    versions: list[str],
) -> set[str]:
    """Resolve platform lock versions to tarball URIs via platform registry tables."""
    if not versions:
        return set()

    deduped_versions = _dedupe_versions(versions)
    stmt = (
        select(PlatformRegistryVersion.tarball_uri)
        .select_from(PlatformRegistryVersion)
        .join(
            PlatformRegistryRepository,
            PlatformRegistryVersion.repository_id == PlatformRegistryRepository.id,
        )
        .where(
            PlatformRegistryRepository.origin == DEFAULT_REGISTRY_ORIGIN,
            PlatformRegistryVersion.version.in_(deduped_versions),
            PlatformRegistryVersion.tarball_uri.is_not(None),
        )
        .order_by(PlatformRegistryVersion.version.asc())
    )

    uris: set[str] = set()
    async with get_async_session_context_manager() as session:
        for (tarball_uri,) in (await session.execute(stmt)).tuples():
            if tarball_uri:
                uris.add(tarball_uri)
    return uris


async def _collect_platform_current_tarball_uris() -> set[str]:
    """Return tarball URIs for the current version of each platform registry repository."""
    stmt = (
        select(
            PlatformRegistryRepository.origin,
            PlatformRegistryVersion.tarball_uri,
        )
        .join(
            PlatformRegistryVersion,
            PlatformRegistryRepository.current_version_id == PlatformRegistryVersion.id,
        )
        .where(PlatformRegistryVersion.tarball_uri.is_not(None))
        .order_by(PlatformRegistryRepository.origin.asc())
    )

    uris: set[str] = set()
    async with get_async_session_context_manager() as session:
        for _, tarball_uri in (await session.execute(stmt)).tuples():
            if tarball_uri:
                uris.add(tarball_uri)
    return uris


async def _warm_tarball_uris(uris: list[str]) -> tuple[int, int]:
    """Download and extract tarballs concurrently. Returns (warmed, failed) counts."""
    if not uris:
        return 0, 0

    concurrency = max(1, config.TRACECAT__EXECUTOR_WARM_CACHE_CONCURRENCY)
    semaphore = asyncio.Semaphore(concurrency)
    runner = get_action_runner()

    async def _warm_one(tarball_uri: str) -> bool:
        async with semaphore:
            try:
                await runner.ensure_registry_environment(tarball_uri)
                return True
            except Exception as e:
                logger.warning(
                    "Failed to warm executor registry tarball",
                    tarball_uri=tarball_uri,
                    error=str(e),
                )
                return False

    results = await asyncio.gather(*(_warm_one(uri) for uri in uris))
    warmed = sum(1 for ok in results if ok)
    failed = len(results) - warmed
    return warmed, failed


async def _run_warmup() -> WarmCacheReport:
    """Execute the full warmup pipeline: collect, deduplicate, resolve, and warm."""
    published_versions = await _collect_published_platform_lock_versions()
    scheduled_versions = await _collect_online_schedule_platform_lock_versions()
    max_locked_versions = max(
        1, config.TRACECAT__EXECUTOR_WARM_CACHE_MAX_LOCKED_VERSIONS
    )
    definition_versions = _dedupe_versions([*published_versions, *scheduled_versions])[
        :max_locked_versions
    ]
    definition_uris = await _resolve_definition_tarball_uris(definition_versions)
    platform_uris = await _collect_platform_current_tarball_uris()

    all_uris = sorted(definition_uris | platform_uris)
    max_tarballs = max(1, config.TRACECAT__EXECUTOR_WARM_CACHE_MAX_TARBALLS)
    if len(all_uris) > max_tarballs:
        logger.info(
            "Truncating warmup tarball set due to cap",
            total_candidates=len(all_uris),
            cap=max_tarballs,
        )
        all_uris = all_uris[:max_tarballs]

    warmed, failed = await _warm_tarball_uris(all_uris)
    return WarmCacheReport(
        enabled=True,
        published_definition_rows=len(published_versions),
        scheduled_definition_rows=len(scheduled_versions),
        definition_rows=len(definition_versions),
        definition_locks=len(definition_uris),
        platform_tarballs=len(platform_uris),
        candidate_tarballs=len(all_uris),
        warmed_tarballs=warmed,
        failed_tarballs=failed,
    )


async def warm_registry_cache_on_startup() -> WarmCacheReport:
    """Top-level entry point for executor startup cache warming.

    Applies a configurable timeout and catches all errors so that warmup
    failures never prevent the executor from starting.
    """
    if not config.TRACECAT__EXECUTOR_WARM_CACHE_ENABLED:
        return WarmCacheReport(
            enabled=False,
            skipped_reason="warmup_disabled",
        )
    if config.TRACECAT__LOCAL_REPOSITORY_ENABLED:
        return WarmCacheReport(
            enabled=False,
            skipped_reason="local_repository_enabled",
        )

    timeout_seconds = max(1.0, config.TRACECAT__EXECUTOR_WARM_CACHE_TIMEOUT_SECONDS)
    try:
        report = await asyncio.wait_for(_run_warmup(), timeout=timeout_seconds)
    except TimeoutError:
        logger.warning(
            "Executor warmup timed out; continuing startup",
            timeout_seconds=timeout_seconds,
        )
        return WarmCacheReport(
            enabled=True,
            timed_out=True,
        )
    except Exception as e:
        logger.exception(
            "Executor warmup failed unexpectedly; continuing startup",
            error=str(e),
        )
        return WarmCacheReport(
            enabled=True,
            skipped_reason="unexpected_error",
        )

    logger.info(
        "Executor warmup completed",
        published_definition_rows=report.published_definition_rows,
        scheduled_definition_rows=report.scheduled_definition_rows,
        definition_rows=report.definition_rows,
        definition_tarballs=report.definition_locks,
        platform_tarballs=report.platform_tarballs,
        candidate_tarballs=report.candidate_tarballs,
        warmed_tarballs=report.warmed_tarballs,
        failed_tarballs=report.failed_tarballs,
    )
    return report
