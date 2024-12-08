from __future__ import annotations

from collections.abc import AsyncGenerator, Sequence
from contextlib import asynccontextmanager

from pydantic import UUID4
from pydantic_core import to_jsonable_python
from sqlalchemy import Boolean
from sqlmodel import cast, func, or_, select
from sqlmodel.ext.asyncio.session import AsyncSession
from tracecat_registry import RegistrySecret

from tracecat import config
from tracecat.contexts import ctx_role
from tracecat.db.engine import get_async_session_context_manager
from tracecat.db.schemas import RegistryAction, RegistryRepository
from tracecat.executor.client import ExecutorClient
from tracecat.logger import logger
from tracecat.registry.actions.models import (
    BoundRegistryAction,
    RegistryActionCreate,
    RegistryActionImplValidator,
    RegistryActionRead,
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
        namespace: str | None = None,
        include_marked: bool = False,
        include_keys: set[str] | None = None,
    ) -> Sequence[RegistryAction]:
        statement = select(RegistryAction)

        if not include_marked:
            statement = statement.where(
                cast(RegistryAction.options["include_in_schema"].astext, Boolean)  # noqa: E712
                == True  # noqa: E712
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

    async def get_action(self, *, action_name: str) -> RegistryAction:
        """Get an action by name."""
        namespace, name = action_name.rsplit(".", maxsplit=1)
        statement = select(RegistryAction).where(
            RegistryAction.owner_id == config.TRACECAT__DEFAULT_ORG_ID,
            RegistryAction.namespace == namespace,
            RegistryAction.name == name,
        )
        result = await self.session.exec(statement)
        action = result.one_or_none()
        if not action:
            raise RegistryError(f"Action {namespace}.{name} not found in repository")
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

        # Interface
        if params.implementation.type == "template":
            interface = model_converters.implementation_to_interface(
                params.implementation
            )
        else:
            interface = params.interface

        action = RegistryAction(
            owner_id=owner_id,
            interface=to_jsonable_python(interface),
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

    async def sync_actions_from_repository(
        self, db_repo: RegistryRepository, pull_remote: bool = True
    ) -> str | None:
        """Sync actions from a repository.

        To sync actions from the db repositories:
        - For each repository, we need to reimport the packages to run decorators. (for remote this involves pulling)
        - Scan the repositories for implementation details/metadata and update the DB
        """
        # (1) Update the API's view of the repository
        repo = Repository(origin=db_repo.origin, role=self.role)
        # Load the repository
        # After we sync the repository with its remote
        # None here means we're pulling the remote repository from HEAD
        sha = None if pull_remote else db_repo.commit_sha
        commit_sha = await repo.load_from_origin(commit_sha=sha)

        # (2) Handle DB bookkeeping for the API's view of the repository
        # Perform diffing here. The expectation for this endpoint is to sync Tracecat's view of
        # the repository with the remote repository -- meaning any creation/updates/deletions to
        # actions should be propogated to the db.
        # Safety: We're in a db session so we can call this
        db_actions = db_repo.actions
        db_actions_map = {db_action.action: db_action for db_action in db_actions}

        self.logger.info(
            "Syncing actions from repository",
            repository=db_repo.origin,
            incoming_actions=len(repo.store.keys()),
            existing_actions=len(db_actions_map.keys()),
        )

        n_created = 0
        n_updated = 0
        n_deleted = 0
        for action_name, new_bound_action in repo.store.items():
            try:
                registry_action = await self.get_action(action_name=action_name)
            except RegistryError:
                self.logger.debug(
                    "Action not found, creating",
                    namespace=new_bound_action.namespace,
                    origin=new_bound_action.origin,
                    repository_id=db_repo.id,
                )
                create_params = RegistryActionCreate.from_bound(
                    new_bound_action, db_repo.id
                )
                await self.create_action(create_params)
                n_created += 1
            else:
                self.logger.debug(
                    "Action found, updating",
                    namespace=new_bound_action.namespace,
                    origin=new_bound_action.origin,
                    repository_id=db_repo.id,
                )
                update_params = RegistryActionUpdate.from_bound(new_bound_action)
                await self.update_action(registry_action, update_params)
                n_updated += 1
            finally:
                # Mark action as not to delete
                db_actions_map.pop(action_name, None)

        # Remove actions that are marked for deletion
        if db_actions_map:
            self.logger.warning(
                "Removing actions that are no longer in the repository",
                actions=db_actions_map.keys(),
            )
            for action_to_remove in db_actions_map.values():
                await self.delete_action(action_to_remove)
                n_deleted += 1

        self.logger.info(
            "Synced actions from repository",
            repository=db_repo.origin,
            created=n_created,
            updated=n_updated,
            deleted=n_deleted,
        )

        return commit_sha

    async def load_action_impl(self, action_name: str) -> BoundRegistryAction:
        """
        Load the implementation for a registry action.
        """
        action = await self.get_action(action_name=action_name)
        bound_action = get_bound_action_impl(action)
        return bound_action

    async def get_action_implicit_secrets(
        self, action: RegistryAction
    ) -> list[RegistrySecret]:
        """Extract the implicit secrets from the template action's steps."""
        impl = RegistryActionImplValidator.validate_python(action.implementation)
        if impl.type != "template":
            return []
        implicit_secrets: list[RegistrySecret] = []
        for step in impl.template_action.definition.steps:
            inner_action = await self.get_action(action_name=step.action)
            implicit_secrets.extend(
                RegistrySecret(**secret) for secret in inner_action.secrets or []
            )
        return implicit_secrets

    async def read_action_with_implicit_secrets(
        self, action: RegistryAction
    ) -> RegistryActionRead:
        extra_secrets = await self.get_action_implicit_secrets(action)
        return RegistryActionRead.from_database(action, extra_secrets)
