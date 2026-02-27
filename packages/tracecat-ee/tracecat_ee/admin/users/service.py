from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import cast

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Mapped

from tracecat.audit.service import AuditService
from tracecat.auth.schemas import UserCreate, UserRole
from tracecat.auth.users import get_user_db_context, get_user_manager_context
from tracecat.db.models import Membership, OrganizationMembership, User
from tracecat.service import BasePlatformService

from .schemas import AdminUserCreate, AdminUserRead


class AdminUserService(BasePlatformService):
    """Platform-level user management."""

    service_name = "admin_user"

    async def create_user(self, params: AdminUserCreate) -> AdminUserRead:
        """Create a platform-level user without org/workspace memberships."""
        async with get_user_db_context(self.session) as user_db:
            async with get_user_manager_context(user_db) as user_manager:
                user_create = UserCreate(email=params.email, password=params.password)
                await user_manager.validate_password(params.password, user_create)
                hashed_password = user_manager.password_helper.hash(params.password)

        user = User(
            email=params.email,
            hashed_password=hashed_password,
            is_active=True,
            is_superuser=params.is_superuser,
            is_verified=True,
            first_name=params.first_name,
            last_name=params.last_name,
            role=UserRole.BASIC,
        )
        self.session.add(user)
        try:
            await self.session.commit()
        except IntegrityError as e:
            await self.session.rollback()
            raise ValueError(f"User with email {params.email} already exists") from e

        await self.session.refresh(user)

        async with AuditService.with_session(
            role=self.role, session=self.session
        ) as svc:
            await svc.create_event(
                resource_type="user",
                action="create",
                resource_id=user.id,
            )

        return AdminUserRead.model_validate(user)

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

    async def delete_user(self, user_id: uuid.UUID, current_user_id: uuid.UUID) -> None:
        """Delete a platform user."""
        if user_id == current_user_id:
            raise ValueError("Cannot delete yourself")

        stmt = select(User).where(cast(Mapped[uuid.UUID], User.id) == user_id)
        result = await self.session.execute(stmt)
        user = result.scalar_one_or_none()
        if not user:
            raise ValueError(f"User {user_id} not found")

        await self.session.execute(
            delete(Membership).where(
                cast(Mapped[uuid.UUID], Membership.user_id) == user_id
            )
        )
        await self.session.execute(
            delete(OrganizationMembership).where(
                cast(Mapped[uuid.UUID], OrganizationMembership.user_id) == user_id
            )
        )

        async with get_user_db_context(self.session) as user_db:
            async with get_user_manager_context(user_db) as user_manager:
                await user_manager.delete(user)

        async with AuditService.with_session(
            role=self.role, session=self.session
        ) as svc:
            await svc.create_event(
                resource_type="user",
                action="delete",
                resource_id=user_id,
            )
