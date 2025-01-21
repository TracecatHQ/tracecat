from __future__ import annotations

import asyncio
import traceback
from collections.abc import Iterator, Mapping
from typing import Any, cast

import ray
import uvloop
from ray.exceptions import RayTaskError
from ray.runtime_env import RuntimeEnv
from sqlmodel.ext.asyncio.session import AsyncSession

from tracecat import config
from tracecat.auth.sandbox import AuthSandbox
from tracecat.concurrency import GatheringTaskGroup
from tracecat.contexts import ctx_logger, ctx_role, ctx_run
from tracecat.db.engine import get_async_engine, get_async_session_context_manager
from tracecat.dsl.common import context_locator, create_default_execution_context
from tracecat.dsl.models import (
    ActionStatement,
    DSLNodeResult,
    ExecutionContext,
    RunActionInput,
)
from tracecat.executor.engine import EXECUTION_TIMEOUT
from tracecat.executor.models import DispatchActionContext, ExecutorActionErrorInfo
from tracecat.expressions.common import ExprContext, ExprOperand
from tracecat.expressions.eval import (
    eval_templated_object,
    extract_templated_secrets,
    get_iterables_from_expression,
)
from tracecat.git import GitUrl, prepare_git_url
from tracecat.logger import logger
from tracecat.parse import traverse_leaves
from tracecat.registry.actions.models import BoundRegistryAction
from tracecat.registry.actions.service import RegistryActionsService
from tracecat.secrets.common import apply_masks_object
from tracecat.secrets.constants import DEFAULT_SECRETS_ENVIRONMENT
from tracecat.secrets.secrets_manager import env_sandbox
from tracecat.ssh import ssh_context
from tracecat.types.auth import Role
from tracecat.types.exceptions import TracecatException, WrappedExecutionError

"""All these methods are used in the registry executor, not on the worker"""


type ArgsT = Mapping[str, Any]
type ExecutionResult = Any | ExecutorActionErrorInfo


def sync_executor_entrypoint(input: RunActionInput, role: Role) -> ExecutionResult:
    """We run this on the ray cluster."""

    asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())
    loop = uvloop.new_event_loop()
    asyncio.set_event_loop(loop)

    logger.info("Running action in sync entrypoint", action=input.task.action)

    async_engine = get_async_engine()
    try:
        coro = run_action_from_input(input=input, role=role)
        return loop.run_until_complete(coro)
    except Exception as e:
        # Raise the error proxy here
        logger.error(
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

    # Here, we pass in context
    # For this action, check whether its dependent secrets are already in the context
    # For any that aren't, pull them in

    action_secret_names = set()
    optional_secrets = set()
    secrets = context.get(ExprContext.SECRETS, {})

    for secret in action.secrets or []:
        # Only add if not already pulled
        if secret.name not in secrets:
            if secret.optional:
                optional_secrets.add(secret.name)
            action_secret_names.add(secret.name)

    args_secret_refs = set(extract_templated_secrets(args))
    async with AuthSandbox(
        secrets=list(action_secret_names | args_secret_refs),
        target="context",
        environment=get_runtime_env(),
        optional_secrets=list(optional_secrets),
    ) as sandbox:
        secrets |= sandbox.secrets.copy()

    context[ExprContext.SECRETS] = context.get(ExprContext.SECRETS, {}) | secrets
    if action.is_template:
        logger.info("Running template action async", action=action.name)
        result = await run_template_action(action=action, args=args, context=context)
    else:
        logger.trace("Running UDF async", action=action.name)
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

    template_context = cast(
        ExecutionContext,
        {
            ExprContext.SECRETS: secrets_context,
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
            step_action = await service.load_action_impl(action_name=step.action)
        logger.trace("Running action step", step_ation=step_action.action)
        result = await run_single_action(
            action=step_action,
            args=evaled_args,
            context=template_context,
        )
        # Store the result of the step
        logger.trace("Storing step result", step=step.ref, result=result)
        template_context[ExprContext.TEMPLATE_ACTION_STEPS][step.ref] = DSLNodeResult(
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
    act_logger = ctx_logger.get(logger.bind(ref=input.task.ref))

    task = input.task
    action_name = task.action

    # Multi-phase expression resolution
    # ---------------------------------
    # 1. Resolve all expressions in all shared (non action-local) contexts
    # 2. Enter loop iteration (if any)
    # 3. Resolve all action-local expressions

    # Set
    # If there's a for loop, we need to process this action in parallel

    # Evaluate `SECRETS` context (XXX: You likely should use the secrets manager instead)
    # --------------------------
    # Securely inject secrets into the task arguments
    # 1. Find all secrets in the task arguments
    # 2. Load the secrets
    # 3. Inject the secrets into the task arguments using an enriched context
    # NOTE: Regardless of loop iteration, we should only make this call/substitution once!!

    async with RegistryActionsService.with_session() as service:
        action = await service.load_action_impl(action_name=action_name)

    action_secret_names = {secret.name for secret in action.secrets or []}
    optional_secrets = {
        secret.name for secret in action.secrets or [] if secret.optional
    }
    args_secret_refs = set(extract_templated_secrets(task.args))
    async with AuthSandbox(
        secrets=list(action_secret_names | args_secret_refs),
        target="context",
        environment=get_runtime_env(),
        optional_secrets=list(optional_secrets),
    ) as sandbox:
        secrets = sandbox.secrets.copy()

    if config.TRACECAT__UNSAFE_DISABLE_SM_MASKING:
        act_logger.warning(
            "Secrets masking is disabled. This is unsafe in production workflows."
        )
        mask_values = None
    else:
        # Safety: Secret context leaves are all strings
        mask_values = {s for _, s in traverse_leaves(secrets)}

    # When we're here, we've populated the task arguments with shared context values

    act_logger.info(
        "Run action",
        task_ref=task.ref,
        action_name=action_name,
        args=task.args,
    )

    context = input.exec_context.copy()
    context.update(SECRETS=secrets)

    flattened_secrets = flatten_secrets(secrets)
    with env_sandbox(flattened_secrets):
        args = evaluate_templated_args(task, context)
        result = await run_single_action(action=action, args=args, context=context)

    if mask_values:
        result = apply_masks_object(result, masks=mask_values)

    act_logger.debug("Result", result=result)
    return result


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
    input: RunActionInput, ctx: DispatchActionContext
) -> ExecutionResult:
    """Run an action on the ray cluster.

    If any exceptions are thrown here, they're platform level errors.
    All application/user level errors are caught by the executor and returned as values.
    """
    # Initialize runtime environment variables
    async with (
        get_async_session_context_manager() as session,
        ssh_context(git_url=ctx.git_url, session=session, role=ctx.role) as env,
    ):
        env_vars = env.to_dict() if env else {}
        additional_vars: dict[str, Any] = {}

        # Add git URL to pip dependencies if SHA is present
        if ctx.git_url and ctx.git_url.ref:
            url = ctx.git_url.to_url()
            additional_vars["pip"] = [url]
            logger.trace("Adding git URL to runtime env", git_url=ctx.git_url, url=url)

        runtime_env = RuntimeEnv(env_vars=env_vars, **additional_vars)

        logger.info("Running action on ray cluster", runtime_env=runtime_env)
        obj_ref = run_action_task.options(runtime_env=runtime_env).remote(
            input, ctx.role
        )
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
        logger.info("Raising executor error proxy")
        raise WrappedExecutionError(error=exec_result)
    return exec_result


async def dispatch_action_on_cluster(
    input: RunActionInput,
    *,
    session: AsyncSession,
    git_url: GitUrl | None = None,
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

    ctx = DispatchActionContext(role=role, git_url=git_url)
    result = await _dispatch_action(input=input, ctx=ctx)
    return result


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

    async def coro(patched_input: RunActionInput):
        return await run_action_on_ray_cluster(patched_input, ctx)

    try:
        async with GatheringTaskGroup() as tg:
            # Create a generator that zips the iterables together
            # Iterate over the for_each items
            for items in zip(*iterators, strict=False):
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
                tg.create_task(coro(new_input))
        return tg.results()
    except* Exception as eg:
        errors = [str(x) for x in eg.exceptions]
        logger.error("Error resolving expressions", errors=errors)
        raise TracecatException(
            (
                f"[{context_locator(task, 'for_each')}]"
                "\n\nError in loop:"
                f"\n\n{'\n\n'.join(errors)}"
            ),
            detail={"errors": errors},
        ) from eg


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
