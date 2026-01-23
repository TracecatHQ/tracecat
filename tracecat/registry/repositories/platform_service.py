"""Platform registry repository service for org-agnostic registry management."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import ClassVar

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from tracecat.db.models import PlatformRegistryRepository, PlatformRegistryVersion
from tracecat.exceptions import RegistryError
from tracecat.registry.repositories.schemas import (
    RegistryRepositoryCreate,
    RegistryRepositoryUpdate,
)
from tracecat.service import BaseService


class PlatformRegistryReposService(BaseService):
    """Platform registry repository service.

    Unlike RegistryReposService, this service does NOT filter by organization_id
    and operates on the platform-scoped PlatformRegistryRepository table.
    """

    service_name: ClassVar[str] = "platform_registry_repositories"

    async def list_repositories(self) -> Sequence[PlatformRegistryRepository]:
        """Get all platform registry repositories."""
        statement = select(PlatformRegistryRepository)
        result = await self.session.execute(statement)
        return result.scalars().all()

    async def get_repository(self, origin: str) -> PlatformRegistryRepository | None:
        """Get a platform registry by origin."""
        statement = (
            select(PlatformRegistryRepository)
            .options(
                selectinload(PlatformRegistryRepository.actions),
                selectinload(PlatformRegistryRepository.current_version),
            )
            .where(PlatformRegistryRepository.origin == origin)
        )
        result = await self.session.execute(statement)
        return result.scalar_one_or_none()

    async def get_repository_by_id(
        self, id: uuid.UUID
    ) -> PlatformRegistryRepository | None:
        """Get a platform registry by ID."""
        statement = (
            select(PlatformRegistryRepository)
            .options(
                selectinload(PlatformRegistryRepository.actions),
                selectinload(PlatformRegistryRepository.current_version),
            )
            .where(PlatformRegistryRepository.id == id)
        )
        result = await self.session.execute(statement)
        return result.scalar_one_or_none()

    async def create_repository(
        self, params: RegistryRepositoryCreate
    ) -> PlatformRegistryRepository:
        """Create a new platform registry repository."""
        repository = PlatformRegistryRepository(origin=params.origin)
        self.session.add(repository)
        await self.session.commit()
        await self.session.refresh(repository, ["actions", "current_version"])
        return repository

    async def get_or_create_repository(self, origin: str) -> PlatformRegistryRepository:
        """Get an existing repository or create a new one."""
        repo = await self.get_repository(origin)
        if repo is None:
            repo = await self.create_repository(RegistryRepositoryCreate(origin=origin))
            self.logger.info("Created platform registry repository", origin=origin)
        return repo

    async def update_repository(
        self, repository: PlatformRegistryRepository, params: RegistryRepositoryUpdate
    ) -> PlatformRegistryRepository:
        """Update a platform registry repository."""
        for key, value in params.model_dump(exclude_unset=True).items():
            setattr(repository, key, value)
        self.session.add(repository)
        await self.session.commit()
        await self.session.refresh(repository, ["actions", "current_version"])
        return repository

    async def delete_repository(self, repository: PlatformRegistryRepository) -> None:
        """Delete a platform registry repository."""
        await self.session.delete(repository)
        await self.session.commit()

    async def promote_version(
        self,
        repository: PlatformRegistryRepository,
        version_id: uuid.UUID,
    ) -> PlatformRegistryRepository:
        """Promote a specific version to current.

        Guardrails:
        - Version must exist and belong to this repository
        - Version must have a valid tarball_uri

        Args:
            repository: The repository to promote a version for
            version_id: The ID of the version to promote

        Returns:
            Updated repository with new current_version_id

        Raises:
            RegistryError: If version not found, doesn't belong to repo, or missing tarball
        """
        # Fetch the version and verify it belongs to this repository
        statement = select(PlatformRegistryVersion).where(
            PlatformRegistryVersion.id == version_id
        )
        result = await self.session.execute(statement)
        version = result.scalar_one_or_none()

        if version is None:
            raise RegistryError(f"Platform version '{version_id}' not found")

        if version.repository_id != repository.id:
            raise RegistryError(
                f"Version '{version_id}' does not belong to repository '{repository.origin}'"
            )

        if not version.tarball_uri:
            raise RegistryError(
                f"Version '{version.version}' has no tarball artifact. "
                "Cannot promote a version without execution artifacts."
            )

        # Update current_version_id
        repository.current_version_id = version.id
        self.session.add(repository)
        await self.session.commit()
        await self.session.refresh(repository, ["actions", "current_version"])

        self.logger.info(
            "Promoted platform registry version",
            repository_id=str(repository.id),
            origin=repository.origin,
            version_id=str(version.id),
            version=version.version,
        )

        return repository
