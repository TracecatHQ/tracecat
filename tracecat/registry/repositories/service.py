from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from tracecat.db.models import RegistryRepository, RegistryVersion
from tracecat.exceptions import RegistryError
from tracecat.registry.repositories.schemas import (
    RegistryRepositoryCreate,
    RegistryRepositoryUpdate,
)
from tracecat.registry.versions.service import RegistryVersionsService
from tracecat.service import BaseOrgService


class RegistryReposService(BaseOrgService):
    """Registry repository service."""

    service_name = "registry_repositories"

    async def list_repositories(self) -> Sequence[RegistryRepository]:
        """Get all registry repositories."""
        statement = select(RegistryRepository).where(
            RegistryRepository.organization_id == self.organization_id
        )
        result = await self.session.execute(statement)
        return result.scalars().all()

    async def get_repository(self, origin: str) -> RegistryRepository | None:
        """Get a registry by origin."""
        statement = (
            select(RegistryRepository)
            .options(selectinload(RegistryRepository.actions))
            .where(
                RegistryRepository.organization_id == self.organization_id,
                RegistryRepository.origin == origin,
            )
        )
        result = await self.session.execute(statement)
        return result.scalar_one_or_none()

    async def get_repository_by_id(self, id: uuid.UUID) -> RegistryRepository:
        """Get a registry by ID."""
        statement = (
            select(RegistryRepository)
            .options(selectinload(RegistryRepository.actions))
            .where(
                RegistryRepository.organization_id == self.organization_id,
                RegistryRepository.id == id,
            )
        )
        result = await self.session.execute(statement)
        return result.scalar_one()

    async def create_repository(
        self, params: RegistryRepositoryCreate
    ) -> RegistryRepository:
        """Create a new registry repository."""
        repository = RegistryRepository(
            organization_id=self.organization_id, origin=params.origin
        )
        self.session.add(repository)
        await self.session.commit()
        await self.session.refresh(repository, ["actions"])
        return repository

    async def update_repository(
        self, repository: RegistryRepository, params: RegistryRepositoryUpdate
    ) -> RegistryRepository:
        """Update a registry repository."""
        for key, value in params.model_dump(exclude_unset=True).items():
            setattr(repository, key, value)
        self.session.add(repository)
        await self.session.commit()
        await self.session.refresh(repository, ["actions"])
        return repository

    async def delete_repository(self, repository: RegistryRepository) -> None:
        """Delete a registry repository."""
        await self.session.delete(repository)
        await self.session.commit()

    async def promote_version(
        self,
        repository: RegistryRepository,
        version_id: uuid.UUID,
    ) -> RegistryRepository:
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
        statement = select(RegistryVersion).where(
            RegistryVersion.id == version_id,
            RegistryVersion.organization_id == self.organization_id,
        )
        result = await self.session.execute(statement)
        version = result.scalar_one_or_none()

        if version is None:
            raise RegistryError(f"Version '{version_id}' not found in organization")

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
        await self.session.refresh(repository, ["actions"])

        self.logger.info(
            "Promoted registry version",
            repository_id=str(repository.id),
            origin=repository.origin,
            version_id=str(version.id),
            version=version.version,
        )

        return repository

    async def validate_version_deletion(
        self,
        repository: RegistryRepository,
        version: RegistryVersion,
    ) -> None:
        """Validate that a version can be deleted.

        Raises:
            RegistryError: If the version cannot be deleted
        """
        # Check 1: Cannot delete the currently promoted version
        if repository.current_version_id == version.id:
            raise RegistryError(
                "Cannot delete the currently promoted version. Promote another version first."
            )

        # Check 2: Cannot delete if published workflow definitions reference this version
        # Query WorkflowDefinition.registry_lock JSONB using containment operator
        # registry_lock structure: {"origins": {"origin": "version_string"}, "actions": {...}}
        versions_service = RegistryVersionsService(self.session, self.role)
        definitions = await versions_service.get_workflow_definitions_using_version(
            origin=repository.origin,
            version_string=version.version,
        )
        if definitions:
            workflow_ids = [str(d.workflow_id) for d in definitions[:5]]  # Show first 5
            more = f" and {len(definitions) - 5} more" if len(definitions) > 5 else ""
            raise RegistryError(
                f"Cannot delete version in use by published workflows: {', '.join(workflow_ids)}{more}"
            )
