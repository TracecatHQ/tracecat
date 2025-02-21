from collections.abc import Mapping, Sequence
from itertools import chain
from typing import Any

from pydantic import ValidationError
from sqlalchemy.exc import MultipleResultsFound
from sqlmodel.ext.asyncio.session import AsyncSession
from tracecat_registry import RegistrySecret

from tracecat.concurrency import GatheringTaskGroup
from tracecat.db.engine import get_async_session_context_manager
from tracecat.db.schemas import RegistryAction
from tracecat.dsl.common import (
    DSLInput,
    ExecuteChildWorkflowArgs,
    RunTableLookupArgs,
    context_locator,
)
from tracecat.dsl.enums import CoreActions
from tracecat.expressions.common import ExprType
from tracecat.expressions.eval import extract_expressions, is_template_only
from tracecat.expressions.parser.validator import ExprValidationContext, ExprValidator
from tracecat.logger import logger
from tracecat.registry.actions.models import RegistryActionInterface
from tracecat.registry.actions.service import RegistryActionsService
from tracecat.secrets.service import SecretsService
from tracecat.types.exceptions import RegistryValidationError, TracecatNotFoundError
from tracecat.validation.common import json_schema_to_pydantic, secret_validator
from tracecat.validation.models import (
    ExprValidationResult,
    RegistryValidationResult,
    SecretValidationResult,
    ValidationResult,
)


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
                    msg=f"[{action.action}]\n\n{msg}",
                    detail={
                        "environment": environment,
                        "secret_name": registry_secret.name,
                    },
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
    secrets = [RegistrySecret(**secret) for secret in action.secrets or []]
    implicit_secrets = await registry_service.fetch_all_action_secrets(action)
    secrets.extend(implicit_secrets)

    for registry_secret in secrets:
        secret_results = await validate_single_secret(
            secrets_service,
            checked_keys,
            environment,
            action,
            registry_secret,
        )
        results.extend(secret_results)

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
    args: Mapping[str, Any],
    ref: str | None = None,
) -> RegistryValidationResult:
    """Validate arguments against a UDF spec."""
    # 1. read the schema from the db
    # 2. construct a pydantic model from the schema
    # 3. validate the args against the pydantic model
    try:
        try:
            if action_name == CoreActions.CHILD_WORKFLOW_EXECUTE:
                validated = ExecuteChildWorkflowArgs.model_validate(args)
            elif action_name == CoreActions.TABLE_LOOKUP:
                validated = RunTableLookupArgs.model_validate(args)
            else:
                service = RegistryActionsService(session)
                action = await service.get_action(action_name=action_name)
                interface = RegistryActionInterface(**action.interface)
                model = json_schema_to_pydantic(interface["expects"])
                # Note that we're allowing type coercion for the input arguments
                # Use cases would be transforming a UTC string to a datetime object
                # We return the validated input arguments as a dictionary
                validated = model.model_validate(args)
            validated_args = validated.model_dump()
        except ValidationError as e:
            logger.warning(f"Validation error for UDF {action_name!r}. {e.errors()!r}")
            raise RegistryValidationError(
                f"Validation error for UDF {action_name!r}. {e.errors()!r}",
                key=action_name,
                err=e,
            ) from e
        except Exception as e:
            raise RegistryValidationError(
                f"Unexpected error when validating input arguments for UDF {action_name!r}. {e}",
                key=action_name,
            ) from e

        return RegistryValidationResult(
            status="success",
            msg="Arguments are valid.",
            validated_args=validated_args,
            ref=ref,
        )
    except RegistryValidationError as e:
        if isinstance(e.err, ValidationError):
            detail = e.err.errors()
        else:
            detail = str(e.err) if e.err else None
        logger.error(
            "Error validating UDF args", action_name=action_name, error=e, detail=detail
        )
        return RegistryValidationResult(
            status="error",
            msg=f"Error validating UDF {action_name}",
            detail=detail,
            ref=ref,
        )
    except KeyError:
        return RegistryValidationResult(
            status="error",
            msg=f"Could not find UDF {action_name!r} in registry. Is this UDF registered?",
            ref=ref,
        )
    except Exception as e:
        raise e


async def validate_dsl_args(
    *,
    session: AsyncSession,
    dsl: DSLInput,
) -> list[ValidationResult]:
    """Validate arguemnts to the DSLInput.

    Check if the input arguemnts are either a templated expression or the correct type.
    """
    val_res: list[ValidationResult] = []
    # Validate the actions
    for act_stmt in dsl.actions:
        # We validate the action args, but keep them as is
        # These will be coerced properly when the workflow is run
        # We store the DSL as is to ensure compatibility with with string reprs
        result = await validate_registry_action_args(
            session=session,
            action_name=act_stmt.action,
            args=act_stmt.args,
            ref=act_stmt.ref,
        )
        if result.status == "error":
            result.msg = f"[{context_locator(act_stmt, 'inputs')}]\n\n{result.msg}"
            val_res.append(result)
        # Validate `run_if`
        if act_stmt.run_if and not is_template_only(act_stmt.run_if):
            val_res.append(
                ValidationResult(
                    status="error",
                    msg=f"[{context_locator(act_stmt, 'run_if')}]\n\n"
                    "`run_if` must only contain an expression.",
                    ref=act_stmt.ref,
                )
            )
        # Validate `for_each`
        # Check that it's an expr or a list of exprs, and that
        match act_stmt.for_each:
            case str():
                if not is_template_only(act_stmt.for_each):
                    val_res.append(
                        ValidationResult(
                            status="error",
                            msg=f"[{context_locator(act_stmt, 'for_each')}]\n\n"
                            "`for_each` must be an expression or list of expressions.",
                            ref=act_stmt.ref,
                        )
                    )
            case list():
                for expr in act_stmt.for_each:
                    if not is_template_only(expr) or not isinstance(expr, str):
                        val_res.append(
                            ValidationResult(
                                status="error",
                                msg=f"[{context_locator(act_stmt, 'for_each')}]\n\n"
                                "`for_each` must be an expression or list of expressions.",
                                ref=act_stmt.ref,
                            )
                        )
            case None:
                pass
            case _:
                val_res.append(
                    ValidationResult(
                        status="error",
                        msg=f"[{context_locator(act_stmt, 'for_each')}]\n\n"
                        "Invalid `for_each` of type {type(act_stmt.for_each)}.",
                        ref=act_stmt.ref,
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
        action_refs={a.ref for a in dsl.actions}, inputs_context=dsl.inputs
    )

    validators = {ExprType.SECRET: secret_validator}
    # This batches all the coros inside the taskgroup
    # and launches them concurrently on __aexit__
    async with GatheringTaskGroup() as tg:
        visitor = ExprValidator(
            task_group=tg,
            validation_context=validation_context,
            validators=validators,  # type: ignore
            # Validate against the specified environment
            environment=dsl.config.environment,
        )
        for act_stmt in dsl.actions:
            # Validate action args
            for expr in extract_expressions(act_stmt.args):
                expr.validate(
                    visitor,
                    loc=context_locator(act_stmt, "inputs"),
                    exclude=exclude,
                )

            # Validate `run_if`
            if act_stmt.run_if:
                # At this point the structure should be correct
                for expr in extract_expressions(act_stmt.run_if):
                    expr.validate(
                        visitor,
                        loc=context_locator(act_stmt, "run_if"),
                        exclude=exclude,
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
                            loc=context_locator(act_stmt, "for_each"),
                            exclude=exclude,
                        )
    return visitor.errors()


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

    iterables: list[Sequence[ValidationResult]] = []

    # Tier 2: Action Args validation
    if validate_args:
        dsl_args_errs = await validate_dsl_args(session=session, dsl=dsl)
        logger.debug(
            f"{len(dsl_args_errs)} DSL args validation errors", errs=dsl_args_errs
        )
        iterables.append(dsl_args_errs)

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
        iterables.append(expr_errs)

    # For secrets we also need to check if any used actions have undefined secrets
    if validate_secrets:
        udf_missing_secrets = await validate_actions_have_defined_secrets(dsl)
        logger.debug(
            f"{len(udf_missing_secrets)} DSL secret validation errors",
            errs=udf_missing_secrets,
        )
        iterables.append(udf_missing_secrets)

    return set(chain(*iterables))
