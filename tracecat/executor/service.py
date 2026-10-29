from __future__ import annotations

import asyncio
import itertools
from collections.abc import Iterator, Mapping, MutableMapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from aiocache import Cache
from pydantic import ValidationError
from sqlalchemy import and_, or_, select, union_all
from tracecat_ee.tunnels.routing import (
    TunnelRoute,
    TunnelRouteError,
    effective_tunnel,
    resolve_tunnel_route,
)

from tracecat import config
from tracecat.auth.executor_tokens import ExecutionOrigin, mint_executor_token
from tracecat.auth.types import Role
from tracecat.authz.controls import require_action_scope
from tracecat.contexts import (
    ctx_logical_time,
    ctx_role,
    ctx_secret_masks,
)
from tracecat.db.engine import get_async_session_bypass_rls_context_manager
from tracecat.db.models import (
    PlatformRegistryRepository,
    PlatformRegistryVersion,
    RegistryRepository,
    RegistryVersion,
)
from tracecat.dsl.common import context_locator, create_default_execution_context
from tracecat.dsl.enums import PlatformAction
from tracecat.dsl.schemas import (
    ActionStatement,
    DSLEnvironment,
    ExecutionContext,
    RunActionInput,
    TaskResult,
    TemplateExecutionContext,
)
from tracecat.exceptions import (
    ExecutionError,
    LoopExecutionError,
    RegistryValidationError,
    TracecatAuthorizationError,
    TracecatException,
)
from tracecat.executor import registry_resolver
from tracecat.executor.backends.base import ExecutorBackend
from tracecat.executor.error_policy import chained_error_classification
from tracecat.executor.minimal_runner import ActionTimeoutError
from tracecat.executor.schemas import (
    ActionImplementation,
    ExecutorActionErrorInfo,
    ExecutorResultSuccess,
    ResolvedContext,
)
from tracecat.executor.secret_preprocessors import (
    SecretEnvProjection,
    collect_mask_values,
    project_secret_env,
)
from tracecat.expressions.common import ExprContext
from tracecat.expressions.eval import (
    collect_expressions,
    eval_templated_object,
    get_iterables_from_expression,
)
from tracecat.expressions.expectations import create_expectation_model
from tracecat.expressions.policy import (
    ActionArgumentPlan,
    ProvenanceMap,
    build_provenance,
    resolve_action_args,
)
from tracecat.identifiers import OrganizationID
from tracecat.logger import logger
from tracecat.observability.sentry import capture_activity_failure
from tracecat.registry.actions.schemas import TemplateActionDefinition
from tracecat.registry.constants import DEFAULT_REGISTRY_ORIGIN
from tracecat.runtime.errors import RuntimeErrorClassification, RuntimeErrorOwner
from tracecat.secrets import secrets_manager
from tracecat.secrets.common import (
    apply_masks_object,
    await_with_masked_errors,
    call_with_masked_errors,
)
from tracecat.secrets.masking import SecretMaskCollector
from tracecat.variables.schemas import VariableSearch
from tracecat.variables.service import VariablesService

"""All these methods are used in the registry executor, not on the worker"""


type ArgsT = Mapping[str, Any]
type ExecutionResult = Any | ExecutorActionErrorInfo


def _sanitize_error_info(
    info: ExecutorActionErrorInfo,
    classification: RuntimeErrorClassification | None,
) -> ExecutorActionErrorInfo:
    """Keep diagnostics, masking observed secrets before transport or logging."""
    if (
        classification is not None
        and classification.owner is RuntimeErrorOwner.PLATFORM
    ):
        info = info.model_copy(update={"message": classification.message})
    masks = ctx_secret_masks.get()
    return (
        ExecutorActionErrorInfo.model_validate(
            {key: masks.redact(value) for key, value in info.model_dump().items()}
        )
        if masks is not None
        else info.model_copy()
    )


def _execution_origin_for_role(role: Role) -> ExecutionOrigin:
    """Classify the trusted entry point that dispatched an executor action."""
    match role:
        case Role(type="service", service_id="tracecat-mcp"):
            return "agent"
        case _:
            return "workflow"


def _mint_action_executor_token(input: RunActionInput, role: Role) -> str:
    """Mint SDK credentials carrying the root action's trusted provenance."""
    if role.workspace_id is None:
        raise ValueError("workspace_id is required for action execution")
    execution_origin = _execution_origin_for_role(role)
    return mint_executor_token(
        workspace_id=role.workspace_id,
        user_id=role.user_id,
        service_id=role.service_id,
        execution_origin=execution_origin,
        root_action=input.task.action,
        agent_session_id=(
            input.agent_session_id if execution_origin == "agent" else None
        ),
        wf_id=str(input.run_context.wf_id),
        wf_exec_id=str(input.run_context.wf_run_id),
    )


@dataclass
class DispatchActionContext:
    role: Role


@dataclass
class RegistryArtifactsContext:
    origin: str
    version: str
    artifact_uri: str


# Cache for individual artifacts (origin, version) -> RegistryArtifactsContext
# Cast to Any since aiocache doesn't have proper type stubs
_artifact_cache: Any = Cache(Cache.MEMORY, ttl=60)


def _artifact_cache_key(
    origin: str, version: str, organization_id: OrganizationID
) -> str:
    return f"artifact:{organization_id}:{origin}:{version}"


async def get_registry_artifacts_for_lock(
    origins: dict[str, str],
    organization_id: OrganizationID,
) -> list[RegistryArtifactsContext]:
    """Get registry tarball URIs for specific locked versions.

    Uses per-artifact caching - only queries DB for cache misses.
    Uses UNION ALL to query both platform and org-scoped tables in a single round-trip.

    Both table hierarchies share the same column structure via BaseRegistryVersion/BaseRegistryRepository,
    so we select only the common columns and union the results.

    Args:
        origins: Maps origin -> version string from RegistryLock["origins"].
            Example: {"tracecat_registry": "2024.12.10.123456", "git+ssh://...": "abc1234"}
        organization_id: The organization ID for scoping the registry lookup.

    Returns:
        List of RegistryArtifactsContext for the locked versions.
    """
    if not origins:
        return []

    # Check cache for each origin/version
    cached_artifacts: list[RegistryArtifactsContext] = []
    platform_misses: list[tuple[str, str]] = []
    org_misses: list[tuple[str, str]] = []

    for origin, version in origins.items():
        key = _artifact_cache_key(origin, version, organization_id)
        cached = await _artifact_cache.get(key=key)
        if cached is not None:
            cached_artifacts.append(cached)
        elif origin == DEFAULT_REGISTRY_ORIGIN:
            platform_misses.append((origin, version))
        else:
            org_misses.append((origin, version))

    # If no misses, return cached results
    if not platform_misses and not org_misses:
        return sorted(cached_artifacts, key=lambda x: x.origin)

    # Fetch all misses with a single UNION ALL query
    fetched_artifacts: list[RegistryArtifactsContext] = []
    async with get_async_session_bypass_rls_context_manager() as session:
        statements = []

        # Platform query (no org filter)
        if platform_misses:
            platform_conditions = [
                and_(
                    PlatformRegistryRepository.origin == origin,
                    PlatformRegistryVersion.version == version,
                )
                for origin, version in platform_misses
            ]
            platform_stmt = (
                select(
                    PlatformRegistryRepository.origin,
                    PlatformRegistryVersion.version,
                    PlatformRegistryVersion.tarball_uri,
                )
                .join(
                    PlatformRegistryVersion,
                    PlatformRegistryVersion.repository_id
                    == PlatformRegistryRepository.id,
                )
                .where(or_(*platform_conditions))
            )
            statements.append(platform_stmt)

        # Org query (with org filter)
        if org_misses:
            org_conditions = [
                and_(
                    RegistryRepository.origin == origin,
                    RegistryVersion.version == version,
                )
                for origin, version in org_misses
            ]
            org_stmt = (
                select(
                    RegistryRepository.origin,
                    RegistryVersion.version,
                    RegistryVersion.tarball_uri,
                )
                .join(
                    RegistryVersion,
                    RegistryVersion.repository_id == RegistryRepository.id,
                )
                .where(
                    RegistryRepository.organization_id == organization_id,
                    or_(*org_conditions),
                )
            )
            statements.append(org_stmt)

        # Execute single UNION ALL query
        if len(statements) == 1:
            combined = statements[0]
        else:
            combined = union_all(*statements)

        result = await session.execute(combined)
        rows = result.all()

        # Process results
        found_keys: set[tuple[str, str]] = set()
        for row in rows:
            origin_val, version_val, artifact_uri = row
            if artifact_uri is not None:
                artifact = RegistryArtifactsContext(
                    origin=origin_val,
                    version=version_val,
                    artifact_uri=artifact_uri,
                )
                fetched_artifacts.append(artifact)
                found_keys.add((origin_val, version_val))
                # Cache the artifact
                key = _artifact_cache_key(origin_val, version_val, organization_id)
                await _artifact_cache.set(key=key, value=artifact)
            else:
                logger.warning(
                    "Registry version found but missing artifact URI",
                    origin=origin_val,
                    version=version_val,
                )

        # Log warnings for any misses not found
        for origin, version in platform_misses + org_misses:
            if (origin, version) not in found_keys:
                logger.warning(
                    "Registry version not found for lock entry",
                    origin=origin,
                    version=version,
                )

    # Combine cached and fetched artifacts
    all_artifacts = cached_artifacts + fetched_artifacts
    return sorted(all_artifacts, key=lambda x: x.origin)


def _sanitized_validation_message(
    exc: Exception, *, action: str, expects: Mapping[str, Any]
) -> str:
    """Render a validation failure without echoing the submitted value.

    Pydantic reports the rejected input verbatim, and repr() escaping means a
    secret can survive exact-string masking there. Both the value and the `loc`
    path are dropped: `loc` carries user-controlled mapping keys for dict-typed
    fields. Only field names the template itself declares are named back.
    """
    if not isinstance(exc, ValidationError):
        return f"Validation error for template action {action!r}."

    details: list[str] = []
    for error in exc.errors(
        include_input=False, include_context=False, include_url=False
    ):
        loc = error.get("loc") or ()
        head = loc[0] if loc else None
        field = head if isinstance(head, str) and head in expects else "<input>"
        details.append(f"{field}: {error.get('msg', 'invalid')} [{error.get('type')}]")

    joined = "; ".join(details) or "invalid input"
    return f"Validation error for template action {action!r}: {joined}"


async def _prepare_step_context(
    step_action: str,
    evaluated_args: dict[str, Any],
    parent_resolved: ResolvedContext,
    input: RunActionInput,
    role: Role,
) -> ResolvedContext:
    """Prepare ResolvedContext for a template step, reusing parent secrets.

    This avoids re-fetching secrets for each step - they're already available
    from the parent template's prepare_resolved_context() call which fetches
    all secrets recursively.
    """
    # Ensure organization_id is set (workflows are always org-scoped)
    if role.organization_id is None:
        raise ValueError("organization_id is required for template step execution")

    # Resolve action implementation via registry resolver (O(1) manifest-based lookup)
    action_impl = await registry_resolver.resolve_action(
        step_action, input.registry_lock, role.organization_id
    )

    # Mint a fresh step token while retaining the root action's provenance.
    executor_token = _mint_action_executor_token(input, role)

    # Reuse parent secrets/variables, use pre-evaluated args
    return ResolvedContext(
        secrets=parent_resolved.secrets,  # Reuse - already fetched recursively
        variables=parent_resolved.variables,  # Reuse
        action_impl=action_impl,
        evaluated_args=evaluated_args,  # Already evaluated against template context
        workspace_id=parent_resolved.workspace_id,
        workflow_id=parent_resolved.workflow_id,
        run_id=parent_resolved.run_id,
        executor_token=executor_token,  # Mint new token for step
        logical_time=parent_resolved.logical_time,
        secret_projection=parent_resolved.secret_projection,
        tunnel_name=parent_resolved.tunnel_name,
        tunnel_proxy_url=parent_resolved.tunnel_proxy_url,
        tunnel_gateway_ip=parent_resolved.tunnel_gateway_ip,
        tunnel_gateway_port=parent_resolved.tunnel_gateway_port,
    )


async def _invoke_template_step(
    *,
    backend: ExecutorBackend,
    resolved_context: ResolvedContext,
    input: RunActionInput,
    ctx: DispatchActionContext,
    timeout: float,
    provenance: ProvenanceMap,
    step_ref: str,
    step_action: str,
) -> Any:
    """Run one template step and sever unsafe exception chains.

    Built inside the handler, raised after it: the original can carry
    plaintext and a chained __cause__/__context__ is re-serialized downstream.
    """
    try:
        return await _invoke_step(
            backend=backend,
            resolved_context=resolved_context,
            input=input,
            ctx=ctx,
            timeout=timeout,
            provenance=provenance,
        )
    except ExecutionError as e:
        if e.info is None:
            raise
        classification = chained_error_classification(e)
        info = _sanitize_error_info(e.info, classification)
        error = ExecutionError(
            info=info,
            classification=classification,
            sentry_capture=e.sentry_capture,
        )
    except Exception as e:
        logger.error(
            "Template step failed",
            step_ref=step_ref,
            step_action=step_action,
            error_type=type(e).__name__,
        )
        info = ExecutorActionErrorInfo.from_exc(e, action_name=step_action)
        classification = chained_error_classification(e)
        info = _sanitize_error_info(info, classification)
        error = ExecutionError(
            info=info,
            classification=classification,
            sentry_capture=(
                capture_activity_failure(e, classification)
                if classification is not None
                else None
            ),
        )
    raise error


async def _execute_template_action(
    backend: ExecutorBackend,
    input: RunActionInput,
    ctx: DispatchActionContext,
    resolved_context: ResolvedContext,
    timeout: float,
    provenance: ProvenanceMap,
) -> Any:
    """Execute a template action by orchestrating its steps.

    This function handles template execution at the service layer, allowing
    template actions to work with any backend (including sandboxed backends).

    Each step becomes a separate backend.execute() call with its own ResolvedContext.
    Secrets are reused from the parent context (already fetched recursively).

    Args:
        backend: The executor backend to use for step execution
        input: The original RunActionInput
        ctx: Dispatch context containing the role
        resolved_context: Pre-resolved context with secrets and template definition
        timeout: The action's own timeout, unclamped. invoke_once enforces it
            across all of the template's steps.
        provenance: Authored input-source and secret-path mapping for this invocation

    Returns:
        The evaluated returns expression result
    """
    role = ctx.role

    # Parse template definition from resolved context
    template_def_dict = resolved_context.action_impl.template_definition
    if not template_def_dict:
        raise ValueError("Template action missing template_definition")

    template_def = TemplateActionDefinition.model_validate(template_def_dict)

    # Validate input args against the template's expects schema
    # This applies defaults and validates types (including enums)
    validated_input_args: dict[str, Any] = {}
    validation_error: RegistryValidationError | None = None
    if template_def.expects:
        try:
            args_model = create_expectation_model(
                template_def.expects, model_name=f"{template_def.action}Args"
            )
            validated = args_model.model_validate(resolved_context.evaluated_args)
            validated_input_args = validated.model_dump(mode="json")
        except Exception as e:
            validation_error = RegistryValidationError(
                _sanitized_validation_message(
                    e, action=template_def.action, expects=template_def.expects
                ),
                key=template_def.action,
            )
    else:
        validated_input_args = dict(resolved_context.evaluated_args)
    # Raised outside the handler: the pydantic original echoes the rejected
    # value, and a chained __cause__/__context__ is re-serialized downstream.
    if validation_error is not None:
        raise validation_error

    # Defaults enter the runtime input mapping during validation rather than
    # through the caller's action arguments. Seed their authored source here so
    # policy-aware child steps can redact or preserve them like explicit inputs.
    default_source_args: dict[str, Any] = {
        parameter: field.default
        for parameter, field in template_def.expects.items()
        if parameter not in provenance and field.has_default()
    }
    if default_source_args:
        provenance = {
            **provenance,
            **build_provenance(default_source_args, provenance),
        }

    # Build template context for expression evaluation.
    secrets_context = resolved_context.secrets
    env_context = input.exec_context.get("ENV", DSLEnvironment())
    vars_context = resolved_context.variables

    template_context = TemplateExecutionContext(
        SECRETS=secrets_context,
        ENV=env_context,
        VARS=vars_context,
        inputs=validated_input_args,
        steps={},
    )
    logger.info(
        "Executing template action via backend",
        action=template_def.action,
        steps=len(template_def.steps),
    )

    # Execute each step
    for step in template_def.steps:
        logger.trace(
            "Executing template step",
            step_ref=step.ref,
            step_action=step.action,
        )

        evaled_args = resolve_action_args(
            step.action,
            step.args,
            template_context,
            provenance,
        )

        # Prepare step context (reuses parent secrets, no re-fetch)
        step_resolved = await _prepare_step_context(
            step_action=step.action,
            evaluated_args=evaled_args,
            parent_resolved=resolved_context,
            input=input,
            role=role,
        )

        # Nested templates receive provenance derived in this scope.
        child_provenance = (
            build_provenance(step.args, provenance)
            if step_resolved.action_impl.type == "template"
            else {}
        )

        # Execute step via _invoke_step (handles nested templates)
        step_result = await _invoke_template_step(
            backend=backend,
            resolved_context=step_resolved,
            input=input,
            ctx=ctx,
            timeout=timeout,
            provenance=child_provenance,
            step_ref=step.ref,
            step_action=step.action,
        )

        # Store step result for subsequent steps (materialized for expression access)
        template_context["steps"][step.ref] = TaskResult.from_result(
            step_result
        ).to_materialized_dict()
        logger.trace("Template step completed", step_ref=step.ref)

    # Evaluate returns expression with final template context
    return eval_templated_object(
        template_def.returns, operand=template_context, provenance=provenance
    )


async def _invoke_step(
    backend: ExecutorBackend,
    resolved_context: ResolvedContext,
    input: RunActionInput,
    ctx: DispatchActionContext,
    timeout: float,
    provenance: ProvenanceMap,
) -> Any:
    """Execute a template step. Skips masking (done at root level).

    This function handles both UDF and nested template actions.
    For templates, it recurses into _execute_template_action.
    For UDFs, it delegates to the backend.

    Args:
        backend: The executor backend to use
        resolved_context: Pre-resolved context for the step
        input: The original RunActionInput
        ctx: Dispatch context containing the role
        timeout: The action's own timeout, unclamped. Each UDF subprocess call
            is capped at the deployment's maximum.
        provenance: Authored input-source and secret-path mapping for this invocation

    Returns:
        The step execution result (unmasked)
    """
    match resolved_context.action_impl.type:
        case "template":
            # Nested template - recurse
            return await _execute_template_action(
                backend=backend,
                input=input,
                ctx=ctx,
                resolved_context=resolved_context,
                timeout=timeout,
                provenance=provenance,
            )
        case "udf":
            # Leaf node - execute via backend
            call_timeout = _udf_call_timeout(resolved_context, timeout)
            result = await backend.execute(
                input=input,
                role=ctx.role,
                resolved_context=resolved_context,
                timeout=call_timeout,
            )
            if isinstance(result, ExecutorResultSuccess):
                # Opaque code can transform credentials without an AST observer.
                # Register its output before a later template expression can
                # expose it. Steps inherit the parent's secret environment, so
                # literal arguments alone do not make their results public.
                if (masks := ctx_secret_masks.get()) is not None and (
                    masks.contains(resolved_context.evaluated_args)
                    or masks.contains(resolved_context.secrets)
                    or (
                        resolved_context.secret_projection is not None
                        and masks.contains(resolved_context.secret_projection.env)
                    )
                ):
                    masks.observe(result.result, include_keys=True)
                return result.result
            else:
                # Error response from backend
                error_data = result.error
                exec_result = ExecutorActionErrorInfo.model_validate(error_data)
                if (
                    exec_result.type == ActionTimeoutError.__name__
                    and call_timeout < timeout
                ):
                    # The deployment cap, not the action's timeout, ended the call.
                    exec_result.message = _timeout_message(
                        timeout, capped_at=call_timeout
                    )
                raise ExecutionError(info=exec_result)
        case _:
            raise ValueError(
                f"Unknown action type: {resolved_context.action_impl.type}"
            )


def _action_timeout(input: RunActionInput) -> float:
    """Return the action's own timeout, unclamped.

    A timeout of 0 or less falls back to the deployment's maximum.
    """
    requested = input.task.retry_policy.timeout
    return requested if requested > 0 else config.TRACECAT__EXECUTOR_CLIENT_TIMEOUT


def _udf_call_timeout(resolved_context: ResolvedContext, timeout: float) -> float:
    """Cap one UDF subprocess call at the deployment's maximum.

    run_python runs in its own sandbox and keeps the action's own timeout.
    """
    if resolved_context.action_impl.action_name == PlatformAction.RUN_PYTHON:
        return timeout
    return min(timeout, config.TRACECAT__EXECUTOR_CLIENT_TIMEOUT)


def _timeout_message(action_timeout: float, *, capped_at: float | None = None) -> str:
    """Describe an action timeout by the action's own timeout, or the cap that hit."""
    if capped_at is None:
        return f"Action execution timed out after {action_timeout:g}s"
    return (
        f"Action execution timed out after {capped_at:g}s, this deployment's maximum "
        "action timeout (TRACECAT__EXECUTOR_CLIENT_TIMEOUT). The action's own timeout "
        f"is {action_timeout:g}s."
    )


def _attach_loop_context(info: ExecutorActionErrorInfo, iteration: int | None) -> None:
    """Record which for_each iteration failed, when running inside one."""
    if iteration is None:
        return
    # Keep the iteration number without copying the whole runtime loop value.
    info.loop_iteration = iteration


@dataclass
class PreparedContext:
    """Context prepared for execution, including resolved secrets and masking info."""

    resolved_context: ResolvedContext
    mask_values: set[str] | None
    provenance: ProvenanceMap | None = None


# A for_each loop re-resolves its shared context at least this often. OAuth
# tokens are refreshed once under 5 minutes remain (OAuthIntegration.needs_refresh),
# so every item still starts with at least 4 minutes left on its token. This
# also bounds how long a loop runs on a secret or variable edited mid-loop.
_LOOP_CONTEXT_MAX_AGE = timedelta(minutes=1)
# Re-resolve this long before a projected temporary credential (e.g. an STS
# session) expires, so an item never starts with one about to lapse.
_CREDENTIAL_EXPIRY_MARGIN = timedelta(minutes=5)


@dataclass(frozen=True, slots=True)
class SharedActionContext:
    """Action context that is the same for every for_each item.

    Covers the action implementation, argument plan, secrets (including OAuth
    tokens), workspace variables, the secret projection (including STS
    credentials) and the tunnel route. None of these read the loop variables.
    """

    action_impl: ActionImplementation
    argument_plan: ActionArgumentPlan
    provenance: ProvenanceMap | None
    secrets: dict[str, Any]
    variables: dict[str, dict[str, str]]
    secret_projection: SecretEnvProjection
    mask_values: set[str] | None
    tunnel_route: TunnelRoute | None
    refresh_at: datetime
    """When a loop must stop reusing this context and resolve a fresh one."""


async def resolve_shared_action_context(
    input: RunActionInput,
    role: Role,
) -> SharedActionContext:
    """Resolve everything an action needs that does not depend on its arguments.

    Fetches secrets, OAuth tokens, workspace variables, the secret projection
    and the tunnel route. Reads nothing from `input.exec_context`, so a
    for_each loop can share the result across items.
    """
    # Ensure organization_id is set (workflows are always org-scoped)
    if role.organization_id is None:
        raise ValueError("organization_id is required for action execution")
    if role.workspace_id is None:
        raise ValueError("workspace_id is required for action execution")

    task = input.task
    action_name = task.action

    # Resolve action implementation and secrets via registry resolver (O(1) manifest-based lookup)
    action_impl = await registry_resolver.resolve_action(
        action_name, input.registry_lock, role.organization_id
    )
    action_secrets = await registry_resolver.collect_action_secrets_from_manifest(
        action_name, input.registry_lock, role.organization_id
    )

    # Apply field policy before expression collection: preserved source is
    # excluded, while secret-dependent occurrences in durable content are
    # replaced before any argument-driven secret lookup.
    argument_plan = ActionArgumentPlan.build(action_name, task.args)
    provenance = build_provenance(task.args) if action_impl.type == "template" else None
    collected = collect_expressions(argument_plan.evaluable)

    # Fetch secrets and variables
    secrets = await secrets_manager.get_action_secrets(
        secret_exprs=collected.secrets, action_secrets=action_secrets
    )
    workspace_variables = await get_workspace_variables(
        variable_exprs=collected.variables,
        environment=input.run_context.environment,
        role=role,
    )

    # Derive masks from the raw secrets before they can reach an exception.
    early_mask_values = (
        set()
        if config.TRACECAT__UNSAFE_DISABLE_SM_MASKING
        else collect_mask_values([secrets])
    )

    # Error sanitization in invoke_once() redacts what this collector observed.
    if masks := ctx_secret_masks.get():
        masks.observe(secrets)

    # Preprocessors handle live credentials (e.g. AWS STS), so their errors can
    # carry secret-derived values. invoke_once() has no mask set until the
    # prepared context is returned, so fail closed here instead.
    secret_projection = await await_with_masked_errors(
        project_secret_env(secrets=secrets, role=role, run_context=input.run_context),
        masks=early_mask_values,
    )

    # Build root-level masks from the runtime projection so host-side credential
    # rewriting is also redacted from final outputs.
    if config.TRACECAT__UNSAFE_DISABLE_SM_MASKING:
        logger.warning(
            "Secrets masking is disabled. This is unsafe in production workflows."
        )
        mask_values = None
    else:
        mask_values = early_mask_values | set(secret_projection.mask_values)

    tunnel_route = None
    if tunnel_name := effective_tunnel(input):
        if PlatformAction.is_interface(action_name):
            raise TunnelRouteError(f"Action {action_name!r} cannot use a tunnel")
        tunnel_route = await resolve_tunnel_route(
            name=tunnel_name,
            workspace_id=role.workspace_id,
            organization_id=role.organization_id,
            subject=(
                f"agent_session:{input.agent_session_id}"
                if input.agent_session_id is not None
                else str(input.run_context.wf_exec_id)
            ),
            action_ref=task.ref,
        )

    refresh_at = datetime.now(UTC) + _LOOP_CONTEXT_MAX_AGE
    if (expires_at := secret_projection.expires_at) is not None:
        refresh_at = min(refresh_at, expires_at - _CREDENTIAL_EXPIRY_MARGIN)

    return SharedActionContext(
        action_impl=action_impl,
        argument_plan=argument_plan,
        provenance=provenance,
        secrets=secrets,
        variables=workspace_variables,
        secret_projection=secret_projection,
        mask_values=mask_values,
        tunnel_route=tunnel_route,
        refresh_at=refresh_at,
    )


def bind_action_context(
    shared: SharedActionContext,
    input: RunActionInput,
    role: Role,
) -> PreparedContext:
    """Evaluate one invocation's arguments against a shared action context.

    Registers every secret and projected credential with the invocation's
    mask collector and mints a fresh executor token.
    """
    task = input.task

    # Build execution context for SDK calls (uses raw secrets for expression eval)
    context = input.exec_context.copy()
    context["SECRETS"] = shared.secrets
    context["VARS"] = shared.variables

    if masks := ctx_secret_masks.get():
        masks.observe(shared.secrets)
        for value in shared.secret_projection.mask_values:
            masks.observe(value)

    # Extract and set logical_time BEFORE evaluating args
    # This ensures FN.now(), FN.utcnow(), FN.today() use the deterministic time
    env_context = context.get(ExprContext.ENV) or {}
    workflow_context = env_context.get("workflow") or {}
    logical_time = workflow_context.get("logical_time")
    logger.trace(
        "Extracting logical_time from context",
        task_ref=task.ref,
        logical_time_raw=logical_time,
    )
    if logical_time is not None and isinstance(logical_time, str):
        # logical_time may be serialized as ISO string through Temporal
        logical_time = datetime.fromisoformat(logical_time)
    logical_time_token = ctx_logical_time.set(logical_time)
    try:
        logger.trace(
            "Context set before template evaluation",
            task_ref=task.ref,
            logical_time=logical_time,
        )
        # Expression errors echo their operand, so the raw secret can be in the
        # message: failures surface as a chainless masked copy.
        evaluated_args = call_with_masked_errors(
            lambda: shared.argument_plan.evaluate(context),
            masks=shared.mask_values or (),
        )
    finally:
        ctx_logical_time.reset(logical_time_token)

    # Generate executor token for SDK authentication
    executor_token = _mint_action_executor_token(input, role)

    tunnel_route = shared.tunnel_route
    resolved_context = ResolvedContext(
        secrets=shared.secrets,
        variables=shared.variables,
        action_impl=shared.action_impl,
        evaluated_args=dict(evaluated_args),
        workspace_id=str(role.workspace_id),
        workflow_id=str(input.run_context.wf_id),
        run_id=str(input.run_context.wf_run_id),
        executor_token=executor_token,
        logical_time=logical_time,
        secret_projection=shared.secret_projection,
        tunnel_name=tunnel_route.tunnel_name if tunnel_route else None,
        tunnel_proxy_url=tunnel_route.proxy_url if tunnel_route else None,
        tunnel_gateway_ip=tunnel_route.gateway_ip if tunnel_route else None,
        tunnel_gateway_port=tunnel_route.gateway_port if tunnel_route else None,
    )

    return PreparedContext(
        resolved_context=resolved_context,
        mask_values=shared.mask_values,
        provenance=shared.provenance,
    )


async def prepare_resolved_context(
    input: RunActionInput,
    role: Role,
) -> PreparedContext:
    """Prepare all context needed for action execution.

    This resolves secrets, variables, action implementation, and evaluated args
    once at the service layer. The resulting ResolvedContext is passed to backends
    for execution without requiring DB access in the sandbox.

    Returns:
        PreparedContext containing ResolvedContext and mask_values for post-processing.
    """
    shared = await resolve_shared_action_context(input, role)
    return bind_action_context(shared, input, role)


@dataclass(slots=True)
class LoopActionContext:
    """Shares one resolved action context across a for_each loop's items.

    The first item resolves it; later items reuse it until `refresh_at`, then
    the next item resolves a fresh one. A failed resolution is not cached, so
    each item that needs the context retries it, as it did before sharing.
    """

    role: Role
    _shared: SharedActionContext | None = None
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def prepare(self, input: RunActionInput) -> PreparedContext:
        """Prepare one item's context, resolving the shared part when stale."""
        async with self._lock:
            shared = self._shared
            if shared is None or datetime.now(UTC) >= shared.refresh_at:
                shared = await resolve_shared_action_context(input, self.role)
                self._shared = shared
        return bind_action_context(shared, input, self.role)


async def invoke_once(
    backend: ExecutorBackend,
    input: RunActionInput,
    ctx: DispatchActionContext,
    iteration: int | None = None,
    loop_context: LoopActionContext | None = None,
) -> ExecutionResult:
    """Execute action using the configured backend.

    The backend is selected via TRACECAT__EXECUTOR_BACKEND config:
    - 'nsjail' ('ephemeral' alias): Cold nsjail subprocess per action (multitenant, full isolation)
    - 'direct': Direct subprocess execution

    A for_each item passes its loop's `loop_context` to reuse the secrets,
    variables and credentials resolved for earlier items.
    """
    role = ctx.role
    action_name = input.task.action
    # Budgets the action's function calls; platform preparation is excluded.
    timeout = _action_timeout(input)

    # Ensure organization_id is set (workflows are always org-scoped)
    if role.organization_id is None:
        raise ValueError("organization_id is required for action dispatch")

    # Bound before the try so context-preparation failures stay safe.
    mask_values: set[str] | None = None

    masks = SecretMaskCollector()
    masks_token = ctx_secret_masks.set(masks)
    try:
        # Prefetch registry lock manifests into cache for O(1) resolution.
        # Keep this inside the error wrapper so entitlement failures are
        # normalized into ExecutionError and then ApplicationError at activity
        # boundaries.
        await registry_resolver.prefetch_lock(input.registry_lock, role.organization_id)

        # Prepare resolved context (secrets, variables, action impl, evaluated args)
        # This is done once here and passed to all backends
        # For templates, secrets are fetched recursively for all steps
        if loop_context is None:
            prepared = await prepare_resolved_context(input, role)
        else:
            prepared = await loop_context.prepare(input)
        resolved_context = prepared.resolved_context
        mask_values = prepared.mask_values
        for value in mask_values or ():
            masks.observe(value)

        # Set logical_time for deterministic FN.now() (applies to in-process backends)
        # Sandboxed backends set this in their subprocess from resolved_context.logical_time
        ctx_logical_time.set(resolved_context.logical_time)

        # A template is one action, so all its steps share the action's
        # timeout; cancelling the running step terminates its subprocess. A
        # single UDF call is timed inside the runner instead, so subprocess
        # startup doesn't count toward it.
        template_scope = asyncio.timeout(
            timeout if resolved_context.action_impl.type == "template" else None
        )
        try:
            async with template_scope:
                # Delegate execution to _invoke_step (shared logic for template/udf)
                # This handles both template orchestration and UDF execution
                action_result = await _invoke_step(
                    backend=backend,
                    resolved_context=resolved_context,
                    input=input,
                    ctx=ctx,
                    timeout=timeout,
                    provenance=prepared.provenance or {},
                )
        except TimeoutError:
            # A TimeoutError raised by a step itself propagates unchanged.
            if template_scope.expired():
                raise ActionTimeoutError(_timeout_message(timeout)) from None
            raise
        # Synchronous work after the last await can cross the deadline before
        # the scope fires. A template that finishes late is still a timeout.
        deadline = template_scope.when()
        if deadline is not None and asyncio.get_running_loop().time() >= deadline:
            raise ActionTimeoutError(_timeout_message(timeout))

    except ExecutionError as e:
        # ExecutionError already has proper error info, just add loop context if needed
        if e.info is None:
            raise
        classification = chained_error_classification(e)
        exec_result = _sanitize_error_info(e.info, classification).model_copy()
        _attach_loop_context(exec_result, iteration)
        safe_error = ExecutionError(
            info=exec_result,
            classification=classification,
            sentry_capture=e.sentry_capture,
        )
    except Exception as e:
        # Infrastructure errors need to be wrapped for consistent error handling
        exec_result = ExecutorActionErrorInfo.from_exc(e, action_name=action_name)
        classification = chained_error_classification(e)
        _attach_loop_context(exec_result, iteration)
        exec_result = _sanitize_error_info(exec_result, classification)
        # Log only the safe diagnostic when secrets may be in scope.
        logger.error(
            "Backend execution failed",
            action=action_name,
            error=exec_result.message,
            error_type=exec_result.type,
            backend=type(backend).__name__,
        )
        # Drop the cause AND the context even with no masks: secret-derived
        # ACTIONS/var inputs mean the original can carry plaintext, and a
        # chained __cause__/__context__ is re-serialized downstream.
        safe_error = ExecutionError(
            info=exec_result,
            classification=classification,
            sentry_capture=(
                capture_activity_failure(e, classification)
                if classification is not None
                else None
            ),
        )
    else:
        # Apply secret masking at root level only. Large action results can make
        # this CPU-bound traversal expensive, so keep it off the activity event
        # loop where it could otherwise delay heartbeats for every activity on
        # the worker.
        if mask_values:
            action_result = await asyncio.to_thread(
                apply_masks_object, action_result, masks=mask_values
            )
        return action_result
    finally:
        ctx_secret_masks.reset(masks_token)
    # Raise outside the handlers so the plaintext original is not attached.
    raise safe_error


async def dispatch_action(backend: ExecutorBackend, input: RunActionInput) -> Any:
    """Dispatch action for execution.

    This function handles dispatching actions to be executed. It supports
    both single action execution and parallel execution using for_each loops.

    Called by:
    - ExecutorActivities.execute_action_activity (Temporal activity on shared-action-queue)

    Args:
        input: The RunActionInput containing the task definition and execution context

    Returns:
        Any: For single actions, returns the ExecutionResult. For for_each loops, returns
             a list of results from all parallel executions.

    Raises:
        TracecatException: If there are errors evaluating for_each expressions or during execution
        ExecutionError: If there are errors from the executor itself
        LoopExecutionError: If there are errors in for_each loop execution
    """
    role = ctx_role.get()
    if role is None:
        raise ValueError("Role is required to dispatch actions")
    ctx = DispatchActionContext(role=role)
    task = input.task

    # Enforce action execution scope
    # This check ensures the user has permission to execute this specific action
    # Scope matching supports wildcards (e.g., action:core.*:execute, action:*:execute)
    require_action_scope(task.action)

    logger.info("Preparing runtime environment", ctx=ctx)
    # If there's no for_each, execute normally
    if not task.for_each:
        return await invoke_once(backend, input, ctx)

    logger.info("Running for_each on action in parallel", action=task.action)

    # Handle for_each by creating bounded parallel executions
    base_context = input.exec_context
    # We have a list of iterators that give a variable assignment path ".path.to.value"
    # and a collection of values as a tuple.
    iterators = get_iterables_from_expression(expr=task.for_each, operand=base_context)

    # Items share secrets, variables and credentials; only argument evaluation,
    # token minting and masking run per item.
    loop_context = LoopActionContext(role=role)
    max_concurrency = max(1, config.TRACECAT__EXECUTOR_FOR_EACH_MAX_CONCURRENCY)
    # Use a fixed worker pool instead of a semaphore around one task per loop item.
    # A semaphore would cap active invokes, but still schedules every iteration and
    # retains per-item task state for large loops. Workers pull lazily from the
    # iterator, so memory and event-loop pressure scale with max_concurrency.
    # Keep this loop-local; cross-activity caps must not use asyncio primitives
    # because activities can run on different event loops.
    loop_items = iter(enumerate(zip(*iterators, strict=False)))
    results: dict[int, ExecutionResult] = {}
    loop_errors: dict[int, ExecutionError] = {}
    iteration_count = 0

    async def worker() -> None:
        nonlocal iteration_count

        while True:
            try:
                i, items = next(loop_items)
            except StopIteration:
                return

            iteration_count += 1
            new_context = base_context.copy()
            # Patch each loop variable
            for iterator_path, iterator_value in items:
                patch_object(
                    obj=cast(MutableMapping[str, Any], new_context),
                    path=ExprContext.LOCAL_VARS + iterator_path,
                    value=iterator_value,
                )
            # Create a new task with the patched context
            new_input = input.model_copy(update={"exec_context": new_context})
            try:
                results[i] = await invoke_once(
                    backend, new_input, ctx, iteration=i, loop_context=loop_context
                )
            except ExecutionError as e:
                loop_errors[i] = e

    try:
        async with asyncio.TaskGroup() as tg:
            for _ in range(max_concurrency):
                tg.create_task(worker())
    except* ExecutionError as eg:
        grouped_loop_errors = flatten_wrapped_exc_error_group(eg)
        raise LoopExecutionError(grouped_loop_errors) from eg
    except* Exception as eg:
        errors = [str(x) for x in eg.exceptions]
        logger.error("Unexpected error(s) in loop", errors=errors, exc_group=eg)
        raise TracecatException(
            (
                f"\n[{context_locator(task, 'for_each')}]"
                "\n\nUnexpected error(s) in loop:"
                f"\n\n{'\n\n'.join(errors)}"
                "\n\nPlease ensure that the loop is iterable and that the loop variable has the correct type."
            ),
            detail={"errors": errors},
        ) from eg
    if loop_errors:
        raise LoopExecutionError([loop_errors[i] for i in sorted(loop_errors)])
    return [results[i] for i in range(iteration_count)]


"""Utilities"""


def evaluate_templated_args(task: ActionStatement, context: ExecutionContext) -> ArgsT:
    return cast(ArgsT, eval_templated_object(task.args, operand=context))


def patch_object(
    obj: MutableMapping[str, Any], *, path: str, value: Any, sep: str = "."
) -> None:
    *stem, leaf = path.split(sep=sep)
    for key in stem:
        obj = obj.setdefault(key, {})
    obj[leaf] = value


def iter_for_each(
    task: ActionStatement,
    context: ExecutionContext,
    *,
    assign_context: ExprContext = ExprContext.LOCAL_VARS,
    patch: bool = True,
) -> Iterator[ArgsT]:
    """Yield patched contexts for each loop iteration."""
    # Evaluate the loop expression
    if not task.for_each:
        raise ValueError("No loop expression found")
    iterators = get_iterables_from_expression(expr=task.for_each, operand=context)

    # Patch the context with the loop item and evaluate the action-local expressions
    # We're copying this so that we don't pollute the original context
    # Currently, the only source of action-local expressions is the loop iteration
    # In the future, we may have other sources of action-local expressions
    # XXX: ENV is the only context that should be shared
    patched_context = context.copy() if patch else create_default_execution_context()

    # Create a generator that zips the iterables together
    for i, items in enumerate(zip(*iterators, strict=False)):
        logger.trace("Loop iteration", iteration=i)
        for iterator_path, iterator_value in items:
            patch_object(
                obj=cast(MutableMapping[str, Any], patched_context),
                path=assign_context + iterator_path,
                value=iterator_value,
            )
        patched_args = evaluate_templated_args(task=task, context=patched_context)
        yield patched_args


def flatten_wrapped_exc_error_group(
    eg: BaseExceptionGroup[ExecutionError] | ExecutionError,
) -> list[ExecutionError]:
    """Flattens an ExceptionGroup or single exception into a list of exceptions.

    Args:
        eg: Either an ExceptionGroup containing exceptions of type T, or a single exception of type T

    Returns:
        A list of exceptions of type T extracted from the ExceptionGroup or containing just the single exception
    """
    if isinstance(eg, BaseExceptionGroup):
        return list(
            itertools.chain.from_iterable(
                flatten_wrapped_exc_error_group(e) for e in eg.exceptions
            )
        )
    return [eg]


async def get_workspace_variables(
    variable_exprs: set[str],
    *,
    environment: str | None = None,
    role: Role | None = None,
) -> dict[str, dict[str, str]]:
    try:
        async with VariablesService.with_session(role=role) as service:
            variables = await service.search_variables(
                VariableSearch(names=variable_exprs, environment=environment)
            )
    except TracecatAuthorizationError as e:
        logger.warning("No access to workspace variables", error=e)
        return {}
    return {variable.name: variable.values for variable in variables}
