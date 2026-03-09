from __future__ import annotations

import asyncio
import itertools
from collections.abc import Iterator, Mapping, MutableMapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, cast

from aiocache import Cache
from sqlalchemy import and_, or_, select, union_all

from tracecat import config
from tracecat.auth.executor_tokens import mint_executor_token
from tracecat.auth.types import Role
from tracecat.authz.controls import require_action_scope
from tracecat.concurrency import GatheringTaskGroup
from tracecat.contexts import (
    ctx_interaction,
    ctx_logical_time,
    ctx_role,
)
from tracecat.db.engine import get_async_session_bypass_rls_context_manager
from tracecat.db.models import (
    PlatformRegistryRepository,
    PlatformRegistryVersion,
    RegistryRepository,
    RegistryVersion,
)
from tracecat.dsl.common import context_locator, create_default_execution_context
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
from tracecat.executor.schemas import (
    ExecutorActionErrorInfo,
    ExecutorResultSuccess,
    ResolvedContext,
)
from tracecat.expressions.common import ExprContext, ExprOperand
from tracecat.expressions.eval import (
    collect_expressions,
    eval_templated_object,
    get_iterables_from_expression,
)
from tracecat.expressions.expectations import create_expectation_model
from tracecat.identifiers import OrganizationID
from tracecat.logger import logger
from tracecat.parse import traverse_leaves
from tracecat.registry.actions.bound import BoundRegistryAction
from tracecat.registry.actions.schemas import TemplateActionDefinition
from tracecat.registry.actions.service import RegistryActionsService
from tracecat.registry.constants import DEFAULT_REGISTRY_ORIGIN
from tracecat.secrets import secrets_manager
from tracecat.secrets.common import apply_masks_object
from tracecat.variables.schemas import VariableSearch
from tracecat.variables.service import VariablesService

"""All these methods are used in the registry executor, not on the worker"""


type ArgsT = Mapping[str, Any]
type ExecutionResult = Any | ExecutorActionErrorInfo


@dataclass
class DispatchActionContext:
    role: Role


@dataclass
class RegistryArtifactsContext:
    origin: str
    version: str
    tarball_uri: str


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
            origin_val, version_val, tarball_uri = row
            if tarball_uri is not None:
                artifact = RegistryArtifactsContext(
                    origin=origin_val,
                    version=version_val,
                    tarball_uri=tarball_uri,
                )
                fetched_artifacts.append(artifact)
                found_keys.add((origin_val, version_val))
                # Cache the artifact
                key = _artifact_cache_key(origin_val, version_val, organization_id)
                await _artifact_cache.set(key=key, value=artifact)
            else:
                logger.warning(
                    "Registry version found but missing tarball_uri",
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


async def _run_action_direct(*, action: BoundRegistryAction, args: ArgsT) -> Any:
    """Execute the UDF directly.

    At this point, the UDF cannot be a template.
    """
    if action.is_template:
        # This should not be reachable
        raise ValueError("Templates cannot be executed directly")

    validated_args = action.validate_args(args=args, mode="python")
    try:
        if action.is_async:
            logger.trace("Running UDF async")
            return await action.fn(**validated_args)
        logger.trace("Running UDF sync")
        return await asyncio.to_thread(action.fn, **validated_args)
    except Exception as e:
        logger.error(
            f"Error running UDF {action.action!r}", error=e, type=type(e).__name__
        )
        raise e


async def run_template_action(
    *,
    action: BoundRegistryAction,
    args: ArgsT,
    context: ExecutionContext | None = None,
) -> Any:
    """Handle template execution."""
    if not action.template_action:
        raise ValueError(
            "Attempted to run a non-template UDF as a template. "
            "UDFs should be executed directly via the executor backend."
        )
    defn = action.template_action.definition

    # Validate arguments and apply defaults
    logger.trace(
        "Validating template action arguments", expects=defn.expects, args=args
    )
    validated_args: dict[str, Any] = {}
    if defn.expects:
        validated_args = action.validate_args(args=args)

    secrets_context = {}
    env_context = DSLEnvironment()
    vars_context = {}
    if context is not None:
        secrets_context = context.get("SECRETS", {})
        env_context = context.get("ENV", DSLEnvironment())
        vars_context = context.get("VARS", {})

    template_context = TemplateExecutionContext(
        SECRETS=secrets_context,
        ENV=env_context,
        VARS=vars_context,
        inputs=validated_args,
        steps={},
    )
    logger.info("Running template action", action=defn.action)
    return await _run_template_steps(defn, template_context)


async def _run_template_steps(
    defn: TemplateActionDefinition, template_context: TemplateExecutionContext
) -> Any:
    for step in defn.steps:
        evaled_args = cast(
            ArgsT,
            eval_templated_object(
                step.args, operand=cast(ExprOperand[str], template_context)
            ),
        )
        async with RegistryActionsService.with_session() as service:
            step_action = await service.load_action_impl(
                action_name=step.action, mode="execution"
            )
        logger.trace("Running action step", step_action=step_action.action)
        result = await _run_single_template_step(
            action=step_action,
            args=evaled_args,
            context=template_context,
        )
        # Store the result of the step
        logger.trace("Storing step result", step=step.ref, result=result)
        template_context["steps"][step.ref] = TaskResult.from_result(
            result
        ).to_materialized_dict()

    # Handle returns
    return eval_templated_object(
        defn.returns, operand=cast(ExprOperand[str], template_context)
    )


async def _run_single_template_step(
    *,
    action: BoundRegistryAction,
    args: ArgsT,
    context: TemplateExecutionContext,
) -> Any:
    """Run a UDF async."""
    if action.is_template:
        logger.info("Running template action async", action=action.name)
        if not action.template_action:
            raise ValueError("Template action missing template_action")
        defn = action.template_action.definition

        # Validate args against nested template's expects and create fresh context
        # with nested template's own inputs, but reuse parent's SECRETS/ENV/VARS
        validated_args: dict[str, Any] = {}
        if defn.expects:
            validated_args = action.validate_args(args=args)

        nested_context = TemplateExecutionContext(
            SECRETS=context.get("SECRETS", {}),
            ENV=context.get("ENV", DSLEnvironment()),
            VARS=context.get("VARS", {}),
            inputs=validated_args,
            steps={},
        )
        result = await _run_template_steps(defn, nested_context)
    else:
        logger.trace("Running UDF async", action=action.name)
        # Get secrets from context
        secrets = context.get("SECRETS", {})
        flat_secrets = secrets_manager.flatten_secrets(secrets)
        with secrets_manager.env_sandbox(flat_secrets):
            result = await _run_action_direct(action=action, args=args)

    return result


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

    # Mint new executor token for step (required for SDK authentication)
    if role.workspace_id is None:
        raise ValueError("workspace_id is required for template step execution")
    executor_token = mint_executor_token(
        workspace_id=role.workspace_id,
        user_id=role.user_id,
        service_id=role.service_id,
        wf_id=str(input.run_context.wf_id),
        wf_exec_id=str(input.run_context.wf_run_id),
    )

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
    )


async def _execute_template_action(
    backend: ExecutorBackend,
    input: RunActionInput,
    ctx: DispatchActionContext,
    resolved_context: ResolvedContext,
    timeout: float,
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
        timeout: Execution timeout

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
    if template_def.expects:
        try:
            args_model = create_expectation_model(
                template_def.expects, model_name=f"{template_def.action}Args"
            )
            validated = args_model.model_validate(resolved_context.evaluated_args)
            validated_input_args = validated.model_dump(mode="json")
        except Exception as e:
            raise RegistryValidationError(
                f"Validation error for template action {template_def.action!r}: {e}",
                key=template_def.action,
            ) from e
    else:
        validated_input_args = dict(resolved_context.evaluated_args)

    # Build template context for expression evaluation
    # Secrets context uses the pre-resolved secrets from parent
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

        # Evaluate step args with template context
        evaled_args = cast(
            dict[str, Any],
            eval_templated_object(step.args, operand=template_context),
        )

        # Prepare step context (reuses parent secrets, no re-fetch)
        step_resolved = await _prepare_step_context(
            step_action=step.action,
            evaluated_args=evaled_args,
            parent_resolved=resolved_context,
            input=input,
            role=role,
        )

        # Execute step via _invoke_step (handles nested templates)
        try:
            step_result = await _invoke_step(
                backend=backend,
                resolved_context=step_resolved,
                input=input,
                ctx=ctx,
                timeout=timeout,
            )
        except ExecutionError:
            # Re-raise with step context preserved
            raise
        except Exception as e:
            # Wrap other exceptions
            logger.error(
                "Template step failed",
                step_ref=step.ref,
                step_action=step.action,
                error=str(e),
            )
            raise ExecutionError(
                info=ExecutorActionErrorInfo.from_exc(e, action_name=step.action)
            ) from e

        # Store step result for subsequent steps (materialized for expression access)
        template_context["steps"][step.ref] = TaskResult.from_result(
            step_result
        ).to_materialized_dict()
        logger.trace("Template step completed", step_ref=step.ref)

    # Evaluate returns expression with final template context
    return eval_templated_object(template_def.returns, operand=template_context)


async def _invoke_step(
    backend: ExecutorBackend,
    resolved_context: ResolvedContext,
    input: RunActionInput,
    ctx: DispatchActionContext,
    timeout: float,
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
        timeout: Execution timeout

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
            )
        case "udf":
            # Leaf node - execute via backend
            result = await backend.execute(
                input=input,
                role=ctx.role,
                resolved_context=resolved_context,
                timeout=timeout,
            )
            if isinstance(result, ExecutorResultSuccess):
                return result.result
            else:
                # Error response from backend
                error_data = result.error
                exec_result = ExecutorActionErrorInfo.model_validate(error_data)
                raise ExecutionError(info=exec_result)
        case _:
            raise ValueError(
                f"Unknown action type: {resolved_context.action_impl.type}"
            )


@dataclass
class PreparedContext:
    """Context prepared for execution, including resolved secrets and masking info."""

    resolved_context: ResolvedContext
    mask_values: set[str] | None


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
    # Ensure organization_id is set (workflows are always org-scoped)
    if role.organization_id is None:
        raise ValueError("organization_id is required for action execution")

    task = input.task
    action_name = task.action

    # Resolve action implementation and secrets via registry resolver (O(1) manifest-based lookup)
    action_impl = await registry_resolver.resolve_action(
        action_name, input.registry_lock, role.organization_id
    )
    action_secrets = await registry_resolver.collect_action_secrets_from_manifest(
        action_name, input.registry_lock, role.organization_id
    )

    # Collect expressions to know what secrets/variables are needed
    collected = collect_expressions(task.args)

    # Fetch secrets and variables
    secrets = await secrets_manager.get_action_secrets(
        secret_exprs=collected.secrets, action_secrets=action_secrets
    )
    workspace_variables = await get_workspace_variables(
        variable_exprs=collected.variables,
        environment=input.run_context.environment,
        role=role,
    )

    # Build mask values for secret masking
    if config.TRACECAT__UNSAFE_DISABLE_SM_MASKING:
        logger.warning(
            "Secrets masking is disabled. This is unsafe in production workflows."
        )
        mask_values = None
    else:
        mask_values = set()
        for _, secret_value in traverse_leaves(secrets):
            if secret_value is not None:
                secret_str = str(secret_value)
                if len(secret_str) > 1:
                    mask_values.add(secret_str)
                if isinstance(secret_value, str) and len(secret_value) > 1:
                    mask_values.add(secret_value)

    # Build execution context for SDK calls
    context = input.exec_context.copy()
    context["SECRETS"] = secrets
    context["VARS"] = workspace_variables

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
    # Set interaction context for FN.get_interaction() during args evaluation
    interaction_token = ctx_interaction.set(input.interaction_context)
    try:
        logger.trace(
            "Context set before template evaluation",
            task_ref=task.ref,
            logical_time=logical_time,
            has_interaction=input.interaction_context is not None,
        )

        # Evaluate templated args (now with logical_time and interaction context set)
        evaluated_args = evaluate_templated_args(task, context)
    finally:
        ctx_logical_time.reset(logical_time_token)
        ctx_interaction.reset(interaction_token)

    if role.workspace_id is None:
        raise ValueError("workspace_id is required for action execution")

    # Generate executor token for SDK authentication
    executor_token = mint_executor_token(
        workspace_id=role.workspace_id,
        user_id=role.user_id,
        service_id=role.service_id,
        wf_id=str(input.run_context.wf_id),
        wf_exec_id=str(input.run_context.wf_run_id),
    )

    resolved_context = ResolvedContext(
        secrets=secrets,
        variables=workspace_variables,
        action_impl=action_impl,
        evaluated_args=dict(evaluated_args),
        workspace_id=str(role.workspace_id),
        workflow_id=str(input.run_context.wf_id),
        run_id=str(input.run_context.wf_run_id),
        executor_token=executor_token,
        logical_time=logical_time,
    )

    return PreparedContext(resolved_context=resolved_context, mask_values=mask_values)


async def invoke_once(
    backend: ExecutorBackend,
    input: RunActionInput,
    ctx: DispatchActionContext,
    iteration: int | None = None,
) -> ExecutionResult:
    """Execute action using the configured backend.

    The backend is selected via TRACECAT__EXECUTOR_BACKEND config:
    - 'pool': Warm nsjail workers (single-tenant, high throughput)
    - 'ephemeral': Cold nsjail subprocess per action (multitenant, full isolation)
    - 'direct': Direct subprocess execution
    - 'test': In-process execution (tests only)
    - 'auto': Auto-select based on environment
    """
    role = ctx.role
    action_name = input.task.action
    timeout = config.TRACECAT__EXECUTOR_CLIENT_TIMEOUT

    # Ensure organization_id is set (workflows are always org-scoped)
    if role.organization_id is None:
        raise ValueError("organization_id is required for action dispatch")

    try:
        # Prefetch registry lock manifests into cache for O(1) resolution.
        # Keep this inside the error wrapper so entitlement failures are
        # normalized into ExecutionError and then ApplicationError at activity
        # boundaries.
        await registry_resolver.prefetch_lock(input.registry_lock, role.organization_id)

        # Prepare resolved context (secrets, variables, action impl, evaluated args)
        # This is done once here and passed to all backends
        # For templates, secrets are fetched recursively for all steps
        prepared = await prepare_resolved_context(input, role)
        resolved_context = prepared.resolved_context
        mask_values = prepared.mask_values

        # Set logical_time for deterministic FN.now() (applies to in-process backends)
        # Sandboxed backends set this in their subprocess from resolved_context.logical_time
        ctx_logical_time.set(resolved_context.logical_time)

        # Delegate execution to _invoke_step (shared logic for template/udf)
        # This handles both template orchestration and UDF execution
        action_result = await _invoke_step(
            backend=backend,
            resolved_context=resolved_context,
            input=input,
            ctx=ctx,
            timeout=timeout,
        )

    except ExecutionError as e:
        # ExecutionError already has proper error info, just add loop context if needed
        if iteration is not None and e.info is not None:
            e.info.loop_iteration = iteration
            e.info.loop_vars = input.exec_context.get(ExprContext.LOCAL_VARS)
        raise
    except Exception as e:
        # Infrastructure errors need to be wrapped for consistent error handling
        logger.error(
            "Backend execution failed",
            action=action_name,
            error=str(e),
            error_type=type(e).__name__,
            backend=type(backend).__name__,
        )
        exec_result = ExecutorActionErrorInfo.from_exc(e, action_name=action_name)
        if iteration is not None:
            exec_result.loop_iteration = iteration
            exec_result.loop_vars = input.exec_context.get(ExprContext.LOCAL_VARS)
        raise ExecutionError(info=exec_result) from e

    # Apply secret masking at root level only
    # Steps don't mask - masking happens once here after all execution completes
    if mask_values:
        action_result = apply_masks_object(action_result, masks=mask_values)
    return action_result


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

    # Handle for_each by creating parallel executions
    base_context = input.exec_context
    # We have a list of iterators that give a variable assignment path ".path.to.value"
    # and a collection of values as a tuple.
    iterators = get_iterables_from_expression(expr=task.for_each, operand=base_context)

    tasks: list[asyncio.Task[ExecutionResult]] = []
    try:
        # Create a generator that zips the iterables together
        # Iterate over the for_each items
        async with GatheringTaskGroup() as tg:
            for i, items in enumerate(zip(*iterators, strict=False)):
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
                coro = invoke_once(backend, new_input, ctx, iteration=i)
                tasks.append(tg.create_task(coro))
        return tg.results()
    except* ExecutionError as eg:
        loop_errors = flatten_wrapped_exc_error_group(eg)
        raise LoopExecutionError(loop_errors) from eg
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
    finally:
        logger.debug("Shut down any pending tasks")
        for t in tasks:
            t.cancel()


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
