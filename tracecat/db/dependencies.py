from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.db.engine import get_async_session, get_async_session_bypass_rls

AsyncDBSession = Annotated[AsyncSession, Depends(get_async_session)]
"""Dependency for an async SQLAlchemy database session."""

AsyncDBSessionBypass = Annotated[AsyncSession, Depends(get_async_session_bypass_rls)]
"""Dependency for an async SQLAlchemy database session with explicit RLS bypass."""
