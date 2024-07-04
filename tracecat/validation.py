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
from typing import TYPE_CHECKING, Any

import httpx
import sqlmodel
from pydantic import BaseModel, ConfigDict, Field, ValidationError, create_model
from slugify import slugify

from tracecat.concurrency import GatheringTaskGroup, apartial
from tracecat.expressions.eval import extract_expressions
from tracecat.expressions.shared import ExprType
from tracecat.expressions.visitors import ExprValidationResult, ExprValidatorVisitor
from tracecat.expressions.visitors.validator import ExprValidationContext
from tracecat.logging import logger
from tracecat.registry import RegisteredUDF, RegistryValidationError, registry
from tracecat.secrets.service import SecretsService
from tracecat.types.validation import (
    VALIDATION_TYPES,
    RegistryValidationResult,
    SecretValidationResult,
    ValidationResult,
)

if TYPE_CHECKING:
    from tracecat.dsl.common import DSLInput


def get_validators(*, secrets_service: SecretsService):
    return {
        ExprType.SECRET: apartial(secret_validator, service=secrets_service),
    }


def validate_dsl_args(dsl: DSLInput) -> list[RegistryValidationResult]:
    """Validate arguemnts to the DSLInput.

    Check if the input arguemnts are either a templated expression or the correct type.
    """
    error_responses: list[RegistryValidationResult] = []
    for act_stmt in dsl.actions:
        # We validate the action args, but keep them as is
        # These will be coerced properly when the workflow is run
        # We store the DSL as is to ensure compatibility with with string reprs
        result = vadliate_udf_args(act_stmt.action, act_stmt.args)
        if result.status == "error":
            error_responses.append(result)
    return error_responses


def vadliate_udf_args(udf_key: str, args: dict[str, Any]) -> RegistryValidationResult:
    """Validate arguments against a UDF spec."""
    try:
        udf = registry.get(udf_key)
        validated_args = udf.validate_args(**args)
        return RegistryValidationResult(
            status="success", msg="Arguments are valid.", validated_args=validated_args
        )
    except RegistryValidationError as e:
        if isinstance(e.err, ValidationError):
            detail = e.err.errors()
        else:
            detail = str(e.err) if e.err else None
        return RegistryValidationResult(
            status="error", msg=f"Error validating UDF {udf_key}", detail=detail
        )
    except KeyError:
        return RegistryValidationResult(
            status="error",
            msg=f"Could not find UDF {udf_key!r} in registry. Is this UDF registered?",
        )
    except Exception as e:
        raise e


async def secret_validator(
    name: str, key: str, *, service: SecretsService
) -> ExprValidationResult:
    # (1) Check if the secret is defined
    defined_secret = await service.aget_secret_by_name(name)
    logger.error("Secret validator", defined_secret=defined_secret, name=name, key=key)
    if not defined_secret:
        logger.error("Missing secret in SECRET context usage", secret_name=name)
        return ExprValidationResult(
            status="error",
            msg=f"Secret {name!r} is not defined in the secrets manager.",
            exprssion_type=ExprType.SECRET,
        )
    decrypted_keys = service.decrypt_keys(defined_secret.encrypted_keys)
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
            exprssion_type=ExprType.SECRET,
        )
    return ExprValidationResult(status="success", exprssion_type=ExprType.SECRET)


async def validate_dsl_expressions(
    session: sqlmodel.Session, dsl: DSLInput
) -> list[ExprValidationResult]:
    """Validate the DSL expressions at commit time."""
    validation_context = ExprValidationContext(
        action_refs={a.ref for a in dsl.actions}, inputs_context=dsl.inputs
    )
    secrets_service = SecretsService(session)
    validators = get_validators(secrets_service=secrets_service)
    async with GatheringTaskGroup() as tg:
        visitor = ExprValidatorVisitor(
            task_group=tg, validation_context=validation_context, validators=validators
        )
        for act_stmt in dsl.actions:
            # This batches all the coros inside the taskgroup
            # and launches them concurrently on __aexit__
            for expr in extract_expressions(act_stmt.args):
                expr.validate(visitor)
    return visitor.errors()


async def validate_actions_have_defined_secrets(
    session: sqlmodel.Session, dsl: DSLInput
) -> list[SecretValidationResult]:
    # 1. Find all secrets in the DSL
    # 2. Find all UDFs in the DSL
    # 3. Check if the UDFs have any secrets that are not defined in the secrets manager

    # In memory cache to prevent duplicate checks
    checked_keys_cache: set[str] = set()
    secrets_service = SecretsService(session)

    async def check_udf_secrets_defined(
        udf: RegisteredUDF,
    ) -> list[SecretValidationResult]:
        """Checks that this secrets needed by this UDF are in the secrets manager.

        Raise a `TracecatCredentialsError` if:
        1. The secret is not defined in the secrets manager
        2. The secret is defined, but has mismatched keys

        """
        nonlocal checked_keys_cache
        results: list[SecretValidationResult] = []
        for registry_secret in udf.secrets or []:
            if registry_secret.name in checked_keys_cache:
                continue
            # (1) Check if the secret is defined
            defined_secret = await secrets_service.aget_secret_by_name(
                registry_secret.name
            )
            checked_keys_cache.add(registry_secret.name)
            if not defined_secret:
                msg = (
                    f"Secret {registry_secret.name!r} is not defined in the secrets manager."
                    f" Please add it using the CLI or UI. This secret requires keys: {registry_secret.keys}",
                )
                results.append(SecretValidationResult(status="error", msg=msg))
                continue
            decrypted_keys = secrets_service.decrypt_keys(defined_secret.encrypted_keys)
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
    async with GatheringTaskGroup() as tg:
        for _, udf in registry.filter(include_keys=udf_keys):
            tg.create_task(check_udf_secrets_defined(udf))
    return list(chain.from_iterable(tg.results()))


async def validate_dsl(
    session: sqlmodel.Session, dsl: DSLInput
) -> list[ValidationResult]:
    """Validate the DSL at commit time.

    This function calls and combines all results from each validation tier.
    """
    # Tier 1: Done by pydantic model

    # Tier 2: UDF Args validation
    dsl_args_errs = validate_dsl_args(dsl)
    logger.debug("DSL args validation errors", errs=dsl_args_errs)

    # Tier 3: Expression validation
    # When we reach this point, the inputs have been validated properly (ignoring templated expressions)
    # We now have to validate that the expressions are valid
    # 1. Find all expressions in the inputs
    # 2. For each expression context, cross-reference the expressions API and udf registry

    expr_errs = await validate_dsl_expressions(session, dsl)
    logger.debug("DSL expression validation errors", errs=expr_errs)

    # For secrets we also need to check if any used actions have undefined secrets
    udf_missing_secrets = await validate_actions_have_defined_secrets(session, dsl)
    return list(chain(dsl_args_errs, expr_errs, udf_missing_secrets))


def generate_model_from_schema(schema: dict[str, str], model_name: str) -> BaseModel:
    fields = {}
    for field_name, field_type in schema.items():
        field_info = Field(default=...)  # Required
        fields[field_name] = (VALIDATION_TYPES[field_type], field_info)
    return create_model(
        f"{model_name}__trigger_input_validator",
        __config__=ConfigDict(extra="forbid"),
        **fields,
    )


def validate_trigger_inputs(
    dsl: DSLInput, payload: dict[str, Any] | None = None
) -> ValidationResult:
    if dsl.entrypoint.expects is None:
        # If there's no expected trigger input schema, we don't validate it
        # as its ignored anyways
        return ValidationResult(
            status="success", msg="No trigger input schema, skipping validation."
        )
    val_model = generate_model_from_schema(
        dsl.entrypoint.expects, slugify(dsl.title, separator="_")
    )
    if payload is None:
        return ValidationResult(
            status="error",
            msg="Trigger input schema is defined but no payload was provided.",
            detail={"schema": val_model.model_json_schema()},
        )

    try:
        val_model.model_validate(payload)
        return ValidationResult(status="success", msg="Trigger inputs are valid.")
    except ValidationError as e:
        return ValidationResult(
            status="error",
            msg=f"Validation error in trigger inputs ({e.title}). Please refer to the schema for more details.",
            detail={"errors": e.errors(), "schema": val_model.model_json_schema()},
        )
