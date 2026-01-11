import asyncio
from typing import Any, cast

from temporalio import activity
from temporalio.exceptions import ApplicationError
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from tracecat.auth.types import Role
from tracecat.contexts import ctx_role, ctx_run
from tracecat.dsl.enums import PlatformAction
from tracecat.dsl.schemas import RunActionInput
from tracecat.dsl.types import ActionErrorInfo
from tracecat.dsl.v1.types import (
    ActionFailure,
    ActionSkip,
    ActionSuccess,
    ExecuteSubflow,
    ExecuteTaskResult,
    ExecutionContext,
    MaterializedContext,
)
from tracecat.exceptions import ExecutionError, LoopExecutionError, RateLimitExceeded
from tracecat.executor.backends import get_executor_backend
from tracecat.executor.service import dispatch_action
from tracecat.expressions.eval import eval_templated_object
from tracecat.logger import logger
from tracecat.storage.object import InlineObject, get_object_storage


class V1Activities:
    """Container for all activities in the V1 DSL."""

    def __init__(self) -> None:
        self.storage = get_object_storage()

    @activity.defn
    def action_statement_activity(
        self,
        ctx: RunActionInput[ExecutionContext],
        role: Role,
    ) -> ExecuteTaskResult:
        """Execute an action statement.

        Run sync so it runs in threadpool.

        # Phases
        1. If action has retry_until - execute task until condition is met. We could model this with ActionRetry
        2.

        # ExecuteChildWorkflow result should be ExecuteSubflowResult

        """

        materialized = cast(
            RunActionInput[MaterializedContext],
            ctx.model_copy(update={"exec_context": self.materialize(ctx)}),
        )
        if task_should_skip(materialized):
            return ActionSkip(reason="condition-not-met")

        match ctx.task.action:
            case PlatformAction.TRANSFORM_GATHER:
                raise NotImplementedError("Gather is not implemented")
            case PlatformAction.TRANSFORM_SCATTER:
                raise NotImplementedError("Scatter is not implemented")
            case PlatformAction.CHILD_WORKFLOW_EXECUTE:
                return prepare_subflow(ctx)
            case PlatformAction.AI_AGENT:
                raise NotImplementedError("Agent is not implemented")
            case PlatformAction.AI_PRESET_AGENT:
                raise NotImplementedError("Preset agent is not implemented")
            case _:
                # Regular action
                return execute_action_sync(materialized, role)

    def materialize(self, ctx: RunActionInput[ExecutionContext]) -> MaterializedContext:
        """Materialize the stream-aware execution context for an action."""
        raise RuntimeError("Not implemented")


def task_should_skip(ctx: RunActionInput[MaterializedContext]) -> bool:
    if run_if := ctx.task.run_if:
        return bool(eval_templated_object(run_if, operand=ctx.exec_context))
    return False


def prepare_subflow(ctx: RunActionInput[ExecutionContext]) -> ExecuteSubflow:
    raise NotImplementedError("Not implemented")


def execute_action_sync(
    ctx: RunActionInput[MaterializedContext], role: Role
) -> ActionSuccess | ActionFailure:
    loop = asyncio.get_event_loop()
    fut = asyncio.run_coroutine_threadsafe(execute_action(ctx, role), loop)
    try:
        result = fut.result()
    except (asyncio.CancelledError, TimeoutError) as e:
        # These are expected errors and should not be retried
        raise e
    except Exception as e:
        # NOTE: We should probably raise exceptions here unless theres error path
        outcome = ActionFailure(error=InlineObject(data=str(e)))
    else:
        outcome = ActionSuccess(result=InlineObject(data=result))
    return outcome


async def execute_action(ctx: RunActionInput[MaterializedContext], role: Role) -> Any:
    ctx_run.set(ctx.run_context)
    ctx_role.set(role)

    act_info = activity.info()
    act_attempt = act_info.attempt
    logger.debug(
        "Execute action activity details",
        task=ctx.task,
        attempt=act_attempt,
        retry_policy=ctx.task.retry_policy,
    )

    try:
        backend = get_executor_backend()
        async for attempt_manager in AsyncRetrying(
            retry=retry_if_exception_type(RateLimitExceeded),
            stop=stop_after_attempt(20),
            wait=wait_exponential(min=4, max=300),
        ):
            with attempt_manager:
                logger.debug(
                    "Begin action attempt",
                    attempt_number=attempt_manager.retry_state.attempt_number,
                )
                return await dispatch_action(backend=backend, input=ctx)
    except ExecutionError as e:
        # ExecutionError from dispatch_action (single action failure)
        kind = e.__class__.__name__
        msg = str(e)
        logger.info("Execution error", error=msg, info=e.info)
        err_info = ActionErrorInfo(
            ref=ctx.task.ref,
            message=msg,
            type=kind,
            attempt=act_attempt,
            stream_id=ctx.stream_id,
        )
        err_msg = err_info.format("execute_action")
        raise ApplicationError(err_msg, err_info, type=kind) from e
    except LoopExecutionError as e:
        # LoopExecutionError from dispatch_action (for_each loop failure)
        kind = e.__class__.__name__
        msg = str(e)
        logger.info("Loop execution error", error=msg, loop_errors=e.loop_errors)
        err_info = ActionErrorInfo(
            ref=ctx.task.ref,
            message=msg,
            type=kind,
            attempt=act_attempt,
            stream_id=ctx.stream_id,
        )
        err_msg = err_info.format("execute_action")
        raise ApplicationError(err_msg, err_info, type=kind) from e
    except ApplicationError as e:
        # Pass through ApplicationError
        logger.error("ApplicationError occurred", error=e)
        err_info = ActionErrorInfo(
            ref=ctx.task.ref,
            message=str(e),
            type=e.type or e.__class__.__name__,
            attempt=act_attempt,
            stream_id=ctx.stream_id,
        )
        err_msg = err_info.format("execute_action")
        raise ApplicationError(
            err_msg, err_info, non_retryable=e.non_retryable, type=e.type
        ) from e
    except Exception as e:
        # Unexpected errors - non-retryable
        kind = e.__class__.__name__
        raw_msg = f"Unexpected {kind} occurred:\n{e}"
        logger.error(raw_msg)

        err_info = ActionErrorInfo(
            ref=ctx.task.ref,
            message=raw_msg,
            type=kind,
            attempt=act_attempt,
            stream_id=ctx.stream_id,
        )
        err_msg = err_info.format("execute_action")
        raise ApplicationError(err_msg, err_info, type=kind, non_retryable=True) from e
