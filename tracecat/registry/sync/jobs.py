"""Platform registry sync on API startup with leader election.

This module handles automatic synchronization of the platform registry
when the API starts. It uses PostgreSQL advisory locks for leader election
to prevent race conditions when multiple API processes start simultaneously.
"""

from __future__ import annotations

import tracecat_registry
from packaging.version import Version
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.authz.seeding import seed_registry_scopes_bulk
from tracecat.db.engine import get_async_session_context_manager
from tracecat.db.locks import (
    derive_lock_key_from_parts,
    pg_advisory_unlock,
    try_pg_advisory_lock,
)
from tracecat.db.models import PlatformRegistryVersion
from tracecat.logger import logger
from tracecat.registry.actions.schemas import RegistryActionCreate
from tracecat.registry.constants import DEFAULT_REGISTRY_ORIGIN
from tracecat.registry.repositories.platform_service import PlatformRegistryReposService
from tracecat.registry.sync.platform_service import PlatformRegistrySyncService
from tracecat.registry.versions.service import PlatformRegistryVersionsService

MAX_SYNC_RETRIES = 3
PLATFORM_SYNC_LOCK_KEY = derive_lock_key_from_parts("platform_registry_sync")


def _is_downgrade(current_version: PlatformRegistryVersion | None, target: str) -> bool:
    """Check if target version would be a downgrade from current."""
    if current_version is None:
        return False
    return Version(target) < Version(current_version.version)


async def sync_platform_registry_on_startup() -> None:
    """Platform registry sync with leader election.

    Called as background task from API lifespan. Uses non-blocking lock
    to elect a single leader - other processes exit immediately.

    Flow:
    1. Try to acquire advisory lock (non-blocking)
    2. If lock acquired (leader):
       a. Check if target version already exists and is current → done
       b. If version exists but not current → no-downgrade check → promote → done
       c. If version doesn't exist → run sync with retries
    3. If lock not acquired (non-leader) → exit immediately
    """
    target_version = tracecat_registry.__version__
    logger.info("Attempting platform registry sync", target_version=target_version)

    try:
        async with get_async_session_context_manager() as session:
            # Leader election: try to acquire lock (non-blocking)
            acquired = await try_pg_advisory_lock(session, PLATFORM_SYNC_LOCK_KEY)
            if not acquired:
                logger.info(
                    "Another process is handling platform registry sync, exiting"
                )
                return

            try:
                await _sync_as_leader(session, target_version)
            finally:
                # Always release lock
                await pg_advisory_unlock(session, PLATFORM_SYNC_LOCK_KEY)

    except Exception as e:
        logger.warning(
            "Platform registry sync failed",
            error=str(e),
            target_version=target_version,
        )
        # Don't re-raise - API should continue


async def _sync_as_leader(session: AsyncSession, target_version: str) -> None:
    """Leader-only sync logic with retries."""
    repos_service = PlatformRegistryReposService(session)
    versions_service = PlatformRegistryVersionsService(session)

    # Get or create platform repository
    repo = await repos_service.get_or_create_repository(DEFAULT_REGISTRY_ORIGIN)

    # Check if target version already exists
    existing_version = await versions_service.get_version_by_repo_and_version(
        repository_id=repo.id,
        version=target_version,
    )

    if existing_version:
        # Version exists - check if it's already current
        if repo.current_version_id == existing_version.id:
            logger.info(
                "Platform registry already at target version",
                version=target_version,
            )
            return

        # Version exists but is not current - don't auto-promote
        # There may be a deliberate reason it's not current (e.g., manual rollback)
        logger.info(
            "Target version exists but is not current, skipping auto-promotion",
            target_version=target_version,
            current_version=repo.current_version.version
            if repo.current_version
            else None,
        )
        return

    # No-downgrade guard: check before attempting sync
    if _is_downgrade(repo.current_version, target_version):
        if repo.current_version is None:
            raise RuntimeError(
                "current_version is None but _is_downgrade returned True"
            )
        logger.warning(
            "Refusing to downgrade platform registry",
            current=repo.current_version.version,
            target=target_version,
        )
        return

    # Version doesn't exist - need to sync (with retries)
    sync_service = PlatformRegistrySyncService(session)

    for attempt in range(1, MAX_SYNC_RETRIES + 1):
        try:
            # Re-check downgrade guard in case another process updated during retries
            await session.refresh(repo, ["current_version"])
            if _is_downgrade(repo.current_version, target_version):
                if repo.current_version is None:
                    raise RuntimeError(
                        "current_version is None but _is_downgrade returned True"
                    )
                logger.warning(
                    "Refusing to downgrade platform registry (detected during retry)",
                    current=repo.current_version.version,
                    target=target_version,
                )
                return

            result = await sync_service.sync_repository_v2(
                repo,
                target_version=target_version,
                bypass_temporal=True,  # Always use subprocess at startup
            )
            logger.info(
                "Platform registry sync completed",
                version=result.version_string,
                num_actions=result.num_actions,
                attempt=attempt,
            )

            # Seed registry scopes for the synced actions
            await _seed_registry_scopes(session, result.actions)
            return

        except Exception as e:
            logger.warning(
                "Platform registry sync attempt failed",
                error=str(e),
                attempt=attempt,
                max_retries=MAX_SYNC_RETRIES,
            )
            # Rollback to clear any failed transaction state before retry
            await session.rollback()
            if attempt == MAX_SYNC_RETRIES:
                raise


async def _seed_registry_scopes(
    session: AsyncSession,
    actions: list[RegistryActionCreate],
) -> None:
    """Seed registry scopes for synced actions.

    Creates `action:{action_key}:execute` scopes for each action.
    Uses bulk upsert for efficiency.

    Args:
        session: Database session
        actions: List of RegistryActionCreate objects from sync
    """
    if not actions:
        return

    # Extract action keys from the actions
    action_keys = [f"{action.namespace}.{action.name}" for action in actions]

    try:
        inserted = await seed_registry_scopes_bulk(session, action_keys)
        await session.commit()
        logger.info("Registry scopes seeded", inserted=inserted, total=len(action_keys))
    except DBAPIError as e:
        logger.warning("Failed to seed registry scopes", error=str(e))
        # Don't fail the sync if scope seeding fails due to DB errors
        await session.rollback()
