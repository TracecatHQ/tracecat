"""Bootstrap service for direct database operations.

This module provides direct database access for bootstrap scenarios,
such as creating the first superuser before any authenticated users exist.

Requires: tracecat package to be installed (tracecat-admin[bootstrap]).
"""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import EmailStr

from tracecat.auth.schemas import UserCreate, UserRole
from tracecat.auth.users import get_or_create_user, lookup_user_by_email
from tracecat.db.engine import get_async_session_context_manager


@dataclass
class CreateSuperuserResult:
    """Result of create_superuser operation."""

    email: str
    user_id: str
    created: bool  # True if user was created, False if promoted existing


async def create_superuser(
    email: str,
    password: str | None = None,
    create: bool = False,
) -> CreateSuperuserResult:
    """Create or promote a user to superuser status.

    Args:
        email: User email address.
        password: Password for new user (required if create=True).
        create: If True, create a new user. If False, promote existing user.

    Returns:
        CreateSuperuserResult with user details.

    Raises:
        ValueError: If user not found (when create=False) or already superuser.
        ValueError: If password not provided when create=True.
    """
    async with get_async_session_context_manager() as session:
        if create:
            if not password:
                raise ValueError("Password is required when creating a new user")

            # Check if user already exists
            existing = await lookup_user_by_email(session=session, email=email)
            if existing:
                raise ValueError(f"User with email '{email}' already exists")

            # Create the user
            user_create = UserCreate(
                email=EmailStr(email),
                password=password,
                is_superuser=True,
                is_verified=True,
                role=UserRole.ADMIN,
            )
            user = await get_or_create_user(user_create, exist_ok=False)

            return CreateSuperuserResult(
                email=user.email,
                user_id=str(user.id),
                created=True,
            )
        else:
            # Find existing user and promote
            user = await lookup_user_by_email(session=session, email=email)
            if not user:
                raise ValueError(f"User with email '{email}' not found")

            if user.is_superuser:
                raise ValueError(f"User '{email}' is already a superuser")

            # Promote to superuser
            user.is_superuser = True
            user.role = UserRole.ADMIN
            await session.commit()
            await session.refresh(user)

            return CreateSuperuserResult(
                email=user.email,
                user_id=str(user.id),
                created=False,
            )
