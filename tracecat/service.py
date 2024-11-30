from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Self

from sqlmodel.ext.asyncio.session import AsyncSession

from tracecat.contexts import ctx_role
from tracecat.db.engine import get_async_session_context_manager
from tracecat.logger import logger
from tracecat.types.auth import Role


class Service:
    """Base class for services."""

    _service_name: str

    def __init__(self, session: AsyncSession, role: Role | None = None):
        self.session = session
        self.role = role or ctx_role.get()
        self.logger = logger.bind(service=self._service_name)

    @classmethod
    @asynccontextmanager
    async def with_session(
        cls,
        role: Role | None = None,
    ) -> AsyncGenerator[Self, None]:
        async with get_async_session_context_manager() as session:
            yield cls(session, role=role)
