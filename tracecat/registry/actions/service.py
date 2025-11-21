from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import Sequence

from pydantic import UUID4, ValidationError
from pydantic_core import ErrorDetails, to_jsonable_python
from sqlalchemy import Boolean, cast, func, or_, select
from tracecat_registry import RegistrySecretType, RegistrySecretTypeValidator

from tracecat import config
from tracecat.db.models import RegistryAction, RegistryRepository
from tracecat.exceptions import (
    RegistryActionValidationError,
    RegistryError,
    RegistryValidationError,
)
from tracecat.expressions.eval import extract_expressions
from tracecat.expressions.validator.validator import (
    TemplateActionExprValidator,
    TemplateActionValidationContext,
)
from tracecat.logger import logger
from tracecat.registry.actions.enums import (
    TemplateActionValidationErrorType,
)
from tracecat.registry.actions.schemas import (
    BoundRegistryAction,
    RegistryActionCreate,
    RegistryActionImplValidator,
    RegistryActionRead,
    RegistryActionUpdate,
    RegistryActionValidationErrorInfo,
    model_converters,
)
from tracecat.registry.constants import DEFAULT_REGISTRY_ORIGIN
from tracecat.registry.index import RegistryIndex
from tracecat.registry.loaders import LoaderMode, get_bound_action_impl
from tracecat.registry.repository import Repository
from tracecat.service import BaseService
from tracecat.settings.service import get_setting_cached


class RegistryActionsService(BaseService):
    """Registry actions service."""

    service_name = "registry_actions"
    # Runtime registry indexes keyed by repository ID (or 'builtin' for the core bundle).
    _runtime_indexes: dict[str, RegistryIndex] = {}
    _runtime_index_lock = asyncio.Lock()

    @classmethod
    async def _ensure_builtin_index(cls, role) -> RegistryIndex:
        """Lazily build the core registry index for runtime execution."""
        async with cls._runtime_index_lock:
            if builtin := cls._runtime_indexes.get("builtin"):
                return builtin

            repo = Repository(origin=DEFAULT_REGISTRY_ORIGIN, role=role)
            await repo.load_from_origin()
            if not repo.index:
                raise RegistryError("Registry index was not initialized")

            cls._runtime_indexes["builtin"] = repo.index
            return repo.index

    @classmethod
    def _register_runtime_index(cls, key: str, index: RegistryIndex) -> None:
        """Cache a repository index for runtime lookups (per process)."""
        cls._runtime_indexes[key] = index

    @classmethod
    async def _get_loader_from_runtime_indexes(
        cls, action_name: str, role
    ) -> tuple[RegistryIndex | None, BoundRegistryAction | None]:
        """Find a bound loader from any cached runtime index."""
        if not cls._runtime_indexes or "builtin" not in cls._runtime_indexes:
            await cls._ensure_builtin_index(role)

        for index in cls._runtime_indexes.values():
            if action_name in index:
                return index, index.get_loader(action_name)

        return None, None

    @staticmethod
    def _collect_loader_secrets(
        loader: BoundRegistryAction, index: RegistryIndex
    ) -> set[RegistrySecretType]:
        """Recursively collect secrets from a bound loader (uses runtime index)."""
        secrets: set[RegistrySecretType] = set(loader.secrets or [])

        if loader.is_template and loader.template_action:
            defn = loader.template_action.definition
            if defn.secrets:
                secrets.update(defn.secrets)
            for step in defn.steps:
                try:
                    step_loader = index.get_loader(step.action)
                except RegistryError:
                    logger.warning(
                        "Step action not found in runtime index",
                        step_action=step.action,
                        parent_action=loader.action,
                    )
                    continue
                secrets.update(
                    RegistryActionsService._collect_loader_secrets(step_loader, index)
                )

        return secrets

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

        result = await self.session.execute(statement)
        return result.scalars().all()

    async def get_action(self, action_name: str) -> RegistryAction:
        """Get an action by name."""
        try:
            namespace, name = action_name.rsplit(".", maxsplit=1)
        except ValueError:
            raise RegistryError(
                f"Action {action_name} is not a valid action name",
                detail={"action_name": action_name},
            ) from None

        statement = select(RegistryAction).where(
            RegistryAction.owner_id == config.TRACECAT__DEFAULT_ORG_ID,
            RegistryAction.namespace == namespace,
            RegistryAction.name == name,
        )
        result = await self.session.execute(statement)
        action = result.scalars().one_or_none()
        if not action:
            raise RegistryError(f"Action {namespace}.{name} not found in the registry")
        return action

    async def get_actions(self, action_names: list[str]) -> Sequence[RegistryAction]:
        """Get actions by name."""
        statement = select(RegistryAction).where(
            RegistryAction.owner_id == config.TRACECAT__DEFAULT_ORG_ID,
            func.concat(RegistryAction.namespace, ".", RegistryAction.name).in_(
                action_names
            ),
        )
        result = await self.session.execute(statement)
        return result.scalars().all()

    async def create_action(
        self,
        params: RegistryActionCreate,
        owner_id: UUID4 = config.TRACECAT__DEFAULT_ORG_ID,
        *,
        commit: bool = True,
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
        if commit:
            await self.session.commit()
        else:
            await self.session.flush()
        return action

    async def update_action(
        self,
        action: RegistryAction,
        params: RegistryActionUpdate,
        *,
        commit: bool = True,
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
        if commit:
            await self.session.commit()
        else:
            await self.session.flush()
        return action

    async def delete_action(
        self, action: RegistryAction, *, commit: bool = True
    ) -> RegistryAction:
        """
        Delete a registry action.

        Args:
            template (DBRegistryAction): The registry action to delete.

        Returns:
            DBRegistryAction: The deleted registry action.
        """
        await self.session.delete(action)
        if commit:
            await self.session.commit()
        else:
            await self.session.flush()
        return action

    async def sync_actions_from_repository(
        self,
        db_repo: RegistryRepository,
        pull_remote: bool = True,
        target_commit_sha: str | None = None,
        *,
        allow_delete_all: bool = False,
    ) -> str | None:
        """Sync actions from a repository.

        To sync actions from the db repositories:
        - For each repository, we need to reimport the packages to run decorators. (for remote this involves pulling)
        - Scan the repositories for implementation details/metadata and update the DB
        """
        # (1) Update the API's view of the repository
        repo = Repository(origin=db_repo.origin, role=self.role)
        repo.repository_id = db_repo.id
        # Load the repository
        # Determine which commit SHA to use:
        # 1. If target_commit_sha is provided, use it
        # 2. If pull_remote is False, use the stored commit SHA
        # 3. Otherwise use None (HEAD)
        if target_commit_sha is not None:
            sha = target_commit_sha
        elif not pull_remote:
            sha = db_repo.commit_sha
        else:
            sha = None
        commit_sha = await repo.load_from_origin(commit_sha=sha)

        # TODO: Move this into it's own function and service it from the registry repository router
        # (1.5) Validate all actions
        # (A) Validate that all actions and steps are valid
        # (B) Validate that each step is correctly formatted
        # - This means taking the step action and looking up the interface
        # - Then we validate the args against the interface
        # (C) Validate that template aciton name doesn't conflict with another action? Doesn't seem like we need this
        # (D) Validate expressions in the args
        should_validate = await get_setting_cached(
            "app_registry_validation_enabled",
            default=False,
        )
        self.logger.info("Registry validation enabled", enabled=should_validate)
        if should_validate:
            if not repo.index:
                raise RegistryError("Registry index was not initialized")

            self.logger.info(
                "Validating actions",
                all_actions=[entry.action_id for entry in repo.index.iter_entries()],
            )
            val_errs: dict[str, list[RegistryActionValidationErrorInfo]] = defaultdict(
                list
            )
            for action in repo.index.iter_loaders():
                if not action.is_template:
                    continue
                if errs := await self.validate_action_template(action, repo):
                    val_errs[action.action].extend(errs)
            if val_errs:
                raise RegistryActionValidationError(
                    f"Found {sum(len(v) for v in val_errs.values())} validation error(s)",
                    detail=val_errs,
                )

        # Perform DB mutations in a single transaction to avoid partial writes
        if self.session.in_transaction():
            async with self.session.begin_nested():
                await self.upsert_actions_from_repo(
                    repo, db_repo, commit=False, allow_delete_all=allow_delete_all
                )
        else:
            async with self.session.begin():
                await self.upsert_actions_from_repo(
                    repo, db_repo, commit=False, allow_delete_all=allow_delete_all
                )

        # Cache the runtime index for execution (per-process in-memory).
        if repo.index:
            self._register_runtime_index(str(db_repo.id), repo.index)

        return commit_sha

    async def get_action_or_none(self, action_name: str) -> RegistryAction | None:
        """Get an action by name, returning None if it doesn't exist."""
        try:
            return await self.get_action(action_name)
        except RegistryError:
            return None

    async def validate_action_template(
        self, action: BoundRegistryAction, repo: Repository
    ) -> list[RegistryActionValidationErrorInfo]:
        """Validate that a template action is correctly formatted."""
        return await validate_action_template(
            action, repo, check_db=True, ra_service=self
        )

    # We need to call this first for UDFs
    async def upsert_actions_from_repo(
        self,
        repo: Repository,
        db_repo: RegistryRepository,
        *,
        commit: bool = True,
        allow_delete_all: bool = False,
    ) -> None:
        """Upsert a list of actions."""
        # (2) Handle DB bookkeeping for the API's view of the repository
        # Perform diffing here. The expectation for this endpoint is to sync Tracecat's view of
        # the repository with the remote repository -- meaning any creation/updates/deletions to
        # actions should be propogated to the db.
        # Safety: We're in a db session so we can call this
        await self.session.refresh(db_repo)
        if not repo.index:
            raise RegistryError("Registry index was not initialized")

        db_actions = db_repo.actions
        db_actions_map = {db_action.action: db_action for db_action in db_actions}
        specs = list(repo.index.iter_specs())
        self.logger.info(
            "Syncing actions from repository",
            repository=db_repo.origin,
            incoming_actions=len(specs),
            existing_actions=len(db_actions_map.keys()),
        )

        if not repo.store:
            if db_actions_map and not allow_delete_all:
                self.logger.error(
                    "Empty registry snapshot; refusing to delete existing actions",
                    repository=db_repo.origin,
                    existing_actions=len(db_actions_map),
                )
                raise RegistryError(
                    "Sync aborted: repository produced no actions; existing actions were preserved."
                )

            if not db_actions_map:
                self.logger.info(
                    "No actions found in repository and none in database; nothing to sync",
                    repository=db_repo.origin,
                )
                return

        n_created = 0
        n_updated = 0
        n_deleted = 0
        for spec in specs:
            try:
                registry_action = await self.get_action(action_name=spec.action_id)
            except RegistryError:
                self.logger.debug(
                    "Action not found, creating",
                    namespace=spec.namespace,
                    origin=spec.origin,
                    repository_id=db_repo.id,
                )
                create_params = spec.to_create_params()
                await self.create_action(create_params, commit=commit)
                n_created += 1
            else:
                self.logger.debug(
                    "Action found, updating",
                    namespace=spec.namespace,
                    origin=spec.origin,
                    repository_id=db_repo.id,
                )
                update_params = spec.to_update_params()
                await self.update_action(registry_action, update_params, commit=commit)
                n_updated += 1
            finally:
                # Mark action as not to delete
                db_actions_map.pop(spec.action_id, None)

        # Remove actions that are marked for deletion
        if db_actions_map:
            self.logger.warning(
                "Removing actions that are no longer in the repository",
                actions=db_actions_map.keys(),
            )
            for action_to_remove in db_actions_map.values():
                await self.delete_action(action_to_remove, commit=commit)
                n_deleted += 1

        self.logger.info(
            "Synced actions from repository",
            repository=db_repo.origin,
            created=n_created,
            updated=n_updated,
            deleted=n_deleted,
        )

    async def load_action_impl(
        self, action_name: str, mode: LoaderMode = "validation"
    ) -> BoundRegistryAction:
        """
        Load the implementation for a registry action.
        """
        # Prefer in-memory runtime indexes (source of truth).
        index, loader = await self._get_loader_from_runtime_indexes(
            action_name, self.role
        )
        if loader:
            return loader

        action = await self.get_action(action_name=action_name)
        bound_action = get_bound_action_impl(action, mode=mode)
        return bound_action

    async def load_action_for_execution(
        self, action_name: str
    ) -> tuple[BoundRegistryAction, set[RegistrySecretType]]:
        """Return a bound action and its secrets, preferring the runtime index."""
        index, loader = await self._get_loader_from_runtime_indexes(
            action_name, self.role
        )
        if loader and index:
            secrets = self._collect_loader_secrets(loader, index)
            return loader, secrets

        action = await self.get_action(action_name=action_name)
        secrets = await self.fetch_all_action_secrets(action)
        return self.get_bound(action, mode="execution"), secrets

    async def read_action_with_implicit_secrets(
        self, action: RegistryAction
    ) -> RegistryActionRead:
        extra_secrets = await self.fetch_all_action_secrets(action)
        return RegistryActionRead.from_database(action, list(extra_secrets))

    async def fetch_all_action_secrets_from_index(
        self, action_name: str
    ) -> set[RegistrySecretType]:
        """Collect secrets for an action using the runtime index if available."""
        index, loader = await self._get_loader_from_runtime_indexes(
            action_name, self.role
        )
        if not loader or not index:
            return set()
        return self._collect_loader_secrets(loader, index)

    async def fetch_all_action_secrets(
        self, action: RegistryAction
    ) -> set[RegistrySecretType]:
        """Recursively fetch all secrets from the action and its template steps.

        Args:
            action: The registry action to fetch secrets from

        Returns:
            set[RegistrySecret]: A set of secret names used by the action and its template steps
        """
        secrets = set()
        impl = RegistryActionImplValidator.validate_python(action.implementation)
        if impl.type == "udf":
            if action.secrets:
                secrets.update(
                    RegistrySecretTypeValidator.validate_python(secret)
                    for secret in action.secrets
                )
        elif impl.type == "template":
            ta = impl.template_action
            if ta is None:
                raise ValueError("Template action is not defined")
            # Add secrets from the template action itself
            if template_secrets := ta.definition.secrets:
                secrets.update(template_secrets)
            # Recursively fetch secrets from each step
            step_action_names = [step.action for step in ta.definition.steps]
            step_ras = await self.get_actions(step_action_names)
            for step_ra in step_ras:
                step_secrets = await self.fetch_all_action_secrets(step_ra)
                secrets.update(step_secrets)
        return secrets

    def get_bound(
        self,
        action: RegistryAction,
        mode: LoaderMode = "execution",
    ) -> BoundRegistryAction:
        """Get the bound action for a registry action."""
        return get_bound_action_impl(action, mode=mode)


def error_details_to_message(err: ErrorDetails) -> str:
    loc = err["loc"]
    if isinstance(loc, tuple):
        loc = ", ".join(f"'{i}'" for i in loc)
    match err.get("type"):
        case "missing":
            msg = f"Missing required field(s): {loc}"
        case "extra_forbidden":
            msg = f"Got unexpected field(s): {loc}"
        case _:
            msg = f"{err['msg']}: {loc}"
    return msg


async def validate_action_template(
    action: BoundRegistryAction,
    repo: Repository,
    *,
    check_db: bool = False,
    ra_service: RegistryActionsService | None = None,
) -> list[RegistryActionValidationErrorInfo]:
    """Validate that a template action is correctly formatted."""
    if not (action.is_template and action.template_action):
        return []
    if check_db and not ra_service:
        raise ValueError("RegistryActionsService is required if check_db is True")
    val_errs: list[RegistryActionValidationErrorInfo] = []
    log = ra_service.logger if ra_service else logger

    defn = action.template_action.definition
    # 1. Validate template steps
    for step in defn.steps:
        # (A) Ensure that the step action type exists
        if repo.index and step.action in repo.index:
            bound_action = repo.index.get_loader(step.action)
        elif step.action in repo.store:
            # If this action is already in the repo, we can just use it
            # We will overwrite the action in the DB anyways
            bound_action = repo.store[step.action]
        elif (
            check_db
            and ra_service
            and (reg_action := await ra_service.get_action_or_none(step.action))
            is not None
        ):
            bound_action = get_bound_action_impl(reg_action, mode="validation")
        else:
            # Action not found in the repo or DB
            val_errs.append(
                RegistryActionValidationErrorInfo(
                    loc_primary=f"steps.{step.ref}",
                    loc_secondary=step.action,
                    type=TemplateActionValidationErrorType.ACTION_NOT_FOUND,
                    details=[f"Action `{step.action}` not found in repository."],
                    is_template=action.is_template,
                )
            )
            log.warning(
                "Step action not found, skipping",
                step_ref=step.ref,
                step_action=step.action,
            )
            continue

        # (B) Validate that the step is correctly formatted
        try:
            bound_action.validate_args(args=step.args)
        except RegistryValidationError as e:
            if isinstance(e.err, ValidationError):
                details = []
                for err in e.err.errors():
                    msg = error_details_to_message(err)
                    details.append(msg)
            else:
                details = [str(e.err)] if e.err else []
            val_errs.append(
                RegistryActionValidationErrorInfo(
                    loc_primary=f"steps.{step.ref}",
                    loc_secondary=step.action,
                    type=TemplateActionValidationErrorType.STEP_VALIDATION_ERROR,
                    details=details,
                    is_template=action.is_template,
                )
            )
    # 2. Validate expressions
    validator = TemplateActionExprValidator(
        validation_context=TemplateActionValidationContext(
            expects=defn.expects,
            step_refs={step.ref for step in defn.steps},
        ),
    )
    for step in defn.steps:
        for field, value in step.args.items():
            for expr in extract_expressions(value):
                expr.validate(validator, loc=("steps", step.ref, "args", field))
    for expr in extract_expressions(defn.returns):
        expr.validate(validator, loc=("returns",))
    expr_errs = set(validator.errors())
    if expr_errs:
        log.warning("Expression validation errors", errors=expr_errs)
    val_errs.extend(
        RegistryActionValidationErrorInfo.from_validation_result(
            e, is_template=action.is_template
        )
        for e in expr_errs
    )

    return val_errs
