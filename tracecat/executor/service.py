from __future__ import annotations

import asyncio
import itertools
import time
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, cast

from sqlalchemy import func, select
from sqlalchemy.orm import aliased

from tracecat import config
from tracecat.auth.executor_tokens import mint_executor_token
from tracecat.auth.types import AccessLevel, Role
from tracecat.concurrency import GatheringTaskGroup
from tracecat.contexts import (
    ctx_interaction,
    ctx_logger,
    ctx_logical_time,
    ctx_role,
    ctx_run,
    ctx_session_id,
    with_session,
)
from tracecat.db.engine import get_async_session_context_manager
from tracecat.db.models import RegistryRepository, RegistryVersion
from tracecat.dsl.common import context_locator, create_default_execution_context
from tracecat.dsl.schemas import (
    ActionStatement,
    ExecutionContext,
    RunActionInput,
    TaskResult,
)
from tracecat.exceptions import (
    ExecutionError,
    LoopExecutionError,
    TracecatAuthorizationError,
    TracecatException,
)
from tracecat.executor.action_runner import get_action_runner
from tracecat.executor.schemas import ExecutorActionErrorInfo, ExecutorResultSuccess
from tracecat.expressions.common import ExprContext, ExprOperand
from tracecat.expressions.eval import (
    collect_expressions,
    eval_templated_object,
    get_iterables_from_expression,
)
from tracecat.feature_flags import is_feature_enabled
from tracecat.feature_flags.enums import FeatureFlag
from tracecat.logger import logger
from tracecat.parse import traverse_leaves
from tracecat.registry.actions.schemas import BoundRegistryAction
from tracecat.registry.actions.service import RegistryActionsService
from tracecat.secrets import secrets_manager
from tracecat.secrets.common import apply_masks_object
from tracecat.variables.schemas import VariableSearch
from tracecat.variables.service import VariablesService

try:
    from tracecat_registry import secrets as registry_secrets
    from tracecat_registry.context import RegistryContext, set_context
except ImportError:
    RegistryContext = None  # type: ignore[misc, assignment]
    set_context = None  # type: ignore[assignment]
    registry_secrets = None  # type: ignore[assignment]

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


_registry_artifacts_cache: dict[str, tuple[float, list[RegistryArtifactsContext]]] = {}


async def get_registry_artifacts_cached(role: Role) -> list[RegistryArtifactsContext]:
    """Get latest registry tarball URIs, cached per workspace.

    Uses a simple TTL cache. No locking - if multiple concurrent requests
    hit an expired cache, they'll all query the DB (harmless duplicate work).
    """
    cache_key = str(role.workspace_id)
    if cached := _registry_artifacts_cache.get(cache_key):
        expire_time, artifacts = cached
        if time.time() < expire_time:
            return artifacts

    # Cache miss or expired - fetch from DB
    logger.info("Querying latest registry artifacts from DB", cache_key=cache_key)
    async with (
        get_async_session_context_manager() as session,
        with_session(session=session),
    ):
        subq = (
            select(
                RegistryVersion.repository_id,
                func.max(RegistryVersion.created_at).label("max_created_at"),
            )
            .where(RegistryVersion.organization_id == config.TRACECAT__DEFAULT_ORG_ID)
            .group_by(RegistryVersion.repository_id)
            .subquery()
        )
        rv_alias = aliased(RegistryVersion)
        statement = (
            select(
                RegistryRepository.origin,
                rv_alias.version,
                rv_alias.tarball_uri,
            )
            .join(subq, RegistryRepository.id == subq.c.repository_id)
            .join(
                rv_alias,
                (rv_alias.repository_id == subq.c.repository_id)
                & (rv_alias.created_at == subq.c.max_created_at),
            )
            .where(
                RegistryRepository.organization_id == config.TRACECAT__DEFAULT_ORG_ID
            )
        )
        result = await session.execute(statement)
        artifacts = [
            RegistryArtifactsContext(
                origin=str(origin),
                version=str(version),
                tarball_uri=str(tarball_uri),
            )
            for origin, version, tarball_uri in result.all()
            if tarball_uri is not None
        ]

    logger.info(
        "Fetched registry artifacts and updating cache",
        count=len(artifacts),
        cache_key=cache_key,
    )
    _registry_artifacts_cache[cache_key] = (time.time() + 60, artifacts)
    return artifacts


async def get_registry_artifacts_for_lock(
    registry_lock: dict[str, str],
) -> list[RegistryArtifactsContext]:
    """Get registry tarball URIs for specific locked versions.

    Args:
        registry_lock: Maps origin -> version string.
            Example: {"tracecat_registry": "2024.12.10.123456", "git+ssh://...": "abc1234"}

    Returns:
        List of RegistryArtifactsContext for the locked versions.
    """
    if not registry_lock:
        return []

    async with (
        get_async_session_context_manager() as session,
        with_session(session=session),
    ):
        artifacts: list[RegistryArtifactsContext] = []

        for origin, version in registry_lock.items():
            # Query for the specific version
            statement = (
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
                    RegistryRepository.origin == origin,
                    RegistryVersion.version == version,
                    RegistryRepository.organization_id
                    == config.TRACECAT__DEFAULT_ORG_ID,
                )
            )
            result = await session.execute(statement)
            row = result.first()

            if row and row[2] is not None:
                # Only include if tarball_uri exists (required after migration)
                artifacts.append(
                    RegistryArtifactsContext(
                        origin=str(row[0]),
                        version=str(row[1]),
                        tarball_uri=str(row[2]),
                    )
                )
            elif row:
                logger.warning(
                    "Registry version found but missing tarball_uri",
                    origin=origin,
                    version=version,
                )
            else:
                logger.warning(
                    "Registry version not found for lock entry",
                    origin=origin,
                    version=version,
                )

    return artifacts


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


async def run_single_action(
    *,
    action: BoundRegistryAction,
    args: ArgsT,
    context: ExecutionContext,
) -> Any:
    """Run a UDF async."""
    if action.is_template:
        logger.info("Running template action async", action=action.name)
        result = await run_template_action(action=action, args=args, context=context)
    else:
        logger.trace("Running UDF async", action=action.name)
        # Get secrets from context
        secrets = context.get(ExprContext.SECRETS, {})
        flat_secrets = secrets_manager.flatten_secrets(secrets)
        with secrets_manager.env_sandbox(flat_secrets):
            result = await _run_action_direct(action=action, args=args)

    return result


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
            "Please use `run_single_action` instead."
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
    env_context = {}
    vars_context = {}
    if context is not None:
        secrets_context = context.get(ExprContext.SECRETS, {})
        env_context = context.get(ExprContext.ENV, {})
        vars_context = context.get(ExprContext.VARS, {})

    template_context = cast(
        ExecutionContext,
        {
            ExprContext.SECRETS: secrets_context,
            ExprContext.ENV: env_context,
            ExprContext.VARS: vars_context,
            ExprContext.TEMPLATE_ACTION_INPUTS: validated_args,
            ExprContext.TEMPLATE_ACTION_STEPS: {},
        },
    )
    logger.info("Running template action", action=defn.action)

    for step in defn.steps:
        evaled_args = cast(
            ArgsT,
            eval_templated_object(
                step.args, operand=cast(ExprOperand, template_context)
            ),
        )
        async with RegistryActionsService.with_session() as service:
            step_action = await service.load_action_impl(
                action_name=step.action, mode="execution"
            )
        logger.trace("Running action step", step_action=step_action.action)
        result = await run_single_action(
            action=step_action,
            args=evaled_args,
            context=template_context,
        )
        # Store the result of the step
        logger.trace("Storing step result", step=step.ref, result=result)
        template_context[ExprContext.TEMPLATE_ACTION_STEPS][step.ref] = TaskResult(
            result=result,
            result_typename=type(result).__name__,
        )

    # Handle returns
    return eval_templated_object(
        defn.returns, operand=cast(ExprOperand, template_context)
    )


async def run_action_from_input(input: RunActionInput, role: Role) -> Any:
    """Main entrypoint for running an action."""
    ctx_role.set(role)
    ctx_run.set(input.run_context)
    ctx_session_id.set(input.session_id)
    # The interaction context was generated by the worker
    if input.interaction_context is not None:
        ctx_interaction.set(input.interaction_context)

    # Set up the registry context for SDK access within UDFs
    if (
        RegistryContext is not None
        and set_context is not None
        and is_feature_enabled(FeatureFlag.REGISTRY_CLIENT)
    ):
        executor_token = ""
        if is_feature_enabled(FeatureFlag.EXECUTOR_AUTH):
            # Create a dedicated executor role preserving workspace context
            # The caller's role may be a user role, but executor tokens require
            # type="service" and service_id="tracecat-executor" for verification
            executor_role = Role(
                type="service",
                service_id="tracecat-executor",
                access_level=AccessLevel.ADMIN,
                workspace_id=role.workspace_id,
                organization_id=role.organization_id,
                user_id=role.user_id,
            )
            executor_token = mint_executor_token(
                role=executor_role,
                run_id=str(input.run_context.wf_run_id),
                workflow_id=str(input.run_context.wf_id),
            )
        registry_ctx = RegistryContext(
            workspace_id=str(role.workspace_id) if role.workspace_id else "",
            workflow_id=str(input.run_context.wf_id),
            run_id=str(input.run_context.wf_run_id),
            environment=input.run_context.environment,
            api_url=config.TRACECAT__API_URL,
            token=executor_token,
        )
        set_context(registry_ctx)

    log = ctx_logger.get(logger.bind(ref=input.task.ref))

    task = input.task
    action_name = task.action

    async with RegistryActionsService.with_session() as service:
        reg_action = await service.get_action(action_name)
        action_secrets = await service.fetch_all_action_secrets(reg_action)
        action = service.get_bound(reg_action, mode="execution")

    collected = collect_expressions(task.args)
    secrets = await secrets_manager.get_action_secrets(
        secret_exprs=collected.secrets, action_secrets=action_secrets
    )
    workspace_variables = await get_workspace_variables(
        variable_exprs=collected.variables,
        environment=input.run_context.environment,
        role=role,
    )
    if config.TRACECAT__UNSAFE_DISABLE_SM_MASKING:
        log.warning(
            "Secrets masking is disabled. This is unsafe in production workflows."
        )
        mask_values = None
    else:
        # Safety: Extract complete secret values for masking, not individual characters
        mask_values = set()
        for _, secret_value in traverse_leaves(secrets):
            # Convert non-string values to strings for masking
            if secret_value is not None:
                secret_str = str(secret_value)
                # Only mask non-empty string values that are longer than 1 character
                # to avoid masking individual characters that might appear in regular text
                if len(secret_str) > 1:
                    mask_values.add(secret_str)
                # Also mask the original value if it's already a string
                if isinstance(secret_value, str) and len(secret_value) > 1:
                    mask_values.add(secret_value)

    # When we're here, we've populated the task arguments with shared context values

    # Note: Do not log task.args here as it may contain templated secrets
    log.info(
        "Run action",
        task_ref=task.ref,
        action_name=action_name,
        # Removed args=task.args to prevent secret leakage
    )

    context = input.exec_context.copy()
    context[ExprContext.SECRETS] = secrets
    context[ExprContext.VARS] = workspace_variables

    # Set logical_time for deterministic FN.now() etc.
    # logical_time = time_anchor + elapsed workflow time
    # Always set unconditionally to avoid stale context leakage
    env_context = context.get(ExprContext.ENV) or {}
    workflow_context = env_context.get("workflow") or {}
    logical_time = workflow_context.get("logical_time")
    if logical_time is not None:
        # logical_time may be serialized as ISO string through Temporal
        if isinstance(logical_time, str):
            logical_time = datetime.fromisoformat(logical_time)
    ctx_logical_time.set(logical_time)

    flattened_secrets = secrets_manager.flatten_secrets(secrets)

    # Initialize registry secrets context for SDK mode
    if registry_secrets is not None and is_feature_enabled(FeatureFlag.REGISTRY_CLIENT):
        registry_secrets.set_context(flattened_secrets)

    with secrets_manager.env_sandbox(flattened_secrets):
        args = evaluate_templated_args(task, context)
        result = await run_single_action(action=action, args=args, context=context)

    if mask_values:
        result = apply_masks_object(result, masks=mask_values)

    log.trace("Result", result=result)
    return result


async def _get_registry_pythonpath(input: RunActionInput, role: Role) -> str | None:
    """Get the PYTHONPATH for the current registry version.

    Uses the same logic as run_action_in_subprocess to get artifacts.
    Returns a colon-separated path including all registry tarballs.
    """
    if config.TRACECAT__LOCAL_REPOSITORY_ENABLED:
        return None

    # Get artifacts from registry (S3 wheelhouse/tarball)
    tarball_uris: list[str] = []
    try:
        if input.registry_lock:
            artifacts = await get_registry_artifacts_for_lock(input.registry_lock)
        else:
            artifacts = await get_registry_artifacts_cached(role)

        # Collect ALL tarball URIs, not just the first one
        # This is important for multi-registry setups (e.g., builtin + custom)
        for artifact in artifacts:
            if artifact.tarball_uri:
                tarball_uris.append(artifact.tarball_uri)
    except Exception as e:
        logger.warning("Failed to load registry artifacts metadata", error=str(e))
        return None

    if not tarball_uris:
        return None

    # Use ActionRunner to ensure all registry environments are set up
    runner = get_action_runner()
    paths: list[str] = []
    for tarball_uri in tarball_uris:
        target_dir = await runner.ensure_registry_environment(tarball_uri=tarball_uri)
        if target_dir:
            paths.append(str(target_dir))

    return ":".join(paths) if paths else None


async def run_action_on_cluster(
    input: RunActionInput, ctx: DispatchActionContext, iteration: int | None = None
) -> ExecutionResult:
    """Execute action using the configured backend.

    The backend is selected via TRACECAT__EXECUTOR_BACKEND config:
    - 'sandboxed_pool': Warm nsjail workers (single-tenant, high throughput)
    - 'ephemeral': Cold nsjail subprocess per action (multitenant, full isolation)
    - 'direct': In-process execution (development only)
    - 'auto': Auto-select based on environment
    """
    # Lazy import to avoid circular dependency:
    # workflow.py -> service.py -> backends/__init__.py -> direct.py -> service.py
    from tracecat.executor.backends import get_executor_backend

    role = ctx.role
    action_name = input.task.action
    timeout = config.TRACECAT__EXECUTOR_CLIENT_TIMEOUT

    # Ensure registry environment is set up (for tarball extraction)
    await _get_registry_pythonpath(input, role)

    backend = get_executor_backend()

    try:
        result = await backend.execute(input=input, role=role, timeout=timeout)
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

    # Handle response from backend
    if isinstance(result, ExecutorResultSuccess):
        return result.result

    # Error response from backend
    error_data = result.error
    exec_result = ExecutorActionErrorInfo.model_validate(error_data)
    if iteration is not None:
        exec_result.loop_iteration = iteration
        exec_result.loop_vars = input.exec_context.get(ExprContext.LOCAL_VARS)
    raise ExecutionError(info=exec_result)


async def run_action_direct(
    input: RunActionInput, ctx: DispatchActionContext, iteration: int | None = None
) -> ExecutionResult:
    """Run action directly without sandbox isolation (for local dev)."""
    role = ctx.role
    action_name = input.task.action
    try:
        result = await run_action_from_input(input, role)
        return result
    except Exception as e:
        # Use the factory method to properly extract traceback info
        exec_result = ExecutorActionErrorInfo.from_exc(e, action_name=action_name)
        if iteration is not None:
            exec_result.loop_iteration = iteration
            exec_result.loop_vars = input.exec_context.get(ExprContext.LOCAL_VARS)
        raise ExecutionError(info=exec_result) from e


async def dispatch_action(input: RunActionInput) -> Any:
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
    ctx = DispatchActionContext(role=role)
    return await _dispatch_action(input=input, ctx=ctx)


async def _dispatch_action(
    input: RunActionInput,
    ctx: DispatchActionContext,
) -> Any:
    task = input.task
    logger.info("Preparing runtime environment", ctx=ctx)
    # If there's no for_each, execute normally
    if not task.for_each:
        return await run_action_on_cluster(input, ctx)

    logger.info("Running for_each on action in parallel", action=task.action)

    # Handle for_each by creating parallel executions
    base_context = input.exec_context
    # We have a list of iterators that give a variable assignment path ".path.to.value"
    # and a collection of values as a tuple.
    iterators = get_iterables_from_expression(expr=task.for_each, operand=base_context)

    async def iteration(patched_input: RunActionInput, i: int):
        return await run_action_on_cluster(patched_input, ctx, iteration=i)

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
                        obj=new_context,  # type: ignore
                        path=ExprContext.LOCAL_VARS + iterator_path,
                        value=iterator_value,
                    )
                # Create a new task with the patched context
                new_input = input.model_copy(update={"exec_context": new_context})
                coro = iteration(new_input, i)
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


def patch_object(obj: dict[str, Any], *, path: str, value: Any, sep: str = ".") -> None:
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
                obj=patched_context,  # type: ignore
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
