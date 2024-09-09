from typing import Annotated

from fastapi import Depends
from sqlmodel.ext.asyncio.session import AsyncSession

from tracecat.db.engine import get_async_session

AsyncDBSession = Annotated[AsyncSession, Depends(get_async_session)]
"""Dependency for an async SQLModel database session."""
