"""Temporal activities for the ExecutorWorker.

These activities run on the 'shared-action-queue' and handle action execution
dispatched from DSL workflows.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from temporalio import activity
from temporalio.exceptions import ApplicationError
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from tracecat.auth.types import Role
from tracecat.contexts import ctx_logger, ctx_logical_time, ctx_role, ctx_run
from tracecat.dsl.enums import PlatformAction
from tracecat.dsl.outcomes import (
    ActionOutcome,
    ActionOutcomeError,
    ActionOutcomeGather,
    ActionOutcomeScatter,
    ActionOutcomeSkipped,
    ActionOutcomeSuccess,
)
from tracecat.dsl.schemas import GatherArgs, RunActionInput, ScatterArgs
from tracecat.dsl.types import ActionErrorInfo
from tracecat.exceptions import (
    ExecutionError,
    LoopExecutionError,
    RateLimitExceeded,
)
from tracecat.executor.backends import get_executor_backend
from tracecat.executor.schemas import HandleActionStatementInput
from tracecat.executor.service import dispatch_action
from tracecat.expressions.common import ExprContext
from tracecat.expressions.eval import eval_templated_object
from tracecat.logger import logger


class ExecutorActivities:
    """Container for executor activities."""

    def __new__(cls) -> None:  # type: ignore[misc]
        raise RuntimeError("This class should not be instantiated")

    @classmethod
    def get_activities(cls) -> list[Callable[..., Any]]:
        """Load and return all activities in the class."""
        return [
            fn
            for method_name in dir(cls)
            if hasattr(
                fn := getattr(cls, method_name),
                "__temporal_activity_definition",
            )
        ]

    @staticmethod
    @activity.defn
    async def execute_action_activity(input: RunActionInput, role: Role) -> Any:
        """Execute an action on the ExecutorWorker.

        This activity runs on 'shared-action-queue' and handles:
        - Rate limit retries (tenacity)
        - for_each loop execution (via dispatch_action)
        - Sandboxed pool execution

        This replaces the HTTP-based run_action_activity from dsl/action.py.
        Secrets/variables are still handled inside the sandbox (Phase 2 will move them here).
        """
        ctx_run.set(input.run_context)
        ctx_role.set(role)

        task = input.task
        environment = input.run_context.environment
        action_name = task.action

        log = logger.bind(
            task_ref=task.ref,
            action_name=action_name,
            wf_id=input.run_context.wf_id,
            role=role,
            environment=environment,
        )
        ctx_logger.set(log)

        act_info = activity.info()
        act_attempt = act_info.attempt
        log.debug(
            "Execute action activity details",
            task=task,
            attempt=act_attempt,
            retry_policy=task.retry_policy,
        )

        try:
            backend = get_executor_backend()
            async for attempt_manager in AsyncRetrying(
                retry=retry_if_exception_type(RateLimitExceeded),
                stop=stop_after_attempt(20),
                wait=wait_exponential(min=4, max=300),
            ):
                with attempt_manager:
                    log.debug(
                        "Begin action attempt",
                        attempt_number=attempt_manager.retry_state.attempt_number,
                    )
                    return await dispatch_action(backend=backend, input=input)
        except ExecutionError as e:
            # ExecutionError from dispatch_action (single action failure)
            kind = e.__class__.__name__
            msg = str(e)
            log.info("Execution error", error=msg, info=e.info)
            err_info = ActionErrorInfo(
                ref=task.ref,
                message=msg,
                type=kind,
                attempt=act_attempt,
                stream_id=input.stream_id,
            )
            err_msg = err_info.format("execute_action")
            raise ApplicationError(err_msg, err_info, type=kind) from e
        except LoopExecutionError as e:
            # LoopExecutionError from dispatch_action (for_each loop failure)
            kind = e.__class__.__name__
            msg = str(e)
            log.info("Loop execution error", error=msg, loop_errors=e.loop_errors)
            err_info = ActionErrorInfo(
                ref=task.ref,
                message=msg,
                type=kind,
                attempt=act_attempt,
                stream_id=input.stream_id,
            )
            err_msg = err_info.format("execute_action")
            raise ApplicationError(err_msg, err_info, type=kind) from e
        except ApplicationError as e:
            # Pass through ApplicationError
            log.error("ApplicationError occurred", error=e)
            err_info = ActionErrorInfo(
                ref=task.ref,
                message=str(e),
                type=e.type or e.__class__.__name__,
                attempt=act_attempt,
                stream_id=input.stream_id,
            )
            err_msg = err_info.format("execute_action")
            raise ApplicationError(
                err_msg, err_info, non_retryable=e.non_retryable, type=e.type
            ) from e
        except Exception as e:
            # Unexpected errors - non-retryable
            kind = e.__class__.__name__
            raw_msg = f"Unexpected {kind} occurred:\n{e}"
            log.error(raw_msg)

            err_info = ActionErrorInfo(
                ref=task.ref,
                message=raw_msg,
                type=kind,
                attempt=act_attempt,
                stream_id=input.stream_id,
            )
            err_msg = err_info.format("execute_action")
            raise ApplicationError(
                err_msg, err_info, type=kind, non_retryable=True
            ) from e

    @staticmethod
    @activity.defn
    async def handle_action_statement_activity(
        input: HandleActionStatementInput,
        role: Role,
    ) -> ActionOutcome:
        """Handle complete action statement lifecycle in a single activity.

        This activity replaces the multi-activity pattern (run_if eval, validation,
        args eval, execution) with a single activity call that returns ActionOutcome.

        Benefits:
        - Reduces Temporal history events from ~4 per action to 1
        - Provides explicit outcome tracking (success/error/skipped)
        - Prepares for large-payload externalization

        Args:
            input: Complete input including task, context, and execution parameters
            role: Authentication role for the execution

        Returns:
            ActionOutcome: Success, Error, Skipped, Scatter, or Gather outcome
        """
        ctx_run.set(input.run_context)
        ctx_role.set(role)
        ctx_logical_time.set(input.logical_time)

        task = input.task
        environment = input.run_context.environment
        action_name = task.action

        log = logger.bind(
            task_ref=task.ref,
            action_name=action_name,
            wf_id=input.run_context.wf_id,
            role=role,
            environment=environment,
            stream_id=input.stream_id,
        )
        ctx_logger.set(log)

        act_info = activity.info()
        act_attempt = act_info.attempt

        log.debug(
            "Handle action statement activity",
            task=task,
            attempt=act_attempt,
            retry_policy=task.retry_policy,
        )

        try:
            # 1. Evaluate run_if condition (if present)
            if task.run_if:
                run_if_result = _evaluate_run_if(input)
                if not run_if_result:
                    log.info("Task run_if condition false, skipping")
                    return ActionOutcomeSkipped(reason="run_if condition false")

            # 2. Handle control-flow actions (scatter/gather)
            if task.action == PlatformAction.TRANSFORM_SCATTER:
                return _handle_scatter_action(input, log)

            if task.action == PlatformAction.TRANSFORM_GATHER:
                return _handle_gather_action(input, log)

            # 3. Execute regular action
            return await _execute_action_with_outcome(input, role, log, act_attempt)

        except RateLimitExceeded:
            # Let Temporal retry the activity
            raise
        except ApplicationError:
            # Pass through for workflow to handle
            raise
        except Exception as e:
            # Non-retryable unexpected error
            kind = e.__class__.__name__
            raw_msg = f"Unexpected {kind} in handle_action_statement:\n{e}"
            log.error(raw_msg)
            return ActionOutcomeError(
                error=raw_msg,
                error_typename=kind,
            )


def _build_operand(input: HandleActionStatementInput) -> dict[str, Any]:
    """Build operand for expression evaluation from input context."""
    return {
        ExprContext.ACTIONS: input.exec_context.get(ExprContext.ACTIONS, {}),
        ExprContext.TRIGGER: input.exec_context.get(ExprContext.TRIGGER, {}),
        ExprContext.ENV: input.exec_context.get(ExprContext.ENV, {}),
        ExprContext.VARS: input.exec_context.get(ExprContext.VARS, {}),
        ExprContext.LOCAL_VARS: input.exec_context.get(ExprContext.LOCAL_VARS, {}),
        ExprContext.SECRETS: input.exec_context.get(ExprContext.SECRETS, {}),
    }


def _evaluate_run_if(input: HandleActionStatementInput) -> bool:
    """Evaluate run_if expression in activity context.

    Args:
        input: The action statement input containing run_if expression

    Returns:
        True if run_if evaluates to truthy, False otherwise
    """
    operand = _build_operand(input)
    result = eval_templated_object(input.task.run_if, operand=operand)
    return bool(result)


def _handle_scatter_action(
    input: HandleActionStatementInput,
    log: Any,
) -> ActionOutcomeScatter:
    """Handle scatter control-flow action.

    Evaluates the scatter collection and returns count/manifest.
    Does NOT create child streams - that's handled by the scheduler.

    Args:
        input: The action statement input
        log: Logger instance

    Returns:
        ActionOutcomeScatter with count (and later manifest_ref)
    """
    from tracecat.common import is_iterable

    operand = _build_operand(input)
    scatter_args = ScatterArgs.model_validate(input.task.args)

    # Evaluate the collection expression
    collection = eval_templated_object(scatter_args.collection, operand=operand)

    if not is_iterable(collection):
        raise ValueError(
            f"Scatter collection must be iterable, got {type(collection).__name__}"
        )

    items = list(collection)
    count = len(items)

    log.info("Scatter evaluated", count=count)

    # For now, return count. Phase 3 will add manifest_ref externalization.
    return ActionOutcomeScatter(
        count=count,
        result=count,  # Backwards compat: ACTIONS.scatter_ref.result = count
    )


def _handle_gather_action(
    input: HandleActionStatementInput,
    log: Any,
) -> ActionOutcomeGather:
    """Handle gather control-flow action.

    Materializes gathered results from streams.
    Stream synchronization is handled by the scheduler.

    Args:
        input: The action statement input
        log: Logger instance

    Returns:
        ActionOutcomeGather with collected results
    """
    gather_args = GatherArgs.model_validate(input.task.args)

    # Stream results are passed via LOCAL_VARS by the scheduler
    stream_results = input.exec_context.get(ExprContext.LOCAL_VARS, {}).get(
        "__stream_results__", []
    )

    # Apply drop_nulls if configured
    if gather_args.drop_nulls:
        stream_results = [r for r in stream_results if r is not None]

    log.info("Gather materialized", result_count=len(stream_results))

    return ActionOutcomeGather(
        result=stream_results,
        result_typename="list",
    )


async def _execute_action_with_outcome(
    input: HandleActionStatementInput,
    role: Role,
    log: Any,
    act_attempt: int,
) -> ActionOutcome:
    """Execute action and wrap result in ActionOutcome.

    Converts HandleActionStatementInput to RunActionInput and uses
    existing dispatch_action flow.

    Args:
        input: The action statement input
        role: Authentication role
        log: Logger instance
        act_attempt: Current activity attempt number

    Returns:
        ActionOutcomeSuccess or raises for retry/error handling
    """
    # Convert to RunActionInput for existing dispatch_action flow
    run_input = RunActionInput(
        task=input.task,
        exec_context=input.exec_context,
        run_context=input.run_context,
        interaction_context=input.interaction_context,
        stream_id=input.stream_id,
        session_id=input.session_id,
        registry_lock=input.registry_lock,
    )

    try:
        backend = get_executor_backend()
        async for attempt_manager in AsyncRetrying(
            retry=retry_if_exception_type(RateLimitExceeded),
            stop=stop_after_attempt(20),
            wait=wait_exponential(min=4, max=300),
        ):
            with attempt_manager:
                log.debug(
                    "Begin action attempt",
                    attempt_number=attempt_manager.retry_state.attempt_number,
                )
                result = await dispatch_action(backend=backend, input=run_input)
                return ActionOutcomeSuccess(
                    result=result,
                    result_typename=type(result).__name__,
                )
        # Should never reach here - AsyncRetrying always returns or raises
        raise RuntimeError("Retry loop completed without returning")
    except ExecutionError as e:
        kind = e.__class__.__name__
        msg = str(e)
        log.info("Execution error", error=msg, info=e.info)
        err_info = ActionErrorInfo(
            ref=input.task.ref,
            message=msg,
            type=kind,
            attempt=act_attempt,
            stream_id=input.stream_id,
        )
        err_msg = err_info.format("handle_action_statement")
        raise ApplicationError(err_msg, err_info, type=kind) from e
    except LoopExecutionError as e:
        kind = e.__class__.__name__
        msg = str(e)
        log.info("Loop execution error", error=msg, loop_errors=e.loop_errors)
        err_info = ActionErrorInfo(
            ref=input.task.ref,
            message=msg,
            type=kind,
            attempt=act_attempt,
            stream_id=input.stream_id,
        )
        err_msg = err_info.format("handle_action_statement")
        raise ApplicationError(err_msg, err_info, type=kind) from e
    except ApplicationError:
        raise
    except Exception as e:
        kind = e.__class__.__name__
        raw_msg = f"Unexpected {kind} in action execution:\n{e}"
        log.error(raw_msg)
        err_info = ActionErrorInfo(
            ref=input.task.ref,
            message=raw_msg,
            type=kind,
            attempt=act_attempt,
            stream_id=input.stream_id,
        )
        err_msg = err_info.format("handle_action_statement")
        raise ApplicationError(err_msg, err_info, type=kind, non_retryable=True) from e
