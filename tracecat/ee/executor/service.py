import asyncio

from temporalio import activity
from temporalio.exceptions import ApplicationError

from tracecat.contexts import ctx_logger, ctx_run
from tracecat.dsl.action import contextualize_message
from tracecat.dsl.models import DSLTaskErrorInfo, RunActionInput
from tracecat.ee.executor.client import ExecutorClientEE
from tracecat.ee.store.models import StoreObjectPtr
from tracecat.logger import logger
from tracecat.types.auth import Role


@activity.defn
async def run_action_with_store_activity(
    input: RunActionInput, role: Role
) -> StoreObjectPtr:
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

    attempt = activity.info().attempt
    log.info(
        "Run action activity",
        task=input.task,
        attempt=attempt,
        retry_policy=input.task.retry_policy,
    )

    # Add a delay
    if input.task.start_delay > 0:
        log.info("Starting action with delay", delay=input.task.start_delay)
        await asyncio.sleep(input.task.start_delay)

    try:
        # Delegate to the registry client
        client = ExecutorClientEE(role=role)
        handle = await client.run_action_store_backend(input)
        return handle.to_pointer()
    except Exception as e:
        # Now that we return ActionRefHandle, these are transient errors
        kind = e.__class__.__name__
        raise ApplicationError(
            contextualize_message(input.task, e, attempt=attempt),
            DSLTaskErrorInfo(
                ref=input.task.ref, message=str(e), type=kind, attempt=attempt
            ),
        ) from e
