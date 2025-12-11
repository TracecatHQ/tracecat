"""Service for computing and managing registry version locks."""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import aliased

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
        RegistryVersion for each (by created_at).

        Returns:
            RegistryLock: Maps origin -> version string.
            Example: {"tracecat_registry": "2024.12.10.123456", "git+ssh://...": "abc1234"}
            Returns empty dict if no versions exist.
        """
        # Subquery to get the max created_at for each repository
        subq = (
            select(
                RegistryVersion.repository_id,
                func.max(RegistryVersion.created_at).label("max_created_at"),
            )
            .where(RegistryVersion.organization_id == config.TRACECAT__DEFAULT_ORG_ID)
            .group_by(RegistryVersion.repository_id)
            .subquery()
        )

        # Alias for joining
        rv_alias = aliased(RegistryVersion)

        # Main query: join to get the actual version records
        statement = (
            select(RegistryRepository.origin, rv_alias.version)
            .join(
                subq,
                RegistryRepository.id == subq.c.repository_id,
            )
            .join(
                rv_alias,
                (rv_alias.repository_id == subq.c.repository_id)
                & (rv_alias.created_at == subq.c.max_created_at),
            )
            .where(
                RegistryRepository.organization_id == config.TRACECAT__DEFAULT_ORG_ID
            )
        )

        result = await self.session.execute(statement)
        rows = result.all()

        lock: RegistryLock = {str(origin): str(version) for origin, version in rows}

        self.logger.info(
            "Computed latest versions lock",
            num_repos=len(lock),
            lock=lock,
        )

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

        # Subquery to get the max created_at for each specified repository
        subq = (
            select(
                RegistryVersion.repository_id,
                func.max(RegistryVersion.created_at).label("max_created_at"),
            )
            .where(
                RegistryVersion.organization_id == config.TRACECAT__DEFAULT_ORG_ID,
                RegistryVersion.repository_id.in_(repository_ids),
            )
            .group_by(RegistryVersion.repository_id)
            .subquery()
        )

        rv_alias = aliased(RegistryVersion)

        statement = (
            select(RegistryRepository.origin, rv_alias.version)
            .join(
                subq,
                RegistryRepository.id == subq.c.repository_id,
            )
            .join(
                rv_alias,
                (rv_alias.repository_id == subq.c.repository_id)
                & (rv_alias.created_at == subq.c.max_created_at),
            )
        )

        result = await self.session.execute(statement)
        return {str(origin): str(version) for origin, version in result.all()}
