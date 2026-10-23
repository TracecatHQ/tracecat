"""Platform registry sync on API startup with leader election.

This module handles automatic synchronization of the platform registry
when the API starts. It uses PostgreSQL advisory locks for leader election
to prevent race conditions when multiple API processes start simultaneously.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from uuid import UUID

import tracecat_registry
from packaging.version import InvalidVersion, Version
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.authz.seeding import seed_registry_scopes
from tracecat.db.engine import get_async_session_bypass_rls_context_manager
from tracecat.db.locks import (
    derive_lock_key_from_parts,
    pg_advisory_unlock,
    try_pg_advisory_lock,
)
from tracecat.db.models import PlatformRegistryVersion
from tracecat.logger import logger
from tracecat.registry.actions.schemas import RegistryActionCreate
from tracecat.registry.artifact_keys import parse_s3_uri
from tracecat.registry.constants import (
    DEFAULT_REGISTRY_ORIGIN,
    PLATFORM_REGISTRY_NAMESPACE,
)
from tracecat.registry.repositories.platform_service import PlatformRegistryReposService
from tracecat.registry.sync.platform_service import PlatformRegistrySyncService
from tracecat.registry.sync.prebuilt import load_prebuilt_builtin_registry_manifest
from tracecat.registry.versions.schemas import (
    RegistryVersionManifest,
    is_registry_content_version,
    registry_base_version,
    registry_content_version,
    registry_manifest_fingerprint,
)
from tracecat.registry.versions.service import PlatformRegistryVersionsService
from tracecat.storage import blob

MAX_SYNC_RETRIES = 3
PLATFORM_SYNC_LOCK_KEY = derive_lock_key_from_parts("platform_registry_sync")
_RELEASE_TAG_PATTERN = re.compile(
    r"^(?P<major>\d+)\.(?P<minor>\d+)\.(?P<patch>\d+)"
    r"(?:-(?P<stage>alpha|a|beta|b|rc|dev|post)\.(?P<number>\d+)"
    r"(?:-(?P<sub_stage>alpha|a|beta|b|rc|dev|post)\.(?P<sub_number>\d+))?)?$"
)
_STAGE_RANK = {
    "dev": 0,
    "alpha": 1,
    "a": 1,
    "beta": 2,
    "b": 2,
    "rc": 3,
    "final": 4,
    "post": 5,
}


@dataclass(frozen=True, slots=True)
class _ArtifactBuildRequest:
    """Artifact work to run after releasing the startup leader lock."""

    target_version: str
    promote_version_id: UUID | None = None
    expected_current_version_id: UUID | None = None


def _release_tag_key(version: str) -> tuple[int, int, int, int, int, int, int]:
    """Return a sortable key for Tracecat release/image tag versions."""
    match = _RELEASE_TAG_PATTERN.fullmatch(version)
    if match is None:
        raise InvalidVersion(f"Invalid Tracecat release tag: {version!r}")

    stage = match.group("stage") or "final"
    number = int(match.group("number") or 0)
    sub_stage = match.group("sub_stage") or "final"
    sub_number = int(match.group("sub_number") or 0)

    return (
        int(match.group("major")),
        int(match.group("minor")),
        int(match.group("patch")),
        _STAGE_RANK[stage],
        number,
        _STAGE_RANK[sub_stage],
        sub_number,
    )


def _normalize_alpha_hotfix(version: str) -> str:
    """Express alpha hotfix tags as post-releases for both version parsers."""
    return re.sub(
        r"^(\d+\.\d+\.\d+-alpha\.\d+)\.(\d+)$",
        r"\1-post.\2",
        version,
    )


def _is_downgrade(current_version: PlatformRegistryVersion | None, target: str) -> bool:
    """Check if target version would be a downgrade from current.

    Local labels (content or collision versions) are ignored: they mark different
    content for the same release, not an order between releases.
    """
    if current_version is None:
        return False
    target = _normalize_alpha_hotfix(registry_base_version(target))
    current = _normalize_alpha_hotfix(registry_base_version(current_version.version))
    try:
        return Version(target) < Version(current)
    except InvalidVersion:
        return _release_tag_key(target) < _release_tag_key(current)


async def sync_platform_registry_on_startup() -> None:
    """Platform registry sync with leader election.

    Called as background task from API lifespan. Uses non-blocking lock
    to elect a single leader - other processes exit immediately.

    Flow:
    1. Try to acquire advisory lock (non-blocking)
    2. If lock acquired (leader):
       a. Resolve the target: the package version, or a content version when
          the image's prebuilt manifest differs from that version's row
       b. Check if target version already exists and is current → done
       c. If target exists with no current version → build artifact then promote
       d. If version exists but is not current → preserve rollback → done
       e. If version doesn't exist → run sync with retries
    3. Release the leader lock and session, then await any artifact build
    4. If lock not acquired (non-leader) → exit immediately
    """
    target_version = tracecat_registry.__version__
    logger.info("Attempting platform registry sync", target_version=target_version)

    try:
        artifact_build: _ArtifactBuildRequest | None = None
        async with get_async_session_bypass_rls_context_manager() as session:
            # Leader election: try to acquire lock (non-blocking)
            acquired = await try_pg_advisory_lock(session, PLATFORM_SYNC_LOCK_KEY)
            if not acquired:
                logger.info(
                    "Another process is handling platform registry sync, exiting"
                )
                return

            try:
                artifact_build = await _sync_as_leader(session, target_version)
            finally:
                # Always release lock
                await pg_advisory_unlock(session, PLATFORM_SYNC_LOCK_KEY)

        # This coroutine itself is supervised by the API lifespan. Awaiting the
        # artifact build here keeps it inside that lifecycle while still
        # releasing the advisory lock and database session before the build.
        if artifact_build is not None:
            await _build_platform_registry_artifact(
                artifact_build.target_version,
                promote_version_id=artifact_build.promote_version_id,
                expected_current_version_id=(
                    artifact_build.expected_current_version_id
                ),
            )

    except Exception as e:
        logger.warning(
            "Platform registry sync failed",
            error=str(e),
            target_version=target_version,
        )
        # Don't re-raise - API should continue


async def _sync_as_leader(
    session: AsyncSession, target_version: str
) -> _ArtifactBuildRequest | None:
    """Run leader-only sync and return deferred artifact work, if any."""
    repos_service = PlatformRegistryReposService(session)
    versions_service = PlatformRegistryVersionsService(session)

    # Get or create platform repository
    repo = await repos_service.get_or_create_repository(DEFAULT_REGISTRY_ORIGIN)
    is_fresh_install = not await versions_service.has_versions(repository_id=repo.id)

    # Fresh installs have no target version to fetch; keep the first-start path lean.
    base_version = target_version
    existing_version = None
    if not is_fresh_install:
        target_version = await _resolve_content_target_version(
            versions_service,
            repository_id=repo.id,
            base_version=base_version,
        )
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
            return _ArtifactBuildRequest(
                target_version=target_version,
                promote_version_id=existing_version.id,
                expected_current_version_id=existing_version.id,
            )

        # If this repo has versions but no current selection, wait until the
        # referenced artifact is present before repairing the pointer.
        if repo.current_version_id is None:
            logger.info(
                "Target platform registry version exists with no current selection; deferring artifact build before promotion",
                target_version=target_version,
                version_id=str(existing_version.id),
            )
            return _ArtifactBuildRequest(
                target_version=target_version,
                promote_version_id=existing_version.id,
                expected_current_version_id=None,
            )

        # A content row is promoted only after its artifact upload, so a content
        # row without an artifact was never promoted: its earlier build failed.
        # Finish that build instead of treating the row as a rollback.
        if is_registry_content_version(
            target_version, base_version
        ) and not await _artifact_exists(existing_version.tarball_uri):
            logger.info(
                "Content registry version exists without an artifact; retrying artifact build before promotion",
                target_version=target_version,
                version_id=str(existing_version.id),
            )
            return _ArtifactBuildRequest(
                target_version=target_version,
                promote_version_id=existing_version.id,
                expected_current_version_id=repo.current_version_id,
            )

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

            expected_current_version_id = repo.current_version_id
            result = await sync_service.sync_repository_v2(
                repo,
                target_version=target_version,
                bypass_temporal=True,  # Always use subprocess at startup
                defer_artifact_build=True,
                promote=is_fresh_install,
            )
            logger.info(
                "Platform registry sync completed",
                version=result.version_string,
                num_actions=result.num_actions,
                attempt=attempt,
                promoted=is_fresh_install,
            )

            # Seed registry scopes for the synced actions
            await _seed_registry_scopes(session, result.actions)
            return _ArtifactBuildRequest(
                target_version=result.version_string,
                promote_version_id=None if is_fresh_install else result.version.id,
                expected_current_version_id=expected_current_version_id,
            )

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


async def _artifact_exists(artifact_uri: str) -> bool:
    """Return whether a registry version's S3 artifact has been uploaded."""
    bucket, key = parse_s3_uri(artifact_uri)
    return await blob.file_exists(key=key, bucket=bucket)


async def _resolve_content_target_version(
    versions_service: PlatformRegistryVersionsService,
    *,
    repository_id: UUID,
    base_version: str,
) -> str:
    """Return the version that identifies this image's builtin registry content.

    The base version row keeps its first-synced manifest. When the image's
    prebuilt manifest differs from it, the image's content gets its own
    immutable content version instead of being mistaken for the base row.
    Without a prebuilt manifest the content is unknown, so the base version is
    kept and the version string alone decides, as before.
    """
    prebuilt_manifest = load_prebuilt_builtin_registry_manifest(
        origin=DEFAULT_REGISTRY_ORIGIN,
        target_version=base_version,
        storage_namespace=PLATFORM_REGISTRY_NAMESPACE,
    )
    if prebuilt_manifest is None:
        return base_version

    base_row = await versions_service.get_version_by_repo_and_version(
        repository_id=repository_id,
        version=base_version,
    )
    if base_row is None:
        return base_version

    prebuilt_fingerprint = registry_manifest_fingerprint(prebuilt_manifest)
    base_fingerprint = registry_manifest_fingerprint(
        RegistryVersionManifest.model_validate(base_row.manifest)
    )
    if base_fingerprint == prebuilt_fingerprint:
        return base_version

    content_version = registry_content_version(base_version, prebuilt_fingerprint)
    logger.info(
        "Builtin registry content differs from its version row; using content version",
        base_version=base_version,
        content_version=content_version,
        base_fingerprint=base_fingerprint,
        prebuilt_fingerprint=prebuilt_fingerprint,
    )
    return content_version


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
        inserted = await seed_registry_scopes(session, action_keys)
        await session.commit()
        logger.info("Registry scopes seeded", inserted=inserted, total=len(action_keys))
    except DBAPIError as e:
        logger.warning("Failed to seed registry scopes", error=str(e))
        # Don't fail the sync if scope seeding fails due to DB errors
        await session.rollback()


async def _build_platform_registry_artifact(
    target_version: str,
    *,
    promote_version_id: UUID | None = None,
    expected_current_version_id: UUID | None = None,
) -> None:
    async with get_async_session_bypass_rls_context_manager() as session:
        sync_service = PlatformRegistrySyncService(session)
        result = await sync_service._build_and_upload_artifacts(
            origin=DEFAULT_REGISTRY_ORIGIN,
            version_string=target_version,
            commit_sha=None,
        )
        logger.info(
            "Platform registry artifact build completed",
            target_version=target_version,
            artifact_uri=result.artifact_uri,
        )
        if promote_version_id is not None:
            await _promote_platform_registry_version_after_artifact_build(
                session,
                target_version=target_version,
                version_id=promote_version_id,
                artifact_uri=result.artifact_uri,
                expected_current_version_id=expected_current_version_id,
            )


async def _promote_platform_registry_version_after_artifact_build(
    session: AsyncSession,
    *,
    target_version: str,
    version_id: UUID,
    artifact_uri: str,
    expected_current_version_id: UUID | None,
) -> None:
    repos_service = PlatformRegistryReposService(session)
    versions_service = PlatformRegistryVersionsService(session)

    repo = await repos_service.get_repository(DEFAULT_REGISTRY_ORIGIN)
    if repo is None:
        logger.warning(
            "Skipping platform registry promotion after artifact build; repository no longer exists",
            target_version=target_version,
            version_id=str(version_id),
        )
        return

    version = await versions_service.get_version(version_id)
    if version is None:
        logger.warning(
            "Skipping platform registry promotion after artifact build; version no longer exists",
            target_version=target_version,
            version_id=str(version_id),
        )
        return

    if version.repository_id != repo.id or version.version != target_version:
        logger.warning(
            "Skipping platform registry promotion after artifact build; version mismatch",
            target_version=target_version,
            version_id=str(version_id),
            version=str(version.version),
        )
        return

    if repo.current_version_id != expected_current_version_id:
        logger.info(
            "Skipping platform registry promotion after artifact build; current version changed",
            target_version=target_version,
            version_id=str(version_id),
            expected_current_version_id=str(expected_current_version_id)
            if expected_current_version_id
            else None,
            current_version_id=str(repo.current_version_id)
            if repo.current_version_id
            else None,
        )
        return

    if version.tarball_uri != artifact_uri:
        logger.info(
            "Updating platform registry version artifact URI before promotion",
            target_version=target_version,
            version_id=str(version_id),
            previous_artifact_uri=version.tarball_uri,
            artifact_uri=artifact_uri,
        )
        version.tarball_uri = artifact_uri
        session.add(version)

    await repos_service.promote_version(repo, version_id)
    logger.info(
        "Promoted platform registry version after artifact build",
        target_version=target_version,
        version_id=str(version_id),
    )
