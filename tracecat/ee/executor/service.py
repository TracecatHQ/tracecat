import asyncio
from collections.abc import Mapping
from typing import Any

from temporalio import activity
from temporalio.exceptions import ApplicationError

from tracecat.contexts import ctx_logger, ctx_run
from tracecat.dsl.action import contextualize_message
from tracecat.dsl.models import DSLTaskErrorInfo, RunActionInput
from tracecat.ee.executor.client import ExecutorClientEE
from tracecat.ee.store.models import (
    ResultVariantValidator,
    StoreResult,
    TaskResultHandle,
)
from tracecat.ee.store.service import get_store
from tracecat.executor.enums import ResultsBackend
from tracecat.executor.models import TaskResult
from tracecat.logger import logger
from tracecat.types.auth import Role
from tracecat.types.exceptions import ActionExecutionError


@activity.defn
async def run_action_with_store_activity(
    input: RunActionInput, role: Role
) -> StoreResult:
    """
    Run an action in store mode.

    Note
    ----
    We must only pass store object pointers across activities/workflows.
    Do not pass the objects themselves as they are too heavy to be transferred over the boundary.
    DSLContext should just keep track of the references to the objects, not the objects themselves.
    """
    ctx_run.set(input.run_context)
    log = logger.bind(
        task_ref=input.task.ref,
        action_name=input.task.action,
        wf_id=input.run_context.wf_id,
        role=role,
        environment=input.run_context.environment,
    )
    ctx_logger.set(log)

    task = input.task
    attempt = activity.info().attempt
    log.info(
        "Run action activity",
        task=task,
        attempt=attempt,
        retry_policy=task.retry_policy,
    )

    # Add a delay
    if task.start_delay > 0:
        log.info("Starting action with delay", delay=task.start_delay)
        await asyncio.sleep(task.start_delay)

    try:
        # Delegate to the registry client
        client = ExecutorClientEE(role=role)
        handle = await client.run_action_store_backend(input)
        return handle.to_pointer()
    except ActionExecutionError as e:
        # We only expect ActionExecutionError to be raised from the executor client
        kind = e.__class__.__name__
        msg = str(e)
        err_loc = contextualize_message(task, msg, attempt=attempt)
        log.error("Application exception occurred", error=msg, detail=e.detail)
        err_info = DSLTaskErrorInfo(
            ref=task.ref,
            message=msg,
            type=kind,
            attempt=attempt,
        )
        raise ApplicationError(err_loc, err_info, type=kind) from e
    except ApplicationError as e:
        # Unexpected application error - depends
        log.error("ApplicationError occurred", error=e)
        err_loc = contextualize_message(task, e.message, attempt=attempt)
        err_info = DSLTaskErrorInfo(
            ref=task.ref,
            message=str(e),
            type=e.type or e.__class__.__name__,
            attempt=attempt,
        )
        raise ApplicationError(
            err_loc, err_info, non_retryable=e.non_retryable, type=e.type
        ) from e
    except Exception as e:
        # Unexpected errors - non-retryable
        kind = e.__class__.__name__
        raw_msg = f"{kind} occurred:\n{e}"
        log.error(raw_msg)

        err_loc = contextualize_message(task, raw_msg, attempt=attempt)
        err_info = DSLTaskErrorInfo(
            ref=task.ref,
            message=raw_msg,
            type=kind,
            attempt=attempt,
        )
        raise ApplicationError(err_loc, err_info, type=kind, non_retryable=True) from e


async def resolve_task_result(result_obj: Mapping[str, Any]) -> TaskResult:
    result = ResultVariantValidator.validate_python(result_obj)
    match result:
        case TaskResult():
            return result
        case StoreResult():
            # Load the action result here
            store = get_store()
            key = result.key
            handle = TaskResultHandle.from_key(key)
            task_result = await store.load_task_result(handle)
            return TaskResult(tc_backend_=ResultsBackend.MEMORY, **task_result)
        case _:
            raise ValueError(f"Invalid result variant: {result}")
