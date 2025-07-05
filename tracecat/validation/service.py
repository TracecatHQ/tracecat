from collections.abc import Mapping
from itertools import chain
from typing import Any

from pydantic import ConfigDict, ValidationError
from sqlalchemy.exc import MultipleResultsFound
from sqlmodel.ext.asyncio.session import AsyncSession
from tracecat_registry import (
    RegistryOAuthSecret,
    RegistrySecret,
    RegistrySecretTypeValidator,
)

from tracecat.concurrency import GatheringTaskGroup
from tracecat.db.engine import get_async_session_context_manager
from tracecat.db.schemas import RegistryAction
from tracecat.dsl.common import DSLInput, ExecuteChildWorkflowArgs
from tracecat.dsl.enums import PlatformAction
from tracecat.ee.interactions.models import ResponseInteraction
from tracecat.expressions.common import ExprType
from tracecat.expressions.eval import extract_expressions, is_template_only
from tracecat.expressions.validator.validator import (
    ExprValidationContext,
    ExprValidator,
)
from tracecat.integrations.enums import OAuthGrantType
from tracecat.integrations.models import ProviderKey
from tracecat.integrations.service import IntegrationService
from tracecat.logger import logger
from tracecat.registry.actions.models import RegistryActionInterface
from tracecat.registry.actions.service import RegistryActionsService
from tracecat.secrets.service import SecretsService
from tracecat.types.exceptions import RegistryValidationError, TracecatNotFoundError
from tracecat.validation.common import json_schema_to_pydantic
from tracecat.validation.models import (
    ActionValidationResult,
    ExprValidationResult,
    SecretValidationDetail,
    SecretValidationResult,
    ValidationDetail,
    ValidationResult,
)

PERMITTED_INTERACTION_ACTIONS = [
    "tools.slack.ask_text_input",
    "tools.slack.lookup_user_by_email",
    "tools.slack.post_notification",
    "tools.slack.post_update",
    "tools.slack.revoke_sessions",
]


async def validate_single_secret(
    secrets_service: SecretsService,
    checked_keys: set[str],
    environment: str,
    action: RegistryAction,
    registry_secret: RegistrySecret,
) -> list[SecretValidationResult]:
    """Validate a single secret against the secrets manager."""
    if registry_secret.name in checked_keys:
        return []

    results: list[SecretValidationResult] = []
    defined_secret = None

    try:
        defined_secret = await secrets_service.get_secret_by_name(
            registry_secret.name, environment=environment
        )
    except TracecatNotFoundError as e:
        # If the secret is required, we fail early
        if not registry_secret.optional:
            secret_repr = f"{registry_secret.name!r} (env: {environment!r})"
            match e.__cause__:
                case MultipleResultsFound():
                    msg = f"Multiple secrets found when searching for secret {secret_repr}."
                case _:
                    msg = f"Secret {secret_repr} is missing in the secrets manager."
            return [
                SecretValidationResult(
                    status="error",
                    msg=msg,
                    detail=SecretValidationDetail(
                        environment=environment,
                        secret_name=registry_secret.name,
                    ),
                )
            ]
        # If the secret is optional, we just log and move on
    finally:
        checked_keys.add(registry_secret.name)

    # At this point we either have an optional secret, or the secret is defined
    # Validate secret keys
    if defined_secret:
        decrypted_keys = secrets_service.decrypt_keys(defined_secret.encrypted_keys)
        defined_keys = {kv.key for kv in decrypted_keys}
        required_keys = frozenset(registry_secret.keys or ())
        optional_keys = frozenset(registry_secret.optional_keys or ())

        final_missing_keys = (required_keys - defined_keys) - optional_keys

        if final_missing_keys:
            results.append(
                SecretValidationResult(
                    status="error",
                    msg=f"Secret {registry_secret.name!r} is missing required keys: {', '.join(final_missing_keys)}",
                )
            )

    return results


async def check_action_secrets(
    secrets_service: SecretsService,
    registry_service: RegistryActionsService,
    checked_keys: set[str],
    environment: str,
    action: RegistryAction,
) -> list[SecretValidationResult]:
    """Check all secrets for a single action."""
    results: list[SecretValidationResult] = []
    secrets = [
        RegistrySecretTypeValidator.validate_python(secret)
        for secret in action.secrets or []
    ]
    implicit_secrets = await registry_service.fetch_all_action_secrets(action)
    secrets.extend(implicit_secrets)

    for registry_secret in secrets:
        if registry_secret.type == "oauth":
            # Workspace integration
            secret_results = await validate_workspace_integration(
                secrets_service.session,
                checked_keys,
                environment,
                registry_secret,
            )
        else:
            # Workspace secret
            secret_results = await validate_single_secret(
                secrets_service,
                checked_keys,
                environment,
                action,
                registry_secret,
            )
        results.extend(secret_results)

    return results


async def validate_workspace_integration(
    session: AsyncSession,
    checked_keys: set[str],
    environment: str,
    registry_secret: RegistryOAuthSecret,
) -> list[SecretValidationResult]:
    """Validate that a workspace has the required OAuth integration.

    Args:
        secrets_service: The secrets service to use for validation.
        checked_keys: Set of keys that have already been checked.
        environment: The environment to validate secrets for.
        action: The registry action that requires the integration.
        registry_secret: The registry secret definition for the OAuth integration.

    Returns:
        A list of validation results.
    """
    results: list[SecretValidationResult] = []

    # Skip if we've already checked this key
    if registry_secret.provider_id in checked_keys:
        return results

    checked_keys.add(registry_secret.provider_id)

    # Get the integration from the workspace
    key = ProviderKey(
        id=registry_secret.provider_id,
        grant_type=OAuthGrantType(registry_secret.grant_type),
    )
    svc = IntegrationService(session)
    integration = await svc.get_integration(provider_key=key)

    if not integration:
        results.append(
            SecretValidationResult(
                status="error",
                msg=f"Required OAuth integration {registry_secret.provider_id!r} is not configured",
            )
        )

    return results


async def validate_actions_have_defined_secrets(
    dsl: DSLInput,
) -> list[SecretValidationResult]:
    """Validate that all actions in the DSL have their required secrets defined."""
    checked_keys: set[str] = set()

    async with get_async_session_context_manager() as session:
        secrets_service = SecretsService(session)

        # Get all actions that need validation
        action_keys = {a.action for a in dsl.actions}
        registry_service = RegistryActionsService(session)
        # For all actions, pull out all the secrets that are used
        actions = await registry_service.list_actions(include_keys=action_keys)

        # Validate all actions concurrently
        async with GatheringTaskGroup() as tg:
            for action in actions:
                tg.create_task(
                    check_action_secrets(
                        secrets_service,
                        registry_service,
                        checked_keys,
                        dsl.config.environment,
                        action,
                    )
                )

        return list(chain.from_iterable(tg.results()))


async def validate_registry_action_args(
    *,
    session: AsyncSession,
    action_name: str,
    action_ref: str,
    args: Mapping[str, Any],
) -> ActionValidationResult:
    """Validate arguments against a UDF spec."""
    # 1. read the schema from the db
    # 2. construct a pydantic model from the schema
    # 3. validate the args against the pydantic model
    try:
        try:
            if action_name == PlatformAction.CHILD_WORKFLOW_EXECUTE:
                validated = ExecuteChildWorkflowArgs.model_validate(args)
            else:
                service = RegistryActionsService(session)
                action = await service.get_action(action_name=action_name)
                interface = RegistryActionInterface(**action.interface)
                model = json_schema_to_pydantic(
                    interface["expects"], root_config=ConfigDict(extra="forbid")
                )
                # Note that we're allowing type coercion for the input arguments
                # Use cases would be transforming a UTC string to a datetime object
                # We return the validated input arguments as a dictionary
                validated = model.model_validate(args)
            validated_args = validated.model_dump()
        except ValidationError as e:
            logger.info("Validation error for action", action_name=action_name)
            raise RegistryValidationError(
                f"Validation error for action {action_name!r}. {e.errors()!r}",
                key=action_name,
                err=e,
            ) from e
        except Exception as e:
            raise RegistryValidationError(
                f"Unexpected error when validating input arguments for action {action_name!r}. {e}",
                key=action_name,
            ) from e

        return ActionValidationResult(
            status="success",
            msg="Arguments are valid.",
            validated_args=validated_args,
            ref=action_ref,
            action_type=action_name,
        )
    except RegistryValidationError as e:
        if isinstance(e.err, ValidationError):
            detail = ValidationDetail.list_from_pydantic(e.err)
        else:
            detail = [ValidationDetail(type="general", msg=str(e.err))]
        logger.info(
            "Error validating action args",
            action_name=action_name,
            error=e,
            detail=detail,
        )
        return ActionValidationResult(
            status="error",
            msg=f"Error validating action {action_name}",
            detail=detail,
            ref=action_ref,
            action_type=action_name,
        )
    except KeyError:
        return ActionValidationResult(
            status="error",
            msg=f"Could not find action {action_name!r} in registry. Is this action registered?",
            ref=action_ref,
            action_type=action_name,
        )


async def validate_dsl_actions(
    *,
    session: AsyncSession,
    dsl: DSLInput,
) -> list[ActionValidationResult]:
    """Validate arguemnts to the DSLInput.

    Check if the input arguemnts are either a templated expression or the correct type.
    """
    val_res: list[ActionValidationResult] = []
    # Validate the actions
    for act_stmt in dsl.actions:
        details: list[ValidationDetail] = []
        # We validate the action args, but keep them as is
        # These will be coerced properly when the workflow is run
        # We store the DSL as is to ensure compatibility with with string reprs
        result = await validate_registry_action_args(
            session=session,
            action_name=act_stmt.action,
            args=act_stmt.args,
            action_ref=act_stmt.ref,
        )
        if result.status == "error" and result.detail:
            details.extend(result.detail)
        # Validate `run_if`
        if act_stmt.run_if and not is_template_only(act_stmt.run_if):
            details.append(
                ValidationDetail(
                    type="action",
                    msg=f"`run_if` must only contain an expression. Got {act_stmt.run_if!r}.",
                    loc=(act_stmt.ref, "run_if"),
                )
            )
        # Validate `for_each`
        # Check that it's an expr or a list of exprs, and that
        match act_stmt.for_each:
            case str():
                if not is_template_only(act_stmt.for_each):
                    details.append(
                        ValidationDetail(
                            type="action",
                            msg=f"`for_each` must be an expression or list of expressions. Got {act_stmt.for_each!r}.",
                            loc=(act_stmt.ref, "for_each"),
                        )
                    )
            case list():
                for expr in act_stmt.for_each:
                    if not is_template_only(expr) or not isinstance(expr, str):
                        details.append(
                            ValidationDetail(
                                type="action",
                                msg=f"`for_each` must be an expression or list of expressions. Got {act_stmt.for_each!r}.",
                                loc=(act_stmt.ref, "for_each"),
                            )
                        )
            case None:
                pass
            case _:
                details.append(
                    ValidationDetail(
                        type="action",
                        msg=f"Invalid `for_each` of type {type(act_stmt.for_each)}.",
                        loc=(act_stmt.ref, "for_each"),
                    )
                )
        # Validate `interaction`
        match act_stmt.interaction:
            case ResponseInteraction():
                if act_stmt.action not in PERMITTED_INTERACTION_ACTIONS:
                    details.append(
                        ValidationDetail(
                            type="action",
                            msg=f"Response interactions are only supported for the following actions:\n"
                            f"{('\n'.join(f'- {x}' for x in PERMITTED_INTERACTION_ACTIONS))}\n",
                            loc=(act_stmt.ref, "interaction"),
                        )
                    )
            case None:
                pass
            case _:
                details.append(
                    ValidationDetail(
                        type="action",
                        msg=f"Unsupported `interaction` of type {type(act_stmt.interaction)}.",
                        loc=(act_stmt.ref, "interaction"),
                    )
                )
        if details:
            val_res.append(
                ActionValidationResult(
                    status="error",
                    msg=result.msg,
                    ref=act_stmt.ref,
                    detail=details,
                    action_type=act_stmt.action,
                )
            )
    # Validate `returns`
    return val_res


async def validate_dsl_expressions(
    dsl: DSLInput,
    *,
    exclude: set[ExprType] | None = None,
) -> list[ExprValidationResult]:
    """Validate the DSL expressions at commit time."""
    validation_context = ExprValidationContext(
        action_refs={a.ref for a in dsl.actions},
        inputs_context=dsl.inputs,
    )

    results: list[ExprValidationResult] = []
    for act_stmt in dsl.actions:
        async with ExprValidator(
            validation_context=validation_context,
            environment=dsl.config.environment,
        ) as visitor:
            # Validate action args
            for expr in extract_expressions(act_stmt.args):
                expr.validate(
                    visitor,
                    loc=(act_stmt.ref, "inputs"),
                    exclude=exclude,
                    ref=act_stmt.ref,
                )

            # Validate `run_if`
            if act_stmt.run_if:
                # At this point the structure should be correct
                for expr in extract_expressions(act_stmt.run_if):
                    expr.validate(
                        visitor,
                        loc=(act_stmt.ref, "run_if"),
                        exclude=exclude,
                        ref=act_stmt.ref,
                    )

            # Validate `for_each`
            if act_stmt.for_each:
                stmts = act_stmt.for_each
                if isinstance(act_stmt.for_each, str):
                    stmts = [act_stmt.for_each]
                for for_each_stmt in stmts:
                    for expr in extract_expressions(for_each_stmt):
                        expr.validate(
                            visitor,
                            loc=(act_stmt.ref, "for_each"),
                            exclude=exclude,
                            ref=act_stmt.ref,
                        )
        if details := visitor.results():
            results.append(
                ExprValidationResult(
                    status="error",
                    msg=f"Found {len(details)} expression errors",
                    detail=details,
                    ref=act_stmt.ref,
                    expression_type=ExprType.GENERIC,
                )
            )

    return results


async def validate_dsl(
    session: AsyncSession,
    dsl: DSLInput,
    *,
    validate_args: bool = True,
    validate_expressions: bool = True,
    validate_secrets: bool = True,
    exclude_exprs: set[ExprType] | None = None,
) -> set[ValidationResult]:
    """Validate the DSL at commit time.

    This function calls and combines all results from each validation tier.
    """
    if not any((validate_args, validate_expressions, validate_secrets)):
        return set()

    iterables: list[ValidationResult] = []

    # Tier 2: Action Args validation
    if validate_args:
        dsl_args_errs = await validate_dsl_actions(session=session, dsl=dsl)
        logger.debug(
            f"{len(dsl_args_errs)} DSL args validation errors", errs=dsl_args_errs
        )
        iterables.extend(ValidationResult.new(err) for err in dsl_args_errs)

    # Tier 3: Expression validation
    # When we reach this point, the inputs have been validated properly (ignoring templated expressions)
    # We now have to validate that the expressions are valid
    # 1. Find all expressions in the inputs
    # 2. For each expression context, cross-reference the expressions API and udf registry

    if validate_expressions:
        expr_errs = await validate_dsl_expressions(dsl, exclude=exclude_exprs)
        logger.debug(
            f"{len(expr_errs)} DSL expression validation errors", errs=expr_errs
        )
        iterables.extend(ValidationResult.new(err) for err in expr_errs)

    # For secrets we also need to check if any used actions have undefined secrets
    if validate_secrets:
        udf_missing_secrets = await validate_actions_have_defined_secrets(dsl)
        logger.debug(
            f"{len(udf_missing_secrets)} DSL secret validation errors",
            errs=udf_missing_secrets,
        )
        iterables.extend(ValidationResult.new(err) for err in udf_missing_secrets)

    return set(iterables)
