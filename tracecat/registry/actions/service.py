from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence

from pydantic import UUID4, ValidationError
from pydantic_core import ErrorDetails, to_jsonable_python
from sqlalchemy import Boolean
from sqlmodel import cast, func, or_, select
from tracecat_registry import RegistrySecret

from tracecat import config
from tracecat.db.schemas import RegistryAction, RegistryRepository
from tracecat.expressions.eval import extract_expressions
from tracecat.expressions.parser.validator import (
    TemplateActionExprValidator,
    TemplateActionValidationContext,
)
from tracecat.logger import logger
from tracecat.registry.actions.enums import (
    TemplateActionValidationErrorType,
)
from tracecat.registry.actions.models import (
    BoundRegistryAction,
    RegistryActionCreate,
    RegistryActionImplValidator,
    RegistryActionRead,
    RegistryActionUpdate,
    RegistryActionValidationErrorInfo,
    model_converters,
)
from tracecat.registry.loaders import LoaderMode, get_bound_action_impl
from tracecat.registry.repository import Repository
from tracecat.service import BaseService
from tracecat.settings.service import get_setting_cached
from tracecat.types.exceptions import (
    RegistryActionValidationError,
    RegistryError,
    RegistryValidationError,
)


class RegistryActionsService(BaseService):
    """Registry actions service."""

    service_name = "registry_actions"

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

    async def get_action(self, action_name: str) -> RegistryAction:
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
        result = await self.session.exec(statement)
        return result.all()

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
            self.logger.info("Validating actions", all_actions=repo.store.keys())
            val_errs: dict[str, list[RegistryActionValidationErrorInfo]] = defaultdict(
                list
            )
            for action in repo.store.values():
                if not action.is_template:
                    continue
                if errs := await self.validate_action_template(action, repo):
                    val_errs[action.action].extend(errs)
            if val_errs:
                raise RegistryActionValidationError(
                    f"Found {sum(len(v) for v in val_errs.values())} validation error(s)",
                    detail=val_errs,
                )

        # NOTE: We should start a transaction here and commit it after the sync is complete
        await self.upsert_actions_from_repo(repo, db_repo)

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
        self, repo: Repository, db_repo: RegistryRepository
    ) -> None:
        """Upsert a list of actions."""
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

    async def load_action_impl(
        self, action_name: str, mode: LoaderMode = "validation"
    ) -> BoundRegistryAction:
        """
        Load the implementation for a registry action.
        """
        action = await self.get_action(action_name=action_name)
        bound_action = get_bound_action_impl(action, mode=mode)
        return bound_action

    async def read_action_with_implicit_secrets(
        self, action: RegistryAction
    ) -> RegistryActionRead:
        extra_secrets = await self.fetch_all_action_secrets(action)
        return RegistryActionRead.from_database(action, list(extra_secrets))

    async def fetch_all_action_secrets(
        self, action: RegistryAction
    ) -> set[RegistrySecret]:
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
                secrets.update(RegistrySecret(**secret) for secret in action.secrets)
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
        if step.action in repo.store:
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
            bound_action.validate_args(**step.args)
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
    log.warning("Expression validation errors", errors=expr_errs)
    val_errs.extend(
        RegistryActionValidationErrorInfo.from_validation_result(
            e, is_template=action.is_template
        )
        for e in expr_errs
    )

    return val_errs
