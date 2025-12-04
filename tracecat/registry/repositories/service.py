from __future__ import annotations

from collections.abc import Sequence

from pydantic import UUID4
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from tracecat import config
from tracecat.db.models import RegistryRepository
from tracecat.registry.repositories.schemas import (
    RegistryRepositoryCreate,
    RegistryRepositoryUpdate,
)
from tracecat.service import BaseService


class RegistryReposService(BaseService):
    """Registry repository service."""

    service_name = "registry_repositories"

    async def list_repositories(self) -> Sequence[RegistryRepository]:
        """Get all registry repositories."""
        statement = select(RegistryRepository)
        result = await self.session.execute(statement)
        return result.scalars().all()

    async def get_repository(self, origin: str) -> RegistryRepository | None:
        """Get a registry by origin."""
        statement = (
            select(RegistryRepository)
            .options(selectinload(RegistryRepository.actions))
            .where(RegistryRepository.origin == origin)
        )
        result = await self.session.execute(statement)
        return result.scalar_one_or_none()

    async def get_repository_by_id(self, id: UUID4) -> RegistryRepository:
        """Get a registry by ID."""
        statement = (
            select(RegistryRepository)
            .options(selectinload(RegistryRepository.actions))
            .where(RegistryRepository.id == id)
        )
        result = await self.session.execute(statement)
        return result.scalar_one()

    async def create_repository(
        self, params: RegistryRepositoryCreate
    ) -> RegistryRepository:
        """Create a new registry repository."""
        repository = RegistryRepository(
            organization_id=config.TRACECAT__DEFAULT_ORG_ID, origin=params.origin
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
