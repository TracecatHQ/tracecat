"""Tracecat validation module.

Validation tiers
----------------
# (1) Validate DSLInput on pydantic model creation

# (2) Validate the arguments in each of the action statements in the DSL against the registry UDFs
[x] Checks that the action referenced a valid UDF
[x] Checks that the arguments to each UDF are correctly named and typed

# (3) Validate the expressions in the DSL
We find all expressions in the DSL and validate them depending on their type.

## SECRETS
[x] Check if the secret is defined in the secrets manager

## ACTIONS
Basic
[x] Check if the action is a valid reference (no structural check)
[x] Check that it's used correctly e.g. `ref.[result|result_typemane]`
Advanced
[ ] Check that the action is correctly referencing an ancestor action (won't just randomly fail)

## INPUTS
Note that static inputs are defined at the top of the file
[x] Check that there are no templated expressions in the inputs
[x] Check that input expressions are all valid (i.e. attempt to evaluate it, since it's static)
 - for now, performing checks on static inputs are redundant as they can be evlauted immediately

## TRIGGERS
Trigger data is dynamic input data. It's not defined in the DSL, but is passed in at runtime.
Let's shift the responsibility of the trigger data validation to the user.
Meaning, let the user define a simple schema for the trigger data and validate it at runtime.
[ ] Check that the trigger data is valid



"""

from __future__ import annotations

from itertools import chain
from typing import TYPE_CHECKING, Any, Optional, cast

from pydantic import BaseModel, Field, ValidationError, create_model
from sqlalchemy.exc import MultipleResultsFound, NoResultFound
from sqlmodel.ext.asyncio.session import AsyncSession
from tracecat_registry import RegistrySecret

from tracecat.concurrency import GatheringTaskGroup
from tracecat.db.schemas import RegistryAction
from tracecat.dsl.common import DSLInput
from tracecat.expressions.eval import extract_expressions, is_template_only
from tracecat.expressions.parser.validator import (
    ExprValidationContext,
    ExprValidationResult,
    ExprValidator,
)
from tracecat.expressions.shared import ExprType, context_locator
from tracecat.logger import logger
from tracecat.registry.actions.models import ArgsT
from tracecat.registry.actions.service import RegistryActionsService
from tracecat.secrets.models import SearchSecretsParams
from tracecat.secrets.service import SecretsService
from tracecat.types.exceptions import RegistryValidationError
from tracecat.types.validation import (
    RegistryValidationResult,
    SecretValidationResult,
    ValidationResult,
)

if TYPE_CHECKING:
    from tracecat.dsl.common import DSLInput


def get_validators():
    return {ExprType.SECRET: secret_validator}


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
        result = await vadliate_registry_action_args(
            session=session,
            action_name=act_stmt.action,
            version=dsl.config.registry_version,
            args=act_stmt.args,
        )
        if result.status == "error":
            result.msg = f"[{context_locator(act_stmt, "inputs")}]\n\n{result.msg}"
            val_res.append(result)
        # Validate `run_if`
        if act_stmt.run_if and not is_template_only(act_stmt.run_if):
            val_res.append(
                ValidationResult(
                    status="error",
                    msg=f"[{context_locator(act_stmt, "run_if")}]\n\n"
                    "`run_if` must only contain an expression.",
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
                            msg=f"[{context_locator(act_stmt, "for_each")}]\n\n"
                            "`for_each` must be an expression or list of expressions.",
                        )
                    )
            case list():
                for expr in act_stmt.for_each:
                    if not is_template_only(expr) or not isinstance(expr, str):
                        val_res.append(
                            ValidationResult(
                                status="error",
                                msg=f"[{context_locator(act_stmt, "for_each")}]\n\n"
                                "`for_each` must be an expression or list of expressions.",
                            )
                        )
            case None:
                pass
            case _:
                val_res.append(
                    ValidationResult(
                        status="error",
                        msg=f"[{context_locator(act_stmt, "for_each")}]\n\n"
                        "Invalid `for_each` of type {type(act_stmt.for_each)}.",
                    )
                )

    # Validate `returns`

    return val_res


async def secret_validator(
    *, name: str, key: str, loc: str, environment: str
) -> ExprValidationResult:
    # (1) Check if the secret is defined
    async with SecretsService.with_session() as service:
        defined_secret = await service.search_secrets(
            SearchSecretsParams(names=[name], environment=environment)
        )
        logger.info("Secret search results", defined_secret=defined_secret)
        if (n_found := len(defined_secret)) != 1:
            logger.error(
                "Secret not found in SECRET context usage",
                n_found=n_found,
                secret_name=name,
                environment=environment,
            )
            return ExprValidationResult(
                status="error",
                msg=f"[{loc}]\n\nFound {n_found} secrets matching {name!r} in the {environment!r} environment.",
                expression_type=ExprType.SECRET,
            )

        # There should only be 1 secret
        decrypted_keys = service.decrypt_keys(defined_secret[0].encrypted_keys)
        defined_keys = {kv.key for kv in decrypted_keys}

    # (2) Check if the secret has the correct keys
    if key not in defined_keys:
        logger.error(
            "Missing secret keys in SECRET context usage",
            secret_name=name,
            missing_key=key,
        )
        return ExprValidationResult(
            status="error",
            msg=f"Secret {name!r} is missing key: {key!r}",
            expression_type=ExprType.SECRET,
        )
    return ExprValidationResult(status="success", expression_type=ExprType.SECRET)


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
            validators=validators,
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

    iterables = []

    # Tier 2: UDF Args validation
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


async def vadliate_registry_action_args(
    *, session: AsyncSession, action_name: str, version: str, args: ArgsT
) -> RegistryValidationResult:
    """Validate arguments against a UDF spec."""
    # 1. read the schema from the db
    # 2. construct a pydantic model from the schema
    # 3. validate the args against the pydantic model
    try:
        service = RegistryActionsService(session)
        action = await service.get_action(version=version, action_name=action_name)
        model = json_schema_to_pydantic(action.interface)
        try:
            # Note that we're allowing type coercion for the input arguments
            # Use cases would be transforming a UTC string to a datetime object
            # We return the validated input arguments as a dictionary
            validated: BaseModel = model.model_validate(args)
            validated_args = cast(ArgsT, validated.model_dump())
        except ValidationError as e:
            logger.error(f"Validation error for UDF {action_name!r}. {e.errors()!r}")
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
            status="success", msg="Arguments are valid.", validated_args=validated_args
        )
    except RegistryValidationError as e:
        if isinstance(e.err, ValidationError):
            detail = e.err.errors()
        else:
            detail = str(e.err) if e.err else None
        return RegistryValidationResult(
            status="error", msg=f"Error validating UDF {action_name}", detail=detail
        )
    except KeyError:
        return RegistryValidationResult(
            status="error",
            msg=f"Could not find UDF {action_name!r} in registry. Is this UDF registered?",
        )
    except Exception as e:
        raise e


def json_schema_to_pydantic(
    schema: dict[str, Any],
    base_schema: dict[str, Any] | None = None,
    *,
    name: str = "DynamicModel",
) -> type[BaseModel]:
    if base_schema is None:
        base_schema = schema

    def resolve_ref(ref: str) -> dict[str, Any]:
        parts = ref.split("/")
        current = base_schema
        for part in parts[1:]:  # Skip the first '#' part
            current = current[part]
        return current

    def create_field(prop_schema: dict[str, Any]) -> type:
        if "$ref" in prop_schema:
            referenced_schema = resolve_ref(prop_schema["$ref"])
            return json_schema_to_pydantic(referenced_schema, base_schema)

        type_ = prop_schema.get("type")
        if type_ == "object":
            return json_schema_to_pydantic(prop_schema, base_schema)
        elif type_ == "array":
            items = prop_schema.get("items", {})
            return list[create_field(items)]
        elif type_ == "string":
            return str
        elif type_ == "integer":
            return int
        elif type_ == "number":
            return float
        elif type_ == "boolean":
            return bool
        else:
            return Any

    properties: dict[str, Any] = schema.get("properties", {})
    required: list[str] = schema.get("required", [])

    fields = {}
    for prop_name, prop_schema in properties.items():
        field_type = create_field(prop_schema)
        field_params = {}

        if "description" in prop_schema:
            field_params["description"] = prop_schema["description"]

        if prop_name not in required:
            field_type = Optional[field_type]  # noqa: UP007
            field_params["default"] = None

        fields[prop_name] = (field_type, Field(**field_params))

    model_name = schema.get("title", name)
    return create_model(model_name, **fields)


async def validate_actions_have_defined_secrets(
    dsl: DSLInput,
) -> list[SecretValidationResult]:
    # 1. Find all secrets in the DSL
    # 2. Find all UDFs in the DSL
    # 3. Check if the UDFs have any secrets that are not defined in the secrets manager

    # In memory cache to prevent duplicate checks
    checked_keys_cache: set[str] = set()
    environment = dsl.config.environment

    async def check_action_secrets_defined(
        action: RegistryAction,
    ) -> list[SecretValidationResult]:
        """Checks that this secrets needed by this UDF are in the secrets manager.

        Raise a `TracecatCredentialsError` if:
        1. The secret is not defined in the secrets manager
        2. The secret is defined, but has mismatched keys

        """
        nonlocal checked_keys_cache
        results: list[SecretValidationResult] = []
        async with SecretsService.with_session() as service:
            for registry_secret in action.secrets or []:
                registry_secret = RegistrySecret.model_validate(registry_secret)
                if registry_secret.name in checked_keys_cache:
                    continue
                # (1) Check if the secret is defined
                try:
                    defined_secret = await service.get_secret_by_name(
                        registry_secret.name,
                        raise_on_error=True,
                        environment=environment,
                    )
                except (NoResultFound, MultipleResultsFound) as e:
                    secret_repr = f"{registry_secret.name!r} (env: {environment!r})"
                    if isinstance(e, NoResultFound):
                        msg = f"Secret {secret_repr} is missing in the secrets manager."
                    else:
                        msg = f"Multiple secrets found when searching for secret {secret_repr}."

                    results.append(
                        SecretValidationResult(
                            status="error",
                            msg=f"[{action.action}]\n\n{msg}",
                            detail={
                                "environment": environment,
                                "secret_name": registry_secret.name,
                            },
                        )
                    )
                    continue
                finally:
                    # This will get run even if the above fails and the loop continues
                    checked_keys_cache.add(registry_secret.name)
                decrypted_keys = service.decrypt_keys(defined_secret.encrypted_keys)
                defined_keys = {kv.key for kv in decrypted_keys}
                required_keys = set(registry_secret.keys)

                # # (2) Check if the secret has the correct keys
                if not required_keys.issubset(defined_keys):
                    results.append(
                        SecretValidationResult(
                            status="error",
                            msg=f"Secret {registry_secret.name!r} is missing keys: {required_keys - defined_keys}",
                        )
                    )

        return results

    udf_keys = {a.action for a in dsl.actions}
    async with RegistryActionsService.with_session() as service:
        actions = await service.list_actions(include_keys=udf_keys)
    async with GatheringTaskGroup() as tg:
        for action in actions:
            tg.create_task(check_action_secrets_defined(action))
    return list(chain.from_iterable(tg.results()))
