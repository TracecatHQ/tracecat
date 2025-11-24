from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.db.engine import get_async_session

AsyncDBSession = Annotated[AsyncSession, Depends(get_async_session)]
"""Dependency for an async SQLAlchemy database session."""
