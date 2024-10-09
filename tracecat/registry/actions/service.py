from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from pydantic import UUID4
from sqlalchemy import Boolean
from sqlmodel import cast, func, or_, select
from sqlmodel.ext.asyncio.session import AsyncSession

from tracecat import config
from tracecat.contexts import ctx_role
from tracecat.db.engine import get_async_session_context_manager
from tracecat.db.schemas import RegistryAction, RegistryRepository
from tracecat.logger import logger
from tracecat.registry.actions.models import (
    BoundRegistryAction,
    RegistryActionCreate,
    RegistryActionUpdate,
    model_converters,
)
from tracecat.registry.loaders import get_bound_action_impl
from tracecat.registry.repository import Repository
from tracecat.types.auth import Role
from tracecat.types.exceptions import RegistryError


class RegistryActionsService:
    """Registry actions service."""

    def __init__(self, session: AsyncSession, role: Role | None = None):
        self.role = role or ctx_role.get()
        self.session = session
        self.logger = logger.bind(service="registry_actions")

    @asynccontextmanager
    @staticmethod
    async def with_session(
        role: Role | None = None,
    ) -> AsyncGenerator[RegistryActionsService, None]:
        async with get_async_session_context_manager() as session:
            yield RegistryActionsService(session, role=role)

    async def list_actions(
        self,
        *,
        versions: list[str] | None = None,
        namespace: str | None = None,
        include_marked: bool = False,
        include_keys: set[str] | None = None,
    ) -> list[RegistryAction]:
        statement = select(RegistryAction)

        if not include_marked:
            statement = statement.where(
                cast(RegistryAction.options["include_in_schema"].astext, Boolean)  # noqa: E712
                == True
            )

        if versions:
            statement = statement.where(
                RegistryAction.version.in_(versions),
            )

        if namespace:
            statement = statement.where(
                RegistryAction.namespace.startswith(namespace),
            )

        if include_keys:
            statement = statement.where(
                or_(
                    func.concat(RegistryAction.namespace, ".", RegistryAction.name).in_(
                        include_keys
                    )
                )
            )

        result = await self.session.exec(statement)
        return result.all()

    async def get_action(self, *, version: str, action_name: str) -> RegistryAction:
        namespace, name = action_name.rsplit(".", maxsplit=1)
        statement = select(RegistryAction).where(
            RegistryAction.owner_id == config.TRACECAT__DEFAULT_ORG_ID,
            RegistryAction.namespace == namespace,
            RegistryAction.name == name,
            RegistryAction.version == version,
        )
        result = await self.session.exec(statement)
        action = result.one_or_none()
        if not action:
            raise RegistryError(
                f"Action {namespace}.{name} not found in registry {version}"
            )
        return action

    async def create_action(
        self,
        params: RegistryActionCreate,
        owner_id: UUID4 = config.TRACECAT__DEFAULT_ORG_ID,
    ) -> RegistryAction:
        """
        Create a new registry action.

        Args:
            params (RegistryActionCreate): Parameters for creating the action.

        Returns:
            DBRegistryAction: The created registry action.
        """

        if params.implementation.type == "template":
            interface = model_converters.implementation_to_interface(
                params.implementation
            )
        else:
            interface = params.interface

        action = RegistryAction(
            owner_id=owner_id,
            interface=interface,
            **params.model_dump(exclude={"interface"}),
        )

        self.session.add(action)
        await self.session.commit()
        return action

    async def update_action(
        self,
        action: RegistryAction,
        params: RegistryActionUpdate,
    ) -> RegistryAction:
        """
        Update an existing registry action.

        Args:
            db_template (DBRegistryAction): The existing registry action to update.
            params (RegistryActionUpdate): Parameters for updating the action.

        Returns:
            DBRegistryAction: The updated registry action.
        """
        set_fields = params.model_dump(exclude_unset=True)
        for key, value in set_fields.items():
            setattr(action, key, value)
        self.session.add(action)
        await self.session.commit()
        return action

    async def delete_action(self, action: RegistryAction) -> RegistryAction:
        """
        Delete a registry action.

        Args:
            template (DBRegistryAction): The registry action to delete.

        Returns:
            DBRegistryAction: The deleted registry action.
        """
        await self.session.delete(action)
        await self.session.commit()
        return action

    async def sync_actions(self, repos: list[RegistryRepository]) -> None:
        """
        Update the RegistryAction table with the actions from a list of repositories.

        Steps:
        1. Load actions from a list of repositories
        2. Update the database with the new actions
        3. Update the registry manager with the new actions
        """

        # For each repo, load from origin
        self.logger.info("Syncing actions from repositories", repos=repos)
        for repo in repos:
            await self.sync_actions_from_repository(repo)

    async def sync_actions_from_repository(
        self, repository: RegistryRepository
    ) -> None:
        """Sync actions from a repository.

        To sync actions from the db repositories:
        - For each repository, we need to reimport the packages to run decorators. (for remote this involves pulling)
        - Scan the repositories for implementation details/metadata and update the DB
        """
        repo = Repository(version=repository.version, origin=repository.origin)
        try:
            await repo.load_from_origin()
        except Exception as e:
            logger.error(f"Error while loading registry from origin: {str(e)}")
            raise e
        # Add the loaded actions to the db
        for action in repo.store.values():
            # Check action already exists
            try:
                await self.get_action(version=action.version, action_name=action.action)
            except RegistryError:
                self.logger.info(
                    "Action not found, creating",
                    namespace=action.namespace,
                    name=action.name,
                    version=action.version,
                    origin=action.origin,
                    repository_id=repository.id,
                )
                params = RegistryActionCreate.from_bound(action, repository.id)
                await self.create_action(params)

    async def load_action_impl(
        self, version: str, action_name: str
    ) -> BoundRegistryAction:
        """
        Load the implementation for a registry action.
        """
        action = await self.get_action(version=version, action_name=action_name)
        action = get_bound_action_impl(action)
        return action
