from collections.abc import AsyncGenerator, Callable
from contextlib import asynccontextmanager
from typing import Any, ClassVar, Self

from sqlalchemy.ext.asyncio import AsyncSession

from tracecat import config
from tracecat.auth.types import Role
from tracecat.contexts import ctx_role
from tracecat.db.engine import get_async_session_context_manager
from tracecat.exceptions import TracecatAuthorizationError
from tracecat.identifiers import OrganizationID
from tracecat.logger import logger


class BaseService:
    """Base class for services."""

    service_name: ClassVar[str]

    def __init__(self, session: AsyncSession, role: Role | None = None):
        self.session = session
        self.role = role or ctx_role.get()
        self.logger = logger.bind(service=self.service_name)

    @property
    def organization_id(self) -> OrganizationID:
        if self.role is None:
            return config.TRACECAT__DEFAULT_ORG_ID
        return self.role.organization_id

    @classmethod
    @asynccontextmanager
    async def with_session(
        cls,
        role: Role | None = None,
        *,
        session: AsyncSession | None = None,
    ) -> AsyncGenerator[Self, None]:
        """Create a service instance with a database session.

        Args:
            role: Optional role for authorization context.
            session: Optional existing session. If provided, caller is responsible
                for managing its lifecycle (it won't be closed by this context manager).
        """
        if session is not None:
            yield cls(session, role=role)
        else:
            async with get_async_session_context_manager() as session:
                yield cls(session, role=role)

    @classmethod
    def get_activities(cls) -> list[Callable[..., Any]]:
        """Get all temporal activities in the class."""
        return [
            getattr(cls, method_name)
            for method_name in dir(cls)
            if hasattr(getattr(cls, method_name), "__temporal_activity_definition")
        ]


class BaseWorkspaceService(BaseService):
    """Base class for services that require a workspace."""

    role: Role  # Override parent - always non-None for workspace services

    def __init__(self, session: AsyncSession, role: Role | None = None):
        super().__init__(session, role)
        if self.role is None or self.role.workspace_id is None:
            raise TracecatAuthorizationError(
                f"{self.service_name} service requires workspace"
            )
        self.workspace_id = self.role.workspace_id
