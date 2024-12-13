"""Functions for executing actions and templates.

NOTE: This is only used in the API server, not the worker
"""

from __future__ import annotations

import asyncio
import os
import traceback
from collections.abc import Iterator, Mapping
from concurrent.futures import ProcessPoolExecutor
from typing import Any, cast

import uvloop
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from tracecat import config
from tracecat.auth.credentials import RoleACL
from tracecat.auth.sandbox import AuthSandbox
from tracecat.concurrency import GatheringTaskGroup
from tracecat.contexts import ctx_logger, ctx_role, ctx_run
from tracecat.db.dependencies import AsyncDBSession
from tracecat.db.engine import get_async_engine
from tracecat.dsl.common import context_locator, create_default_dsl_context
from tracecat.dsl.models import (
    ActionStatement,
    DSLContext,
    DSLNodeResult,
    RunActionInput,
)
from tracecat.expressions.eval import (
    OperandType,
    eval_templated_object,
    extract_templated_secrets,
    get_iterables_from_expression,
)
from tracecat.expressions.shared import ExprContext
from tracecat.logger import logger
from tracecat.parse import traverse_leaves
from tracecat.registry.actions.models import (
    ArgsClsT,
    BoundRegistryAction,
    RegistryActionErrorInfo,
    RegistryActionValidate,
    RegistryActionValidateResponse,
)
from tracecat.registry.actions.service import RegistryActionsService
from tracecat.registry.repository import Repository
from tracecat.secrets.common import apply_masks_object
from tracecat.secrets.constants import DEFAULT_SECRETS_ENVIRONMENT
from tracecat.secrets.secrets_manager import env_sandbox
from tracecat.types.auth import Role
from tracecat.types.exceptions import RegistryError, TracecatException
from tracecat.validation.service import validate_registry_action_args

"""All these methods are used in the registry executor, not on the worker"""

# Registry Action Controls

type ArgsT = Mapping[str, Any]
_executor: ProcessPoolExecutor | None = None

router = APIRouter(tags=["executor"])


class ExecutorSyncInput(BaseModel):
    origin: str


@router.post("/sync")
async def sync_executor(
    *,
    role: Role = RoleACL(
        allow_user=False,  # XXX(authz): Users cannot sync the executor
        allow_service=True,  # Only services can sync the executor
        require_workspace="no",
    ),
    input: ExecutorSyncInput,
) -> None:
    """Sync the executor from the registry."""
    repo = Repository(origin=input.origin, role=role)
    try:
        await repo.load_from_origin()
    except RegistryError as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e)
        ) from e


@router.post("/run/{action_name}")
async def run_action(
    *,
    role: Role = RoleACL(
        allow_user=False,  # XXX(authz): Users cannot execute actions
        allow_service=True,  # Only services can execute actions
        require_workspace="no",
    ),
    action_name: str,
    action_input: RunActionInput,
) -> Any:
    """Execute a registry action."""
    ref = action_input.task.ref
    ctx_role.set(role)
    act_logger = logger.bind(role=role, action_name=action_name, ref=ref)
    ctx_logger.set(act_logger)

    act_logger.info("Starting action")
    try:
        return await run_action_in_pool(input=action_input)
    except Exception as e:
        # Get the traceback info
        tb = traceback.extract_tb(e.__traceback__)[-1]  # Get the last frame
        error_detail = RegistryActionErrorInfo(
            action_name=action_name,
            type=e.__class__.__name__,
            message=str(e),
            filename=tb.filename,
            function=tb.name,
            lineno=tb.lineno,
        )
        act_logger.error(
            "Error running action",
            action_name=action_name,
            type=error_detail.type,
            message=error_detail.message,
            filename=error_detail.filename,
            function=error_detail.function,
            lineno=error_detail.lineno,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=error_detail.model_dump(mode="json"),
        ) from e


@router.post("/validate/{action_name}")
async def validate_action(
    *,
    role: Role = RoleACL(
        allow_user=False,  # XXX(authz): Users cannot validate actions
        allow_service=True,  # Only services can validate actions
        require_workspace="no",
    ),
    session: AsyncDBSession,
    action_name: str,
    params: RegistryActionValidate,
) -> RegistryActionValidateResponse:
    """Validate a registry action."""
    try:
        result = await validate_registry_action_args(
            session=session, action_name=action_name, args=params.args
        )

        if result.status == "error":
            logger.warning(
                "Error validating UDF args", message=result.msg, details=result.detail
            )
        return RegistryActionValidateResponse.from_validation_result(result)
    except KeyError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Action {action_name!r} not found in registry",
        ) from e


# We want to be able to serve a looped action
# Before we send out tasks to the executor we should inspect the size of the loop
# and set the right chunk size for each worker


def _init_worker_process():
    """Initialize each worker process with its own event loop"""
    # Configure uvloop for the process and create a new event loop
    asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())
    loop = uvloop.new_event_loop()
    asyncio.set_event_loop(loop)
    logger.info("Initialized worker process with new event loop", pid=os.getpid())


def get_executor() -> ProcessPoolExecutor:
    """Get the executor, creating it if it doesn't exist"""
    global _executor
    if _executor is None:
        _executor = ProcessPoolExecutor(initializer=_init_worker_process)
        logger.info("Initialized executor process pool")
    return _executor


def sync_executor_entrypoint(input: RunActionInput[ArgsT], role: Role) -> Any:
    """Run an action on the executor (API, not worker)"""

    logger.info("Running action in pool", input=input)

    async def coro():
        ctx_role.set(role)
        async_engine = get_async_engine()
        try:
            return await run_action_from_input(input=input)
        finally:
            await async_engine.dispose()

    try:
        loop = asyncio.get_running_loop()
        logger.debug("Got running loop")
    except RuntimeError:
        loop = asyncio.get_event_loop()
        logger.debug("Get event loop")
    return loop.run_until_complete(coro())


async def run_action_in_pool(input: RunActionInput[ArgsT]) -> Any:
    """Run an action on the executor (API, not worker)"""
    loop = asyncio.get_running_loop()
    role = ctx_role.get()
    result = await loop.run_in_executor(
        get_executor(), sync_executor_entrypoint, input, role
    )
    return result


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
            return await action.fn(**args)
        logger.trace("Running UDF sync")
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
    context: DSLContext,
) -> Any:
    """Run a UDF async."""

    # Here, we pass in context
    # For this action, check whether its dependent secrets are already in the context
    # For any that aren't, pull them in

    action_secret_names = set()
    optional_secrets = set()
    secrets = context.get("SECRETS", {})

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

    context["SECRETS"] = context.get("SECRETS", {}) | secrets
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
        DSLContext,
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
                step.args, operand=cast(OperandType, template_context)
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
        defn.returns, operand=cast(OperandType, template_context)
    )


async def run_action_from_input(input: RunActionInput) -> Any:
    """This runs on the executor (API, not worker)"""
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
        # Actual execution
        if task.for_each:
            # If the action is CPU bound, just run it directly
            # Otherwise, we want to parallelize it
            iterator = iter_for_each(task=task, context=context)

            try:
                async with GatheringTaskGroup() as tg:
                    for patched_args in iterator:
                        tg.create_task(
                            run_single_action(
                                action=action, args=patched_args, context=context
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

    # Patch the context with the loop item and evaluate the action-local expressions
    # We're copying this so that we don't pollute the original context
    # Currently, the only source of action-local expressions is the loop iteration
    # In the future, we may have other sources of action-local expressions
    # XXX: ENV is the only context that should be shared
    patched_context = context.copy() if patch else create_default_dsl_context()
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
