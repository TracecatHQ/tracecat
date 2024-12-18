"""Functions for executing actions and templates.

NOTE: This is only used in the API server, not the worker
"""

from __future__ import annotations

import asyncio
import traceback
from collections.abc import Callable, Iterator, Mapping
from types import CoroutineType
from typing import Any, cast

import ray
import uvloop
from ray.exceptions import RayTaskError

from tracecat import config
from tracecat.auth.sandbox import AuthSandbox
from tracecat.concurrency import GatheringTaskGroup
from tracecat.contexts import ctx_logger, ctx_role, ctx_run
from tracecat.db.engine import get_async_engine
from tracecat.dsl.common import context_locator, create_default_execution_context
from tracecat.dsl.models import (
    ActionResult,
    ActionStatement,
    ExecutionContext,
    RunActionInput,
)
from tracecat.ee.store.service import get_store
from tracecat.executor.engine import EXECUTION_TIMEOUT
from tracecat.executor.enums import ResultsBackend
from tracecat.expressions.common import ExprContext, ExprOperand
from tracecat.expressions.core import extract_expressions
from tracecat.expressions.eval import (
    eval_templated_object,
    extract_templated_secrets,
    get_iterables_from_expression,
)
from tracecat.logger import logger
from tracecat.parse import traverse_leaves
from tracecat.registry.actions.models import (
    ArgsClsT,
    BoundRegistryAction,
)
from tracecat.registry.actions.service import RegistryActionsService
from tracecat.secrets.common import apply_masks_object
from tracecat.secrets.constants import DEFAULT_SECRETS_ENVIRONMENT
from tracecat.secrets.secrets_manager import env_sandbox
from tracecat.types.auth import Role
from tracecat.types.exceptions import TracecatException

"""All these methods are used in the registry executor, not on the worker"""


type ArgsT = Mapping[str, Any]


def sync_executor_entrypoint(input: RunActionInput[ArgsT], role: Role) -> Any:
    """We run this on the ray cluster."""

    asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())
    loop = uvloop.new_event_loop()
    asyncio.set_event_loop(loop)

    logger.info("Running action in sync entrypoint", action=input.task.action)

    async def coro():
        ctx_role.set(role)
        ctx_run.set(input.run_context)
        ctx_logger.set(
            logger.bind(
                role=role,
                ref=input.task.ref,
                results_backend=config.TRACECAT__RESULTS_BACKEND.value,
            )
        )
        async_engine = get_async_engine()
        try:
            return await run_action_from_input(input=input)
        finally:
            await async_engine.dispose()

    try:
        return loop.run_until_complete(coro())
    except Exception as e:
        logger.error(
            "Error running action",
            error=e,
            type=type(e).__name__,
            traceback=traceback.format_exc(),
        )
        raise e
    finally:
        # We always close the loop
        loop.close()


async def _run_action_direct(
    *, action: BoundRegistryAction[ArgsClsT], args: ArgsT, validate: bool = False
) -> Any:
    """Execute the UDF directly.

    At this point, the UDF cannot be a template.
    """
    if validate:
        # Optional, as we already validate in the caller
        args = action.validate_args(**args)
    if action.is_template:
        # This should not be reached
        raise ValueError("Templates cannot be executed directly")
    try:
        if action.is_async:
            logger.trace("Running UDF async")
            if isinstance(action.fn, CoroutineType):
                raise TracecatException("UDF is async but doesn't return a coroutine")
            return await action.fn(**args)
        logger.trace("Running UDF sync")
        if not isinstance(action.fn, Callable):
            raise TracecatException("UDF is not callable")
        return await asyncio.to_thread(action.fn, **args)
    except Exception as e:
        logger.error(
            f"Error running UDF {action.action!r}", error=e, type=type(e).__name__
        )
        raise e


async def run_single_action(
    *,
    action: BoundRegistryAction[ArgsClsT],
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
        logger.info("Running template UDF async", action=action.name)
        return await run_template_action(action=action, args=args, context=context)
    flat_secrets = flatten_secrets(secrets)
    with env_sandbox(flat_secrets):
        # Run the UDF in the caller process (usually the worker)
        return await _run_action_direct(action=action, args=args)


async def run_template_action(
    *,
    action: BoundRegistryAction[ArgsClsT],
    args: ArgsT,
    context: ExecutionContext | None = None,
) -> Any:
    """Handle template execution.

    You should use `run_async` instead of calling this directly.

    Move the template action execution here, so we can
    override run_async's implementation
    """
    if not action.template_action:
        raise ValueError(
            "Attempted to run a non-template UDF as a template. "
            "Please use `run_single_action` instead."
        )
    defn = action.template_action.definition
    template_context = cast(
        ExecutionContext,
        {
            ExprContext.SECRETS: {}
            if context is None
            else context.get(ExprContext.SECRETS, {}),
            ExprContext.TEMPLATE_ACTION_INPUTS: args,
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
        template_context[ExprContext.TEMPLATE_ACTION_STEPS][step.ref] = ActionResult(
            result=result,
            result_typename=type(result).__name__,
        )

    # Handle returns
    return eval_templated_object(
        defn.returns, operand=cast(ExprOperand, template_context)
    )


async def run_action_from_input(input: RunActionInput) -> Any:
    """This runs on the executor process pool."""
    log = ctx_logger.get()
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

    # === Prepare action ===
    # Prepare the execution context to evaluate expressions
    context = input.exec_context.copy()

    extracted_exprs = extract_expressions(task.args)
    extracted_secrets = extracted_exprs[ExprContext.SECRETS]
    log.trace("Extracted expressions", extracted_exprs=extracted_exprs)

    # If we run with object store backend, we need to retrieve action expressions from the store.
    if config.TRACECAT__RESULTS_BACKEND == ResultsBackend.STORE:
        # (1) Extract expressions
        log.trace("Store backend, pulling action results into execution context")

        # (2) Pull action results from the store
        # We only pull the action results that are actually used in the template
        if extracted_action_refs := extracted_exprs[ExprContext.ACTIONS]:
            store = get_store()
            action_results = await store.load_action_result_batched(
                execution_id=input.run_context.wf_exec_id,
                action_refs=extracted_action_refs,
            )
            context.update(ACTIONS=action_results)
            log.trace("Updated action context", action_results=action_results)
        else:
            log.trace("No action refs in task args")
    else:
        # Otherwise, we use the action results from the current execution context
        log.trace("Memory backend, using action results from current execution context")

    # Pull secrets
    async with AuthSandbox(
        secrets=list(action_secret_names | extracted_secrets),
        target="context",
        environment=get_runtime_env(),
        optional_secrets=list(optional_secrets),
    ) as sandbox:
        secrets = sandbox.secrets.copy()
        context.update(SECRETS=secrets)

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

    context = input.exec_context.copy()
    context.update(SECRETS=secrets)

    flattened_secrets = flatten_secrets(secrets)
    with env_sandbox(flattened_secrets):
        args = evaluate_templated_args(task, context)
        result = await run_single_action(action=action, args=args, context=context)

    if mask_values:
        result = apply_masks_object(result, masks=mask_values)

    log.debug("Result", result=result)
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
def run_action_task(input: RunActionInput, role: Role) -> Any:
    """Ray task that runs an action."""
    return sync_executor_entrypoint(input, role)


async def run_action_on_ray_cluster(input: RunActionInput, role: Role) -> Any:
    """Run an action on the ray cluster."""

    obj_ref = run_action_task.remote(input, role)
    try:
        coro = asyncio.to_thread(ray.get, obj_ref)
        return await asyncio.wait_for(coro, timeout=EXECUTION_TIMEOUT)
    except TimeoutError as e:
        logger.error("Action timed out, cancelling task", error=e)
        ray.cancel(obj_ref, force=True)
        raise e
    except RayTaskError as e:
        logger.error("Error running action on ray cluster", error=e)
        if isinstance(e.cause, BaseException):
            raise e.cause from None
        raise e


async def dispatch_action_on_cluster(input: RunActionInput, role: Role) -> Any:
    """Schedule actions on the ray cluster."""

    task = input.task

    # If there's no for_each, execute normally
    if not task.for_each:
        return await run_action_on_ray_cluster(input, role)

    logger.info("Running for_each on action in parallel", action=task.action)

    # Handle for_each by creating parallel executions
    base_context = input.exec_context
    # We have a list of iterators that give a variable assignment path ".path.to.value"
    # and a collection of values as a tuple.
    iterators = get_iterables_from_expression(expr=task.for_each, operand=base_context)

    async def coro(patched_input: RunActionInput):
        return await run_action_on_ray_cluster(patched_input, role)

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
