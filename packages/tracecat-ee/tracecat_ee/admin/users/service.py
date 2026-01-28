from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import cast

from sqlalchemy import func, select
from sqlalchemy.orm import Mapped

from tracecat.db.models import User
from tracecat.service import BasePlatformService

from .schemas import AdminUserRead


class AdminUserService(BasePlatformService):
    """Platform-level user management."""

    service_name = "admin_user"

    async def list_users(self) -> Sequence[AdminUserRead]:
        """List all users."""
        stmt = select(User).order_by(cast(Mapped[str], User.email).asc())
        result = await self.session.execute(stmt)
        return AdminUserRead.list_adapter().validate_python(result.scalars().all())

    async def get_user(self, user_id: uuid.UUID) -> AdminUserRead:
        """Get user by ID."""
        stmt = select(User).where(cast(Mapped[uuid.UUID], User.id) == user_id)
        result = await self.session.execute(stmt)
        user = result.scalar_one_or_none()
        if not user:
            raise ValueError(f"User {user_id} not found")
        return AdminUserRead.model_validate(user)

    async def promote_superuser(self, user_id: uuid.UUID) -> AdminUserRead:
        """Promote a user to superuser."""
        stmt = select(User).where(cast(Mapped[uuid.UUID], User.id) == user_id)
        result = await self.session.execute(stmt)
        user = result.scalar_one_or_none()
        if not user:
            raise ValueError(f"User {user_id} not found")

        if user.is_superuser:
            raise ValueError(f"User {user_id} is already a superuser")

        user.is_superuser = True
        await self.session.commit()
        await self.session.refresh(user)
        return AdminUserRead.model_validate(user)

    async def demote_superuser(
        self, user_id: uuid.UUID, current_user_id: uuid.UUID
    ) -> AdminUserRead:
        """Remove superuser status from a user."""
        # Safety: cannot demote yourself
        if user_id == current_user_id:
            raise ValueError("Cannot demote yourself")

        stmt = select(User).where(cast(Mapped[uuid.UUID], User.id) == user_id)
        result = await self.session.execute(stmt)
        user = result.scalar_one_or_none()
        if not user:
            raise ValueError(f"User {user_id} not found")

        if not user.is_superuser:
            raise ValueError(f"User {user_id} is not a superuser")

        # Safety: cannot demote last superuser
        count_stmt = (
            select(func.count())
            .select_from(User)
            .where(cast(Mapped[bool], User.is_superuser) == True)  # noqa: E712
        )
        count_result = await self.session.execute(count_stmt)
        superuser_count = count_result.scalar_one()

        if superuser_count <= 1:
            raise ValueError("Cannot demote the last superuser")

        user.is_superuser = False
        await self.session.commit()
        await self.session.refresh(user)
        return AdminUserRead.model_validate(user)
