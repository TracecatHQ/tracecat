from __future__ import annotations

from collections.abc import AsyncGenerator, Sequence
from contextlib import asynccontextmanager

from sqlmodel import select

from tracecat.auth.models import UserUpdate
from tracecat.auth.users import (
    UserManager,
    get_user_db_context,
    get_user_manager_context,
)
from tracecat.authz.controls import require_access_level
from tracecat.db.schemas import User
from tracecat.identifiers import UserID
from tracecat.service import Service
from tracecat.types.auth import AccessLevel
from tracecat.types.exceptions import TracecatAuthorizationError


class OrgService(Service):
    """Manage the organization."""

    _service_name = "org"

    @asynccontextmanager
    async def _manager(self) -> AsyncGenerator[UserManager, None]:
        async with get_user_db_context(self.session) as user_db:
            async with get_user_manager_context(user_db) as user_manager:
                yield user_manager

    # === Manage members ===

    @require_access_level(AccessLevel.ADMIN)
    async def list_members(self) -> Sequence[User]:
        """
        Retrieve a list of all members in the organization.

        This method queries the database to obtain all user records
        associated with the organization. It returns a sequence of
        User objects representing each member.

        Returns:
            Sequence[User]: A sequence containing User objects of all
            members in the organization.
        """
        statement = select(User)
        result = await self.session.exec(statement)
        return result.all()

    @require_access_level(AccessLevel.ADMIN)
    async def get_member(self, user_id: UserID) -> User:
        """Retrieve a member of the organization by their user ID.

        Args:
            user_id (UserID): The unique identifier of the user.

        Returns:
            User: The user object representing the member of the organization.

        Raises:
            NoResultFound: If no user with the given ID exists.
        """
        statement = select(User).where(User.id == user_id)
        result = await self.session.exec(statement)
        return result.one()

    @require_access_level(AccessLevel.ADMIN)
    async def delete_member(self, user_id: UserID) -> None:
        """
        Remove a member of the organization.

        This method deletes a specified member from the organization.
        It first checks if the member is a superuser and raises an
        authorization error if so, as superusers cannot be deleted.

        Args:
            user_id (UserID): The unique identifier of the user to be removed.

        Raises:
            TracecatAuthorizationError: If the user is a superuser and cannot be deleted.
        """
        user = await self.get_member(user_id)
        if user.is_superuser:
            raise TracecatAuthorizationError("Cannot delete superuser")
        async with self._manager() as user_manager:
            await user_manager.delete(user)

    @require_access_level(AccessLevel.ADMIN)
    async def update_member(self, user_id: UserID, params: UserUpdate) -> User:
        """
        Update a member of the organization.

        This method updates the details of a specified member within the organization.
        It checks if the member is a superuser and raises an authorization error if so.

        Args:
            user_id (UserID): The unique identifier of the user to be updated.
            params (UserUpdate): The parameters containing the updated user information.

        Returns:
            User: The updated user object representing the member of the organization.

        Raises:
            TracecatAuthorizationError: If the user is a superuser and cannot be updated.
        """
        user = await self.get_member(user_id)
        if user.is_superuser:
            raise TracecatAuthorizationError("Cannot update superuser")
        async with self._manager() as user_manager:
            updated_user = await user_manager.update(
                user_update=params, user=user, safe=True
            )
        return updated_user

    # === Manage settings ===

    @require_access_level(AccessLevel.ADMIN)
    async def get_settings(self) -> dict[str, str]:
        """Get the organization settings."""
        raise NotImplementedError
