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
    Workspace,
)
from tracecat.executor.action_runner import get_action_runner
from tracecat.executor.service import get_registry_artifacts_for_lock
from tracecat.identifiers import OrganizationID
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


@dataclass(frozen=True)
class _DefinitionLockCandidate:
    """A workflow definition whose registry lock origins need warming."""

    organization_id: OrganizationID
    workflow_id: str
    origins: dict[str, str]


def _dedupe_definition_candidates(
    candidates: list[_DefinitionLockCandidate],
) -> list[_DefinitionLockCandidate]:
    """Deduplicate candidates by (organization_id, workflow_id), preserving order."""
    deduped: list[_DefinitionLockCandidate] = []
    seen: set[tuple[str, str]] = set()
    for candidate in candidates:
        dedupe_key = (str(candidate.organization_id), candidate.workflow_id)
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        deduped.append(candidate)
    return deduped


def _keep_platform_origins_only(origins: dict[str, str]) -> dict[str, str]:
    """Keep only platform registry origins from a lock origins dict."""
    if version := origins.get(DEFAULT_REGISTRY_ORIGIN):
        return {DEFAULT_REGISTRY_ORIGIN: version}
    return {}


def _filter_definition_candidates_to_platform_origins(
    candidates: list[_DefinitionLockCandidate],
) -> list[_DefinitionLockCandidate]:
    """Drop custom registry origins so warmup only targets platform tarballs."""
    filtered: list[_DefinitionLockCandidate] = []
    for candidate in candidates:
        platform_origins = _keep_platform_origins_only(candidate.origins)
        if not platform_origins:
            continue
        filtered.append(
            _DefinitionLockCandidate(
                organization_id=candidate.organization_id,
                workflow_id=candidate.workflow_id,
                origins=platform_origins,
            )
        )
    return filtered


async def _collect_published_definition_lock_candidates() -> list[
    _DefinitionLockCandidate
]:
    """Query the latest published workflow definitions and return their lock candidates.

    Uses JSONB operators to extract origins directly in SQL, handling both
    the current RegistryLock schema and the legacy flat-dict format.
    """
    stmt = (
        select(
            Workspace.organization_id,
            WorkflowDefinition.workflow_id,
            _origins_jsonb.label("origins"),
        )
        .select_from(WorkflowDefinition)
        .join(Workflow, Workflow.id == WorkflowDefinition.workflow_id)
        .join(
            Workspace,
            and_(
                Workspace.id == Workflow.workspace_id,
                Workspace.id == WorkflowDefinition.workspace_id,
            ),
        )
        .where(
            Workflow.version.is_not(None),
            WorkflowDefinition.version == Workflow.version,
            WorkflowDefinition.registry_lock.is_not(None),
            func.jsonb_typeof(_origins_jsonb) == "object",
        )
        .order_by(WorkflowDefinition.workflow_id.asc())
        .limit(config.TRACECAT__EXECUTOR_WARM_CACHE_MAX_WORKFLOW_DEFINITIONS)
    )
    async with get_async_session_context_manager() as session:
        rows = (await session.execute(stmt)).tuples().all()
    return [
        _DefinitionLockCandidate(
            organization_id=org_id,
            workflow_id=str(wf_id),
            origins=origins,
        )
        for org_id, wf_id, origins in rows
        if origins
    ]


async def _collect_online_schedule_lock_candidates() -> list[_DefinitionLockCandidate]:
    """Query online-scheduled workflow definitions and return their lock candidates.

    Uses JSONB operators to extract origins directly in SQL, handling both
    the current RegistryLock schema and the legacy flat-dict format. The query
    intentionally does not hard-cap rows so deduplication by workflow can be
    applied before enforcing the warmup limit.
    """
    stmt = (
        select(
            Workspace.organization_id,
            WorkflowDefinition.workflow_id,
            _origins_jsonb.label("origins"),
        )
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
        .join(
            Workspace,
            and_(
                Workspace.id == Schedule.workspace_id,
                Workspace.id == Workflow.workspace_id,
                Workspace.id == WorkflowDefinition.workspace_id,
            ),
        )
        .where(
            Schedule.status == "online",
            Workflow.version.is_not(None),
            WorkflowDefinition.registry_lock.is_not(None),
            func.jsonb_typeof(_origins_jsonb) == "object",
        )
        .order_by(Schedule.workflow_id.asc())
    )
    async with get_async_session_context_manager() as session:
        rows = (await session.execute(stmt)).tuples().all()
    return [
        _DefinitionLockCandidate(
            organization_id=org_id,
            workflow_id=str(wf_id),
            origins=origins,
        )
        for org_id, wf_id, origins in rows
        if origins
    ]


async def _resolve_definition_tarball_uris(
    candidates: list[_DefinitionLockCandidate],
) -> set[str]:
    """Resolve lock candidates to concrete platform tarball URIs via the registry."""
    uris: set[str] = set()
    seen: set[tuple[str, tuple[tuple[str, str], ...]]] = set()

    for candidate in candidates:
        dedupe_key = (
            str(candidate.organization_id),
            tuple(sorted(candidate.origins.items())),
        )
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)

        artifacts = await get_registry_artifacts_for_lock(
            origins=candidate.origins,
            organization_id=candidate.organization_id,
        )
        for artifact in artifacts:
            if artifact.tarball_uri:
                uris.add(artifact.tarball_uri)
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
    published_candidates = await _collect_published_definition_lock_candidates()
    scheduled_candidates = await _collect_online_schedule_lock_candidates()
    max_definitions = max(
        1, config.TRACECAT__EXECUTOR_WARM_CACHE_MAX_WORKFLOW_DEFINITIONS
    )
    definition_candidates = _dedupe_definition_candidates(
        [*published_candidates, *scheduled_candidates]
    )[:max_definitions]
    platform_definition_candidates = _filter_definition_candidates_to_platform_origins(
        definition_candidates
    )
    definition_uris = await _resolve_definition_tarball_uris(
        platform_definition_candidates
    )
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
        published_definition_rows=len(published_candidates),
        scheduled_definition_rows=len(scheduled_candidates),
        definition_rows=len(platform_definition_candidates),
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
