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
[ ] Check that there are no templated expressions in the inputs
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
from pydantic import ValidationError

from tracecat.auth.clients import AuthenticatedAPIClient
from tracecat.concurrency import GatheringTaskGroup
from tracecat.contexts import ctx_role
from tracecat.db import schemas
from tracecat.expressions.eval import extract_expressions
from tracecat.expressions.shared import ExprType
from tracecat.expressions.visitors import ExprValidationResult, ExprValidatorVisitor
from tracecat.expressions.visitors.validator import ExprValidationContext
from tracecat.logging import logger
from tracecat.registry import RegisteredUDF, RegistryValidationError, registry
from tracecat.types.auth import Role
from tracecat.types.validation import (
    RegistryValidationResult,
    SecretValidationResult,
    ValidationResult,
)

if TYPE_CHECKING:
    from tracecat.dsl.common import DSLInput


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


async def secret_validator(name: str, key: str) -> ExprValidationResult:
    try:
        # (1) Check if the secret is defined
        role = ctx_role.get()
        async with AuthenticatedAPIClient(
            role=Role(type="service", user_id=role.user_id, service_id=role.service_id)
        ) as client:
            res = await client.get(f"/secrets/{name}")
            res.raise_for_status()  # Raise an exception for HTTP error codes
        # (2) Check if the secret has the correct keys
        defined_secret = schemas.Secret.model_validate_json(res.content)
        defined_keys = {kv.key for kv in defined_secret.keys or []}

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
    except httpx.HTTPStatusError:
        logger.error("Missing secret in SECRET context usage", secret_name=name)
        return ExprValidationResult(
            status="error",
            msg=f"Failed to retrieve secret {name!r}. Please check whether you've defined"
            " it correctly and it exists in the secret manager.",
            exprssion_type=ExprType.SECRET,
        )


def default_validator_visitor(
    task_group: GatheringTaskGroup, validation_context: ExprValidationContext
) -> ExprValidatorVisitor:
    return ExprValidatorVisitor(
        task_group=task_group,
        validation_context=validation_context,
        validators={ExprType.SECRET: secret_validator},
    )


async def validate_dsl_expressions(dsl: DSLInput) -> list[ExprValidationResult]:
    """Validate the DSL expressions at commit time."""
    validation_context = ExprValidationContext(
        action_refs={a.ref for a in dsl.actions}, inputs_context=dsl.inputs
    )
    async with GatheringTaskGroup() as tg:
        visitor = default_validator_visitor(
            task_group=tg, validation_context=validation_context
        )
        for act_stmt in dsl.actions:
            # This batches all the coros inside the taskgroup
            # and launches them concurrently on __aexit__
            for expr in extract_expressions(act_stmt.args):
                expr.validate(visitor)
    return visitor.errors()


async def validate_actions_have_defined_secrets(
    dsl: DSLInput,
) -> list[SecretValidationResult]:
    # 1. Find all secrets in the DSL
    # 2. Find all UDFs in the DSL
    # 3. Check if the UDFs have any secrets that are not defined in the secrets manager

    # In memory cache to prevent duplicate checks
    checked_keys_cache: set[str] = set()

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
            try:
                # (1) Check if the secret is defined
                res = await client.get(f"/secrets/{registry_secret.name}")
                res.raise_for_status()  # Raise an exception for HTTP error codes
                # (2) Check if the secret has the correct keys
                defined_secret = schemas.Secret.model_validate_json(res.content)
                defined_keys = {kv.key for kv in defined_secret.keys or []}
                required_keys = set(registry_secret.keys)

                if not required_keys.issubset(defined_keys):
                    results.append(
                        SecretValidationResult(
                            status="error",
                            msg=f"Secret {registry_secret.name!r} is missing keys: {required_keys - defined_keys}",
                        )
                    )
            except httpx.HTTPStatusError:
                if res.status_code == 404:
                    msg = (
                        f"Couldn't find secret {registry_secret.name!r} in the secrets manager."
                        f" Please add it using the CLI or UI. This secret requires keys: {registry_secret.keys}"
                    )
                else:
                    msg = (
                        f"Failed to retrieve secret {registry_secret.name!r}. Please check whether you have the correct permissions or have defined"
                        " it correctly and it exists in the secret manager."
                    )
                logger.error(msg)
                results.append(SecretValidationResult(status="error", msg=msg))
            finally:
                checked_keys_cache.add(registry_secret.name)
        return results

    udf_keys = {a.action for a in dsl.actions}
    role = ctx_role.get()
    async with (
        AuthenticatedAPIClient(
            role=Role(type="service", user_id=role.user_id, service_id=role.service_id)
        ) as client,
        GatheringTaskGroup() as tg,
    ):
        for _, udf in registry.filter(include_keys=udf_keys):
            tg.create_task(check_udf_secrets_defined(udf))
    return list(chain.from_iterable(tg.results()))


async def validate_dsl(dsl: DSLInput) -> list[ValidationResult]:
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

    expr_errs = await validate_dsl_expressions(dsl)
    logger.debug("DSL expression validation errors", errs=expr_errs)

    # For secrets we also need to check if any used actions have undefined secrets
    udf_missing_secrets = await validate_actions_have_defined_secrets(dsl)
    return list(chain(dsl_args_errs, expr_errs, udf_missing_secrets))
