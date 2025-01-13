from collections.abc import AsyncGenerator, Callable
from contextlib import asynccontextmanager
from typing import Any, Self

from sqlmodel.ext.asyncio.session import AsyncSession

from tracecat.contexts import ctx_role
from tracecat.db.engine import get_async_session_context_manager
from tracecat.logger import logger
from tracecat.types.auth import Role


class BaseService:
    """Base class for services."""

    service_name: str

    def __init__(self, session: AsyncSession, role: Role | None = None):
        self.session = session
        self.role = role or ctx_role.get()
        self.logger = logger.bind(service=self.service_name)

    @classmethod
    @asynccontextmanager
    async def with_session(
        cls,
        role: Role | None = None,
    ) -> AsyncGenerator[Self, None]:
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
