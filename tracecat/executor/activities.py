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
from tracecat.contexts import ctx_logger, ctx_role, ctx_run
from tracecat.dsl.schemas import RunActionInput
from tracecat.dsl.types import ActionErrorInfo
from tracecat.exceptions import (
    ExecutionError,
    LoopExecutionError,
    RateLimitExceeded,
)
from tracecat.executor.backends import get_executor_backend
from tracecat.executor.service import dispatch_action
from tracecat.logger import logger
from tracecat.storage.object import get_object_storage


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
                    result = await dispatch_action(backend=backend, input=input)

                    # Always wrap result in StoredObject envelope
                    # - get_object_storage() returns S3ObjectStorage when externalization is enabled
                    #   (externalizes if above threshold), else InMemoryObjectStorage (always inline)
                    storage = get_object_storage()
                    key = f"{input.run_context.wf_exec_id}/{input.stream_id}/{task.ref}.json"
                    stored = await storage.store(key, result)

                    return {
                        "stored": stored,  # Temporal serializes Pydantic models
                        "result_typename": type(result).__name__,
                    }
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
