"""Functions for executing actions and templates.

NOTE: This is only used in the API server, not the worker
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from typing import Any, cast

from tracecat import config
from tracecat.auth.sandbox import AuthSandbox
from tracecat.concurrency import GatheringTaskGroup
from tracecat.contexts import ctx_logger, ctx_role, ctx_run
from tracecat.dsl.io import resolve_success_output
from tracecat.dsl.models import (
    ActionStatement,
    DSLContext,
    DSLNodeResult,
    UDFActionInput,
)
from tracecat.expressions.eval import (
    eval_templated_object,
    extract_templated_secrets,
    get_iterables_from_expression,
)
from tracecat.expressions.shared import ExprContext, context_locator
from tracecat.logger import logger
from tracecat.parse import traverse_leaves
from tracecat.registry.actions.models import ArgsClsT, ArgsT, BoundRegistryAction
from tracecat.registry.actions.service import RegistryActionsService
from tracecat.secrets.common import apply_masks_object
from tracecat.secrets.constants import DEFAULT_SECRETS_ENVIRONMENT
from tracecat.secrets.secrets_manager import env_sandbox
from tracecat.types.exceptions import TracecatException

"""All these methods are used in the registry executor, not on the worker"""


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
            logger.info("Running UDF async")
            return await action.fn(**args)
        logger.info("Running UDF sync")
        return await asyncio.to_thread(action.fn, **args)
    except Exception as e:
        logger.error(f"Error running UDF {action.action!r}: {e}")
        raise


async def run_single_action(
    *,
    action_name: str,
    args: ArgsT,
    context: dict[str, Any] | None = None,
    version: str | None = None,
) -> Any:
    """Run a UDF async."""
    # NOTE(perf): We might want to cache this, or call at a higher level
    async with RegistryActionsService.with_session() as service:
        action = await service.load_action_impl(
            version=version, action_name=action_name
        )
    validated_args = action.validate_args(**args)
    if action.is_template:
        logger.info("Running template UDF async", action=action_name)
        return await run_template_action(
            action=action,
            args=validated_args,
            context=context or {},
            version=version,
        )

    logger.info("Running regular UDF async", action=action_name)
    secret_names = [secret.name for secret in action.secrets or []]
    run_context = ctx_run.get()
    environment = getattr(run_context, "environment", DEFAULT_SECRETS_ENVIRONMENT)
    async with (
        AuthSandbox(
            secrets=secret_names, target="context", environment=environment
        ) as sandbox,
    ):
        # Flatten the secrets to a dict[str, str]
        secret_context = sandbox.secrets.copy()
        flattened_secrets: dict[str, str] = {}
        for name, keyvalues in secret_context.items():
            for key, value in keyvalues.items():
                if key in flattened_secrets:
                    raise ValueError(
                        f"Key {key!r} is duplicated in {name!r}! "
                        "Please ensure only one secret with a given name is set. "
                        "e.g. If you have `first_secret.KEY` set, then you cannot "
                        "also set `second_secret.KEY` as `KEY` is duplicated."
                    )
                flattened_secrets[key] = value

        with env_sandbox(flattened_secrets):
            # Run the UDF in the caller process (usually the worker)
            return await _run_action_direct(action=action, args=validated_args)


async def run_template_action(
    *,
    action: BoundRegistryAction[ArgsClsT],
    args: ArgsT,
    context: DSLContext,
    version: str | None = None,
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
    template_context = context.copy() | {
        ExprContext.TEMPLATE_ACTION_INPUTS: args,
        ExprContext.TEMPLATE_ACTION_LAYERS: {},
    }
    logger.info("Running template action", action=defn.action)

    for layer in defn.layers:
        evaled_args = cast(
            ArgsT, eval_templated_object(layer.args, operand=template_context)
        )
        result = await run_single_action(
            action_name=layer.action,
            args=evaled_args,
            context=template_context,
            version=version,
        )
        # Store the result of the layer
        logger.info("Storing layer result", layer=layer.ref, result=result)
        template_context[ExprContext.TEMPLATE_ACTION_LAYERS][layer.ref] = DSLNodeResult(
            result=result,
            result_typename=type(result).__name__,
        )

    # Handle returns
    return eval_templated_object(defn.returns, operand=template_context)


async def run_action_from_input(input: UDFActionInput[ArgsT]) -> Any:
    """This runs on the executor (API, not worker)"""
    ctx_run.set(input.run_context)
    ctx_role.set(input.role)
    act_logger = ctx_logger.get()

    task = input.task
    registry_version = input.run_context.registry_version
    environment = input.run_context.environment
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
    secret_refs = extract_templated_secrets(task.args)

    async with AuthSandbox(
        secrets=secret_refs, target="context", environment=environment
    ) as sandbox:
        secrets = sandbox.secrets.copy()
    context_with_secrets = DSLContext(
        **{
            **input.exec_context,
            ExprContext.SECRETS: secrets,
        }
    )

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

    # Short circuit if mocking the output
    if (act_test := input.action_test) and act_test.enable:
        # XXX: This will fail if we run it against a loop
        act_logger.warning(
            f"Action test enabled, mocking the output of {task.ref!r}."
            " You should not use this in production workflows."
        )
        if act_test.validate_args:
            # args = _evaluate_templated_args(task, context_with_secrets)
            # action.validate_args(**args)
            act_logger.warning("Action test validation not supported")
            pass
        return await resolve_success_output(act_test)

    # Actual execution

    if task.for_each:
        iterator = iter_for_each(task=task, context=context_with_secrets)
        try:
            async with GatheringTaskGroup() as tg:
                for patched_args in iterator:
                    tg.create_task(
                        run_single_action(
                            action_name=action_name,
                            args=patched_args,
                            context=context_with_secrets,
                            version=registry_version,
                        )
                    )

            result = tg.results()
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

    else:
        args = evaluate_templated_args(task, context_with_secrets)
        result = await run_single_action(
            action_name=action_name,
            args=args,
            context=context_with_secrets,
            version=registry_version,
        )

    if mask_values:
        result = apply_masks_object(result, masks=mask_values)

    act_logger.debug("Result", result=result)
    return result


"""Utilities"""


def evaluate_templated_args(task: ActionStatement[ArgsT], context: DSLContext) -> ArgsT:
    return cast(ArgsT, eval_templated_object(task.args, operand=context))


def patch_object(obj: dict[str, Any], *, path: str, value: Any, sep: str = ".") -> None:
    *stem, leaf = path.split(sep=sep)
    for key in stem:
        obj = obj.setdefault(key, {})
    obj[leaf] = value


def iter_for_each(
    task: ActionStatement[ArgsT],
    context: DSLContext,
    *,
    assign_context: ExprContext = ExprContext.LOCAL_VARS,
    patch: bool = True,
) -> Iterator[ArgsT]:
    """Yield patched contexts for each loop iteration."""
    # Evaluate the loop expression
    iterators = get_iterables_from_expression(expr=task.for_each, operand=context)

    # Assert that all length of the iterables are the same
    # This is a requirement for parallel processing
    # if len({len(expr.collection) for expr in iterators}) != 1:
    #     raise ValueError("All iterables must be of the same length")

    # Create a generator that zips the iterables together
    for i, items in enumerate(zip(*iterators, strict=False)):
        logger.trace("Loop iteration", iteration=i)
        # Patch the context with the loop item and evaluate the action-local expressions
        # We're copying this so that we don't pollute the original context
        # Currently, the only source of action-local expressions is the loop iteration
        # In the future, we may have other sources of action-local expressions
        patched_context = (
            context.copy()
            if patch
            # XXX: ENV is the only context that should be shared
            else DSLContext.create_default()
        )
        logger.trace("Context before patch", patched_context=patched_context)
        for iterator_path, iterator_value in items:
            patch_object(
                patched_context,
                path=assign_context + iterator_path,
                value=iterator_value,
            )
        logger.trace("Patched context", patched_context=patched_context)
        patched_args = evaluate_templated_args(task=task, context=patched_context)
        logger.trace("Patched args", patched_args=patched_args)
        yield patched_args
