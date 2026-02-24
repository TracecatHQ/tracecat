from collections.abc import Mapping
from dataclasses import dataclass
from itertools import chain
from typing import Any

import lark
from pydantic import (
    ConfigDict,
    ValidationError,
)
from sqlalchemy.exc import MultipleResultsFound
from sqlalchemy.ext.asyncio import AsyncSession
from tracecat_registry import (
    RegistryOAuthSecret,
    RegistrySecret,
)

from tracecat.auth.types import Role
from tracecat.concurrency import GatheringTaskGroup
from tracecat.db.engine import get_async_session_context_manager
from tracecat.dsl.common import DSLInput, ExecuteSubflowArgs
from tracecat.dsl.enums import PlatformAction
from tracecat.dsl.schemas import ActionStatement
from tracecat.exceptions import RegistryValidationError, TracecatNotFoundError
from tracecat.expressions import patterns
from tracecat.expressions.common import ExprType
from tracecat.expressions.eval import extract_expressions, is_template_only
from tracecat.expressions.expectations import ExpectedField, parse_type
from tracecat.expressions.validator.validator import (
    ExprValidationContext,
    ExprValidator,
)
from tracecat.integrations.enums import OAuthGrantType
from tracecat.integrations.schemas import ProviderKey
from tracecat.integrations.service import IntegrationService
from tracecat.interactions.schemas import ResponseInteraction
from tracecat.logger import logger
from tracecat.registry.actions.service import RegistryActionsService
from tracecat.registry.versions.schemas import RegistryVersionManifest
from tracecat.secrets.service import SecretsService
from tracecat.tiers.entitlements import Entitlement, EntitlementService
from tracecat.tiers.service import TierService
from tracecat.validation.common import json_schema_to_pydantic
from tracecat.validation.schemas import (
    ActionValidationResult,
    DSLValidationResult,
    ExprValidationResult,
    SecretValidationDetail,
    SecretValidationResult,
    ValidationDetail,
    ValidationResult,
)

PERMITTED_INTERACTION_ACTIONS = [
    "tools.slack.post_message",
    "tools.slack.update_message",
]


def get_effective_environment(stmt: ActionStatement, default_environment: str) -> str:
    """Determine the effective environment for an action statement.

    Args:
        stmt: Action statement that may have an environment override
        default_environment: Default environment to use if no override

    Returns:
        The effective environment string
    """
    if stmt.environment and isinstance(stmt.environment, str):
        if not patterns.TEMPLATE_STRING.search(stmt.environment):
            return stmt.environment
    return default_environment


async def validate_single_secret(
    secrets_service: SecretsService,
    checked_keys: set[str],
    environment: str,
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


async def check_action_secrets_from_manifest(
    secrets_service: SecretsService,
    checked_keys: set[str],
    environment: str,
    manifest: RegistryVersionManifest,
    action_name: str,
) -> list[SecretValidationResult]:
    """Check all secrets for an action using manifest data.

    This function uses the manifest to aggregate secrets recursively,
    without needing to fetch RegistryAction from the database.
    """
    results: list[SecretValidationResult] = []

    # Use the static method to aggregate all secrets from the manifest
    secrets = RegistryActionsService.aggregate_secrets_from_manifest(
        manifest, action_name
    )

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

    # We de-duplicate checks per provider+grant type combo
    key_identifier = (
        f"oauth::{registry_secret.provider_id}::{registry_secret.grant_type}"
    )

    # Skip validation if this optional integration isn't configured
    if registry_secret.optional:
        return results

    # Skip if we've already checked this key
    if key_identifier in checked_keys:
        return results

    checked_keys.add(key_identifier)

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
                msg=f"Required OAuth integration {registry_secret.provider_id!r} (grant_type: {registry_secret.grant_type}) is not configured",
            )
        )

    return results


@dataclass(frozen=True)
class ActionEnvPair:
    action: str
    """The action id."""
    environment: str
    """The environment to validate secrets for."""


async def validate_actions_have_defined_secrets(
    dsl: DSLInput,
    *,
    role: Role,
) -> list[SecretValidationResult]:
    """Validate that all actions in the DSL have their required secrets defined."""
    checked_keys: set[str] = set()
    action_env_pairs: set[ActionEnvPair] = set()

    for stmt in dsl.actions:
        env_override = get_effective_environment(stmt, dsl.config.environment)
        action_env_pairs.add(
            ActionEnvPair(action=stmt.action, environment=env_override)
        )

    async with get_async_session_context_manager() as session:
        secrets_service = SecretsService(session, role=role)

        # Get all actions that need validation from index/manifest
        registry_service = RegistryActionsService(session, role=role)
        action_names = [a.action for a in dsl.actions]
        actions_data = await registry_service.get_actions_from_index(action_names)

        # Validate all actions concurrently using manifest-based lookup
        async with GatheringTaskGroup() as tg:
            for action_env_pair in action_env_pairs:
                action_data = actions_data.get(action_env_pair.action)
                if action_data is None:
                    # Action not found in index - skip validation
                    # (will be caught by other validation steps)
                    continue
                tg.create_task(
                    check_action_secrets_from_manifest(
                        secrets_service,
                        checked_keys,
                        action_env_pair.environment,
                        action_data.manifest,
                        action_env_pair.action,
                    )
                )

        return list(chain.from_iterable(tg.results()))


async def validate_registry_action_args(
    *,
    session: AsyncSession,
    role: Role,
    action_name: str,
    action_ref: str,
    args: Mapping[str, Any],
) -> ActionValidationResult:
    """Validate arguments against a UDF spec."""
    # 1. read the schema from the index/manifest
    # 2. construct a pydantic model from the schema
    # 3. validate the args against the pydantic model
    try:
        try:
            if action_name == PlatformAction.CHILD_WORKFLOW_EXECUTE:
                validated = ExecuteSubflowArgs.model_validate(args)
            elif (
                PlatformAction.is_interface(action_name)
                and action_name != PlatformAction.RUN_PYTHON
            ):
                # Other interface/platform actions (ai.action, ai.agent,
                # scatter, gather, etc.) are handled by the workflow
                # engine and don't have registry manifests. Skip
                # deep validation — just pass args through.
                return ActionValidationResult(
                    status="success",
                    msg="Arguments are valid.",
                    validated_args=dict(args),
                    ref=action_ref,
                    action_type=action_name,
                )
            else:
                service = RegistryActionsService(session, role=role)
                action_data = await service.get_action_from_index(action_name)
                if action_data is None:
                    raise KeyError(f"Action {action_name} not found in registry index")
                manifest_action = action_data.manifest.actions.get(action_name)
                if manifest_action is None:
                    raise KeyError(f"Action {action_name} not found in manifest")
                interface = manifest_action.interface
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
                err=str(e),  # Pass the error message to preserve context
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
            # Use the exception message when err is None (e.g., when a non-ValidationError occurred)
            msg = str(e.err) if e.err is not None else str(e)
            detail = [ValidationDetail(type="general", msg=msg)]
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
    role: Role,
    dsl: DSLInput,
) -> list[ActionValidationResult]:
    """Validate arguemnts to the DSLInput.

    Check if the input arguemnts are either a templated expression or the correct type.
    """
    val_res: list[ActionValidationResult] = []
    # Validate the actions
    agent_addons_entitled: bool | None = None
    for act_stmt in dsl.actions:
        details: list[ValidationDetail] = []
        # We validate the action args, but keep them as is
        # These will be coerced properly when the workflow is run
        # We store the DSL as is to ensure compatibility with with string reprs
        result = await validate_registry_action_args(
            session=session,
            role=role,
            action_name=act_stmt.action,
            args=act_stmt.args,
            action_ref=act_stmt.ref,
        )
        if result.status == "error" and result.detail:
            details.extend(result.detail)

        # Entitlement gate: tool approvals are an enterprise feature
        if (
            act_stmt.action == "ai.agent"
            and act_stmt.args.get("tool_approvals") is not None
        ):
            if agent_addons_entitled is None:
                if role.organization_id is None:
                    raise ValueError(
                        "Role must have organization_id to validate entitlements"
                    )
                entitlement_svc = EntitlementService(TierService(session))
                agent_addons_entitled = await entitlement_svc.is_entitled(
                    role.organization_id, Entitlement.AGENT_ADDONS
                )
            if not agent_addons_entitled:
                details.append(
                    ValidationDetail(
                        type="action",
                        msg=(
                            "`tool_approvals` requires the 'agent_addons' entitlement. "
                            "Remove the field or upgrade your plan."
                        ),
                        loc=(act_stmt.ref, "tool_approvals"),
                    )
                )
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
    )

    results: list[ExprValidationResult] = []
    for act_stmt in dsl.actions:
        if act_stmt.environment is not None:
            # Only literal strings are permitted
            if not isinstance(
                act_stmt.environment, str
            ) or patterns.TEMPLATE_STRING.search(act_stmt.environment):
                results.append(
                    ExprValidationResult(
                        status="error",
                        msg=(
                            "Template expressions are not allowed in "
                            "`environment` overrides. Provide a literal string."
                        ),
                        ref=act_stmt.ref,
                        expression_type=ExprType.ENV,
                    )
                )
                # Skip further processing for this action – the error is terminal
                continue

        env_override = get_effective_environment(act_stmt, dsl.config.environment)

        async with ExprValidator(
            validation_context=validation_context,
            environment=env_override,
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


def validate_entrypoint_expects(
    expects: Mapping[str, Any] | None,
) -> list[DSLValidationResult]:
    """Validate a workflow entrypoint expects mapping."""

    if not expects:
        return []

    results: list[DSLValidationResult] = []
    for field_name, raw_field in expects.items():
        details: list[ValidationDetail] = []
        try:
            validated_field = ExpectedField.model_validate(raw_field)
        except ValidationError as e:
            for detail in ValidationDetail.list_from_pydantic(e):
                loc = ("entrypoint", "expects", field_name)
                if detail.loc:
                    loc = (*loc, *detail.loc)
                details.append(
                    ValidationDetail(
                        type=f"entrypoint.{detail.type}",
                        msg=detail.msg,
                        loc=loc,
                    )
                )
        else:
            try:
                parse_type(validated_field.type, field_name)
            except lark.UnexpectedInput as e:
                details.append(
                    ValidationDetail(
                        type="entrypoint.expects.type",
                        msg=f"Failed to parse type {validated_field.type!r}: {e}",
                        loc=("entrypoint", "expects", field_name, "type"),
                    )
                )
            except ValueError as e:
                details.append(
                    ValidationDetail(
                        type="entrypoint.expects.type",
                        msg=str(e),
                        loc=("entrypoint", "expects", field_name, "type"),
                    )
                )
            except Exception as e:
                details.append(
                    ValidationDetail(
                        type="entrypoint.expects.type",
                        msg=f"Unexpected error validating type: {e}",
                        loc=("entrypoint", "expects", field_name, "type"),
                    )
                )

        if details:
            results.append(
                DSLValidationResult(
                    status="error",
                    msg=f"Invalid entrypoint expected field '{field_name}'.",
                    detail=details,
                    ref=field_name,
                )
            )

    return results


def validate_dsl_entrypoint(dsl: DSLInput) -> list[DSLValidationResult]:
    """Validate the DSL entrypoint schema."""

    return validate_entrypoint_expects(dsl.entrypoint.expects)


async def validate_dsl(
    session: AsyncSession,
    dsl: DSLInput,
    *,
    role: Role,
    validate_entrypoint: bool = True,
    validate_args: bool = True,
    validate_expressions: bool = True,
    validate_secrets: bool = True,
    exclude_exprs: set[ExprType] | None = None,
) -> set[ValidationResult]:
    """Validate the DSL at commit time.

    This function calls and combines all results from each validation tier.
    """
    if not any(
        (validate_entrypoint, validate_args, validate_expressions, validate_secrets)
    ):
        return set()

    iterables: list[ValidationResult] = []

    # Tier 1: Entrypoint schema validation
    if validate_entrypoint:
        entrypoint_errs = validate_dsl_entrypoint(dsl)
        logger.debug(
            f"{len(entrypoint_errs)} DSL entrypoint validation errors",
            errs=entrypoint_errs,
        )
        iterables.extend(ValidationResult.new(err) for err in entrypoint_errs)

    # Tier 2: Action Args validation
    if validate_args:
        dsl_args_errs = await validate_dsl_actions(session=session, role=role, dsl=dsl)
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
        udf_missing_secrets = await validate_actions_have_defined_secrets(
            dsl, role=role
        )
        logger.debug(
            f"{len(udf_missing_secrets)} DSL secret validation errors",
            errs=udf_missing_secrets,
        )
        iterables.extend(ValidationResult.new(err) for err in udf_missing_secrets)

    return set(iterables)
