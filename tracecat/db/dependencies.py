from collections.abc import AsyncGenerator
from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.db.engine import get_async_session, get_async_session_bypass_rls

AsyncDBSession = Annotated[AsyncSession, Depends(get_async_session)]
"""Dependency for an async SQLAlchemy database session."""

AsyncDBSessionBypass = Annotated[AsyncSession, Depends(get_async_session_bypass_rls)]
"""Dependency for an async SQLAlchemy database session with explicit RLS bypass."""


async def _release_request_session(
    session: AsyncDBSession,
) -> AsyncGenerator[None, None]:
    yield
    await session.close()


ReleaseRequestSession = Depends(_release_request_session, scope="function")
"""Route dependency that returns the request session's connection to the pool
as soon as the endpoint returns, instead of after the response body is sent.

Use it on streaming routes: the request session (shared with auth dependencies)
otherwise stays checked out, idle in transaction, for the whole stream.
"""
