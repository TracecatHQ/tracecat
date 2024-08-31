from typing import Annotated

from fastapi import Depends
from sqlmodel.ext.asyncio.session import AsyncSession

from tracecat.auth.credentials import (
    authenticate_user_for_workspace,
    authenticate_user_or_service_for_workspace,
)
from tracecat.db.engine import get_async_session
from tracecat.types.auth import Role

WorkspaceUserRole = Annotated[Role, Depends(authenticate_user_for_workspace)]
"""Dependency for a user role for a workspace."""

WorkspaceUserOrServiceRole = Annotated[
    Role, Depends(authenticate_user_or_service_for_workspace)
]
"""Dependency for a user or service role for a workspace."""

AsyncDBSession = Annotated[AsyncSession, Depends(get_async_session)]
"""Dependency for an async SQLModel database session."""
