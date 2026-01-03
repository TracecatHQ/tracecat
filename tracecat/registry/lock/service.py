"""Service for computing and managing registry version locks."""

from __future__ import annotations

from sqlalchemy import select

from tracecat import config
from tracecat.db.models import RegistryRepository, RegistryVersion
from tracecat.registry.lock.types import RegistryLock
from tracecat.service import BaseService


class RegistryLockService(BaseService):
    """Service for computing and managing registry version locks.

    Registry locks map repository origins to specific version strings,
    allowing workflows to pin their dependent registry versions for
    reproducible execution.
    """

    service_name = "registry_lock"

    async def get_latest_versions_lock(self) -> RegistryLock:
        """Get lock mapping each repository origin to its latest version.

        Queries all RegistryRepositories and finds the most recent
        RegistryVersion for each (by created_at, with id as tiebreaker).

        Returns:
            RegistryLock: Maps origin -> version string.
            Example: {"tracecat_registry": "2024.12.10.123456", "git+ssh://...": "abc1234"}
            Returns empty dict if no versions exist.
        """
        # Use PostgreSQL DISTINCT ON to get exactly one row per repository,
        # ordered by created_at DESC, id DESC for deterministic tiebreaking
        statement = (
            select(RegistryRepository.origin, RegistryVersion.version)
            .join(
                RegistryVersion,
                RegistryVersion.repository_id == RegistryRepository.id,
            )
            .where(
                RegistryRepository.organization_id == config.TRACECAT__DEFAULT_ORG_ID,
                RegistryVersion.organization_id == config.TRACECAT__DEFAULT_ORG_ID,
            )
            .distinct(RegistryVersion.repository_id)
            .order_by(
                RegistryVersion.repository_id,
                RegistryVersion.created_at.desc(),
                RegistryVersion.id.desc(),
            )
        )

        result = await self.session.execute(statement)
        rows = result.all()

        lock: RegistryLock = {str(origin): str(version) for origin, version in rows}

        self.logger.debug("Computed latest versions lock", num_repos=len(lock))

        return lock

    async def get_version_lock_for_repositories(
        self,
        repository_ids: list[str],
    ) -> RegistryLock:
        """Get lock for specific repositories only.

        Args:
            repository_ids: List of repository IDs to get versions for

        Returns:
            RegistryLock: Maps origin -> version string for requested repos.
        """
        if not repository_ids:
            return {}

        # Use PostgreSQL DISTINCT ON to get exactly one row per repository,
        # ordered by created_at DESC, id DESC for deterministic tiebreaking
        statement = (
            select(RegistryRepository.origin, RegistryVersion.version)
            .join(
                RegistryVersion,
                RegistryVersion.repository_id == RegistryRepository.id,
            )
            .where(
                RegistryVersion.organization_id == config.TRACECAT__DEFAULT_ORG_ID,
                RegistryVersion.repository_id.in_(repository_ids),
            )
            .distinct(RegistryVersion.repository_id)
            .order_by(
                RegistryVersion.repository_id,
                RegistryVersion.created_at.desc(),
                RegistryVersion.id.desc(),
            )
        )

        result = await self.session.execute(statement)
        return {str(origin): str(version) for origin, version in result.all()}
