from __future__ import annotations

import asyncio
import itertools
import traceback
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any, cast

import orjson
import ray
import uvloop
from ray.exceptions import RayTaskError
from ray.runtime_env import RuntimeEnv
from sqlmodel.ext.asyncio.session import AsyncSession

from tracecat import config
from tracecat.auth.sandbox import AuthSandbox
from tracecat.concurrency import GatheringTaskGroup
from tracecat.contexts import ctx_interaction, ctx_logger, ctx_role, ctx_run
from tracecat.db.engine import get_async_engine
from tracecat.dsl.common import context_locator, create_default_execution_context
from tracecat.dsl.models import (
    ActionStatement,
    ExecutionContext,
    RunActionInput,
    TaskResult,
)
from tracecat.ee.store.models import ObjectRef
from tracecat.ee.store.service import ObjectStore
from tracecat.executor.engine import EXECUTION_TIMEOUT
from tracecat.executor.models import DispatchActionContext, ExecutorActionErrorInfo
from tracecat.expressions.common import ExprContext, ExprOperand
from tracecat.expressions.eval import (
    eval_templated_object,
    extract_templated_secrets,
    get_iterables_from_expression,
)
from tracecat.git import prepare_git_url
from tracecat.logger import logger
from tracecat.parse import get_pyproject_toml_required_deps, traverse_leaves
from tracecat.registry.actions.models import BoundRegistryAction
from tracecat.registry.actions.service import RegistryActionsService
from tracecat.secrets.common import apply_masks_object
from tracecat.secrets.constants import DEFAULT_SECRETS_ENVIRONMENT
from tracecat.secrets.secrets_manager import env_sandbox
from tracecat.ssh import get_ssh_command
from tracecat.types.auth import Role
from tracecat.types.exceptions import (
    ExecutionError,
    LoopExecutionError,
    TracecatException,
)

"""All these methods are used in the registry executor, not on the worker"""


type ArgsT = Mapping[str, Any]
type ExecutionResult = Any | ExecutorActionErrorInfo | ObjectRef


def sync_executor_entrypoint(input: RunActionInput, role: Role) -> ExecutionResult:
    """We run this on the ray cluster."""

    asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())
    loop = uvloop.new_event_loop()
    asyncio.set_event_loop(loop)

    logger.info("Running action in sync entrypoint", action=input.task.action)

    async_engine = get_async_engine()
    try:
        coro = run_action_from_input(input=input, role=role)
        result = loop.run_until_complete(coro)

        if config.TRACECAT__USE_OBJECT_STORE:
            logger.info("Storing action result in object store", result=result)
            # If the object exceeds the hard cap, we error out and do not store it
            result_bytes = orjson.dumps(result)
            if len(result_bytes) > config.TRACECAT__MAX_OBJECT_SIZE_BYTES:
                logger.warning(
                    "Object size exceeds maximum allowed size",
                    size=len(result_bytes),
                    limit=config.TRACECAT__MAX_OBJECT_SIZE_BYTES,
                )
                raise ValueError(
                    f"Object size {len(result_bytes)} bytes exceeds maximum allowed size of {config.TRACECAT__MAX_OBJECT_SIZE_BYTES} bytes"
                )
            logger.info("Storing action result in object store", result=result)
            # Store the result of the action and return the object ref
            store = ObjectStore.get()
            result = loop.run_until_complete(store.put_object_bytes(result_bytes))
        return result
    except Exception as e:
        # Raise the error proxy here
        logger.info(
            "Error running action, raising error proxy",
            error=e,
            type=type(e).__name__,
            traceback=traceback.format_exc(),
        )
        return ExecutorActionErrorInfo.from_exc(e, input.task.action)
    finally:
        loop.run_until_complete(async_engine.dispose())
        loop.close()  # We always close the loop


async def _run_action_direct(*, action: BoundRegistryAction, args: ArgsT) -> Any:
    """Execute the UDF directly.

    At this point, the UDF cannot be a template.
    """
    if action.is_template:
        # This should not be reachable
        raise ValueError("Templates cannot be executed directly")

    validated_args = action.validate_args(**args)
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
        flat_secrets = flatten_secrets(secrets)
        with env_sandbox(flat_secrets):
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
    if defn.expects:
        validated_args = action.validate_args(**args)

    secrets_context = {}
    if context is not None:
        secrets_context = context.get(ExprContext.SECRETS, {})
        env_context = context.get(ExprContext.ENV, {})

    template_context = cast(
        ExecutionContext,
        {
            ExprContext.SECRETS: secrets_context,
            ExprContext.ENV: env_context,
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
    # The interaction context was generated by the worker
    if input.interaction_context is not None:
        ctx_interaction.set(input.interaction_context)
    log = ctx_logger.get(logger.bind(ref=input.task.ref))

    task = input.task
    action_name = task.action

    context = await load_execution_context(input)
    async with RegistryActionsService.with_session() as service:
        reg_action = await service.get_action(action_name)
        action_secrets = await service.fetch_all_action_secrets(reg_action)
        action = service.get_bound(reg_action, mode="execution")

    args_secrets = set(extract_templated_secrets(task.args))
    optional_secrets = {s.name for s in action_secrets if s.optional}
    required_secrets = {s.name for s in action_secrets if not s.optional}
    secrets_to_fetch = required_secrets | args_secrets | optional_secrets

    logger.info(
        "Handling secrets",
        required_secrets=required_secrets,
        optional_secrets=optional_secrets,
        args_secrets=args_secrets,
        secrets_to_fetch=secrets_to_fetch,
    )

    # Get all secrets in one call
    async with AuthSandbox(
        secrets=secrets_to_fetch,
        environment=get_runtime_env(),
        optional_secrets=optional_secrets,
    ) as sandbox:
        secrets = sandbox.secrets.copy()

    if config.TRACECAT__UNSAFE_DISABLE_SM_MASKING:
        log.warning(
            "Secrets masking is disabled. This is unsafe in production workflows."
        )
        mask_values = None
    else:
        # Safety: Secret context leaves are all strings
        mask_values = {s for _, s in traverse_leaves(secrets)}

    # When we're here, we've populated the task arguments with shared context values

    log.info(
        "Run action",
        task_ref=task.ref,
        action_name=action_name,
        args=task.args,
    )

    context.update(SECRETS=secrets)

    flattened_secrets = flatten_secrets(secrets)
    with env_sandbox(flattened_secrets):
        args = evaluate_templated_args(task, context)
        result = await run_single_action(action=action, args=args, context=context)

    if mask_values:
        result = apply_masks_object(result, masks=mask_values)

    log.trace("Result", result=result)
    return result


async def load_execution_context(input: RunActionInput) -> ExecutionContext:
    """Prepare the action context for running an action. If we're using the store pull from minio."""

    log = ctx_logger.get()
    context = input.exec_context.copy()
    if not config.TRACECAT__USE_OBJECT_STORE:
        log.warning("Object store is disabled, skipping action result fetching")
        return context

    # Actions
    # (1) Extract expressions: Grab the action refs that this action depends on
    log.warning("Store backend, pulling action results into execution context")

    task = input.task
    store = ObjectStore.get()
    context = await store.resolve_object_refs(obj=task.args, context=context)
    return context


def get_runtime_env() -> str:
    """Get the runtime environment from `ctx_run` contextvar. Defaults to `default` if not set."""
    return getattr(ctx_run.get(), "environment", DEFAULT_SECRETS_ENVIRONMENT)


def flatten_secrets(secrets: dict[str, Any]):
    """Given secrets in the format of {name: {key: value}}, we need to flatten
    it to a dict[str, str] to set in the environment context.

    For example, if you have the secret `my_secret.KEY`, then you access this in the UDF
    as `KEY`. This means you cannot have a clashing key in different secrets.
    """
    flattened_secrets: dict[str, str] = {}
    for name, keyvalues in secrets.items():
        for key, value in keyvalues.items():
            if key in flattened_secrets:
                raise ValueError(
                    f"Key {key!r} is duplicated in {name!r}! "
                    "Please ensure only one secret with a given name is set. "
                    "e.g. If you have `first_secret.KEY` set, then you cannot "
                    "also set `second_secret.KEY` as `KEY` is duplicated."
                )
            flattened_secrets[key] = value
    return flattened_secrets


@ray.remote
def run_action_task(input: RunActionInput, role: Role) -> ExecutionResult:
    """Ray task that runs an action."""
    return sync_executor_entrypoint(input, role)


async def run_action_on_ray_cluster(
    input: RunActionInput, ctx: DispatchActionContext, iteration: int | None = None
) -> ExecutionResult:
    """Run an action on the ray cluster.

    If any exceptions are thrown here, they're platform level errors.
    All application/user level errors are caught by the executor and returned as values.
    """
    # Initialize runtime environment variables
    env_vars = {"GIT_SSH_COMMAND": ctx.ssh_command} if ctx.ssh_command else {}
    additional_vars: dict[str, Any] = {}

    # Add git URL to pip dependencies if SHA is present
    pip_deps = []
    if ctx.git_url and ctx.git_url.ref:
        url = ctx.git_url.to_url()
        pip_deps.append(url)
        logger.trace("Adding git URL to runtime env", git_url=ctx.git_url, url=url)

    # If we have a local registry, we need to add it to the runtime env
    if config.TRACECAT__LOCAL_REPOSITORY_ENABLED:
        local_repo_path = config.TRACECAT__LOCAL_REPOSITORY_CONTAINER_PATH
        logger.info(
            "Adding local repository and required dependencies to runtime env",
            local_repo_path=local_repo_path,
        )

        # Try pyproject.toml first
        pyproject_path = Path(local_repo_path) / "pyproject.toml"
        if not pyproject_path.exists():
            logger.error(
                "No pyproject.toml found in local repository", path=pyproject_path
            )
            raise ValueError("No pyproject.toml found in local repository")
        required_deps = await asyncio.to_thread(
            get_pyproject_toml_required_deps, pyproject_path
        )
        logger.debug(
            "Found pyproject.toml with required dependencies", deps=required_deps
        )
        pip_deps.extend([local_repo_path, *required_deps])

    # Add pip dependencies to runtime env
    if pip_deps:
        additional_vars["pip"] = pip_deps

    runtime_env = RuntimeEnv(env_vars=env_vars, **additional_vars)

    logger.trace("Running action on ray cluster", runtime_env=runtime_env)
    obj_ref = run_action_task.options(runtime_env=runtime_env).remote(input, ctx.role)
    try:
        coro = asyncio.to_thread(ray.get, obj_ref)
        exec_result = await asyncio.wait_for(coro, timeout=EXECUTION_TIMEOUT)
    except TimeoutError as e:
        logger.error("Action timed out, cancelling task", error=e)
        ray.cancel(obj_ref, force=True)
        raise e
    except RayTaskError as e:
        logger.error("Error running action on ray cluster", error=e)
        if isinstance(e.cause, BaseException):
            raise e.cause from None
        raise e

    # Here, we have some result or error.
    # Reconstruct the error and raise some kind of proxy
    if isinstance(exec_result, ExecutorActionErrorInfo):
        logger.trace("Raising executor error proxy", exec_result=exec_result)
        if iteration is not None:
            exec_result.loop_iteration = iteration
            exec_result.loop_vars = input.exec_context[ExprContext.LOCAL_VARS]
        raise ExecutionError(info=exec_result)
    return exec_result


async def dispatch_action_on_cluster(
    input: RunActionInput,
    session: AsyncSession,
) -> Any:
    """Schedule actions on the ray cluster.

    This function handles dispatching actions to be executed on a Ray cluster. It supports
    both single action execution and parallel execution using for_each loops.

    Args:
        input: The RunActionInput containing the task definition and execution context
        role: The Role used for authorization
        git_url: The Git URL to use for the action
    Returns:
        Any: For single actions, returns the ExecutionResult. For for_each loops, returns
             a list of results from all parallel executions.

    Raises:
        TracecatException: If there are errors evaluating for_each expressions or during execution
        ExecutorErrorWrapper: If there are errors from the executor itself
    """
    git_url = await prepare_git_url()

    role = ctx_role.get()

    ctx = DispatchActionContext(role=role)
    if git_url:
        sh_cmd = await get_ssh_command(git_url=git_url, session=session, role=role)
        ctx.ssh_command = sh_cmd
        ctx.git_url = git_url
    return await _dispatch_action(input=input, ctx=ctx)


async def _dispatch_action(
    input: RunActionInput,
    ctx: DispatchActionContext,
) -> Any:
    task = input.task
    logger.info("Preparing runtime environment", ctx=ctx)
    # If there's no for_each, execute normally
    if not task.for_each:
        return await run_action_on_ray_cluster(input, ctx)

    logger.info("Running for_each on action in parallel", action=task.action)

    # Handle for_each by creating parallel executions
    base_context = input.exec_context
    # We have a list of iterators that give a variable assignment path ".path.to.value"
    # and a collection of values as a tuple.
    iterators = get_iterables_from_expression(expr=task.for_each, operand=base_context)

    async def iteration(patched_input: RunActionInput, i: int):
        return await run_action_on_ray_cluster(patched_input, ctx, iteration=i)

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
    logger.trace("Context before patch", patched_context=patched_context)

    # Create a generator that zips the iterables together
    for i, items in enumerate(zip(*iterators, strict=False)):
        logger.trace("Loop iteration", iteration=i)
        for iterator_path, iterator_value in items:
            patch_object(
                obj=patched_context,  # type: ignore
                path=assign_context + iterator_path,
                value=iterator_value,
            )
        logger.trace("Patched context", patched_context=patched_context)
        patched_args = evaluate_templated_args(task=task, context=patched_context)
        logger.trace("Patched args", patched_args=patched_args)
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
