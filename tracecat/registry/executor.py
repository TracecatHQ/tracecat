"""Functions for executing actions and templates.

NOTE: This is only used in the API server, not the worker
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator, Mapping
from typing import Any, cast

from tracecat import config
from tracecat.auth.sandbox import AuthSandbox
from tracecat.concurrency import GatheringTaskGroup
from tracecat.contexts import ctx_logger, ctx_role, ctx_run
from tracecat.dsl.common import context_locator, create_default_dsl_context
from tracecat.dsl.models import (
    ActionStatement,
    DSLContext,
    DSLNodeResult,
    RunActionInput,
)
from tracecat.expressions.eval import (
    eval_templated_object,
    extract_templated_secrets,
    get_iterables_from_expression,
)
from tracecat.expressions.shared import ExprContext, ExprContextType
from tracecat.logger import logger
from tracecat.parse import traverse_leaves
from tracecat.registry.actions.models import ArgsClsT, BoundRegistryAction
from tracecat.registry.actions.service import RegistryActionsService
from tracecat.secrets.common import apply_masks_object
from tracecat.secrets.constants import DEFAULT_SECRETS_ENVIRONMENT
from tracecat.secrets.secrets_manager import env_sandbox
from tracecat.types.exceptions import TracecatException

"""All these methods are used in the registry executor, not on the worker"""

type ArgsT = Mapping[str, Any]


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
        logger.error(
            f"Error running UDF {action.action!r}", error=e, type=type(e).__name__
        )
        raise e


async def run_single_action(
    *,
    action_name: str,
    args: ArgsT,
    context: dict[str, Any] | None = None,
) -> Any:
    """Run a UDF async."""
    # NOTE(perf): We might want to cache this, or call at a higher level
    async with RegistryActionsService.with_session() as service:
        action = await service.load_action_impl(action_name=action_name)
    validated_args = action.validate_args(**args)

    logger.info("Running regular UDF async", action=action_name)
    secret_names = [secret.name for secret in action.secrets or []]
    optional_secrets = [
        secret.name for secret in action.secrets or [] if secret.optional
    ]
    run_context = ctx_run.get()
    environment = getattr(run_context, "environment", DEFAULT_SECRETS_ENVIRONMENT)
    async with (
        AuthSandbox(
            secrets=secret_names,
            target="context",
            environment=environment,
            optional_secrets=optional_secrets,
        ) as sandbox,
    ):
        # Flatten the secrets to a dict[str, str]
        secret_context = sandbox.secrets.copy()
        if action.is_template:
            logger.info("Running template UDF async", action=action_name)
            context_with_secrets = context.copy() if context else {}
            # Merge the secrets from the sandbox with the existing context
            context_with_secrets[ExprContext.SECRETS] = (
                context_with_secrets.get(ExprContext.SECRETS, {}) | secret_context
            )
            return await run_template_action(
                action=action,
                args=validated_args,
                context=context_with_secrets,
            )
        # Given secrets in the format of {name: {key: value}}, we need to flatten
        # it to a dict[str, str] to set in the environment context
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
        ExprContextType,
        context.copy()
        | {
            ExprContext.TEMPLATE_ACTION_INPUTS: args,
            ExprContext.TEMPLATE_ACTION_STEPS: {},
        },
    )
    logger.info("Running template action", action=defn.action)

    for step in defn.steps:
        evaled_args = cast(
            ArgsT, eval_templated_object(step.args, operand=template_context)
        )
        result = await run_single_action(
            action_name=step.action,
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
    return eval_templated_object(defn.returns, operand=template_context)


async def run_action_from_input(input: RunActionInput[ArgsT]) -> Any:
    """This runs on the executor (API, not worker)"""
    ctx_run.set(input.run_context)
    ctx_role.set(input.role)
    act_logger = ctx_logger.get()

    task = input.task
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
            context=cast(dict[str, Any], context_with_secrets),
        )

    if mask_values:
        result = apply_masks_object(result, masks=mask_values)

    act_logger.debug("Result", result=result)
    return result


"""Utilities"""


def evaluate_templated_args(task: ActionStatement, context: DSLContext) -> ArgsT:
    return cast(ArgsT, eval_templated_object(task.args, operand=context))


def patch_object(obj: dict[str, Any], *, path: str, value: Any, sep: str = ".") -> None:
    *stem, leaf = path.split(sep=sep)
    for key in stem:
        obj = obj.setdefault(key, {})
    obj[leaf] = value


def iter_for_each(
    task: ActionStatement,
    context: DSLContext,
    *,
    assign_context: ExprContext = ExprContext.LOCAL_VARS,
    patch: bool = True,
) -> Iterator[ArgsT]:
    """Yield patched contexts for each loop iteration."""
    # Evaluate the loop expression
    if not task.for_each:
        raise ValueError("No loop expression found")
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
            else create_default_dsl_context()
        )
        logger.trace("Context before patch", patched_context=patched_context)
        for iterator_path, iterator_value in items:
            patch_object(
                cast(dict[str, Any], patched_context),
                path=assign_context + iterator_path,
                value=iterator_value,
            )
        logger.trace("Patched context", patched_context=patched_context)
        patched_args = evaluate_templated_args(task=task, context=patched_context)
        logger.trace("Patched args", patched_args=patched_args)
        yield patched_args
