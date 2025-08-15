from typing import Annotated, TypeAlias

from fastapi import Depends
from sqlmodel.ext.asyncio.session import AsyncSession

from tracecat.db.engine import get_async_session

AsyncDBSession: TypeAlias = Annotated[AsyncSession, Depends(get_async_session)]
"""Dependency for an async SQLModel database session."""
