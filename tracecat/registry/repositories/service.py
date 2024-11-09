from __future__ import annotations

from collections.abc import AsyncGenerator, Sequence
from contextlib import asynccontextmanager

from pydantic import UUID4
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from tracecat import config
from tracecat.contexts import ctx_role
from tracecat.db.engine import get_async_session_context_manager
from tracecat.db.schemas import RegistryRepository
from tracecat.logger import logger
from tracecat.registry.repositories.models import RegistryRepositoryCreate
from tracecat.types.auth import Role


class RegistryReposService:
    """Registry repository service."""

    def __init__(self, session: AsyncSession, role: Role | None = None):
        self.role = role or ctx_role.get()
        self.session = session
        self.logger = logger.bind(service="registry-repositories")

    @asynccontextmanager
    @staticmethod
    async def with_session(
        role: Role | None = None,
    ) -> AsyncGenerator[RegistryReposService, None]:
        async with get_async_session_context_manager() as session:
            yield RegistryReposService(session, role=role)

    async def list_repositories(self) -> Sequence[RegistryRepository]:
        """Get all registry repositories."""
        statement = select(RegistryRepository)
        result = await self.session.exec(statement)
        return result.all()

    async def get_repository(self, origin: str) -> RegistryRepository | None:
        """Get a registry by origin."""
        statement = select(RegistryRepository).where(
            RegistryRepository.origin == origin
        )
        result = await self.session.exec(statement)
        return result.one_or_none()

    async def get_repository_by_id(self, id: UUID4) -> RegistryRepository | None:
        """Get a registry by ID."""
        statement = select(RegistryRepository).where(RegistryRepository.id == id)
        result = await self.session.exec(statement)
        return result.one_or_none()

    async def create_repository(
        self, params: RegistryRepositoryCreate
    ) -> RegistryRepository:
        """Create a new registry repository."""
        repository = RegistryRepository(
            owner_id=config.TRACECAT__DEFAULT_ORG_ID, origin=params.origin
        )
        self.session.add(repository)
        await self.session.commit()
        await self.session.refresh(repository)
        return repository

    async def update_repository(
        self, repository: RegistryRepository
    ) -> RegistryRepository:
        """Update a registry repository."""
        self.session.add(repository)
        await self.session.commit()
        await self.session.refresh(repository)
        return repository

    async def delete_repository(self, repository: RegistryRepository) -> None:
        """Delete a registry repository."""
        await self.session.delete(repository)
        await self.session.commit()
