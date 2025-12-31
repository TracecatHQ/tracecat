from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Any

import dateparser
from pydantic import BaseModel
from temporalio import activity
from temporalio.exceptions import ApplicationError
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from tracecat.auth.types import Role
from tracecat.contexts import ctx_logger, ctx_logical_time, ctx_run
from tracecat.db.engine import get_async_session_context_manager
from tracecat.dsl.schemas import ActionStatement, RunActionInput
from tracecat.dsl.types import ActionErrorInfo
from tracecat.exceptions import (
    ExecutorClientError,
    RateLimitExceeded,
    RegistryError,
    TracecatExpressionError,
)
from tracecat.executor.client import ExecutorClient
from tracecat.expressions.common import ExprContext
from tracecat.expressions.core import TemplateExpression
from tracecat.expressions.eval import eval_templated_object
from tracecat.logger import logger
from tracecat.registry.actions.schemas import RegistryActionValidateResponse
from tracecat.validation.service import validate_registry_action_args


class ValidateActionActivityInput(BaseModel):
    role: Role
    task: ActionStatement


class DSLActivities:
    """Container for all UDFs registered in the registry."""

    def __new__(cls):  # type: ignore
        raise RuntimeError("This class should not be instantiated")

    @classmethod
    def load(cls) -> list[Callable[[RunActionInput], Any]]:
        """Load and return all UDFs in the class."""
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
    async def validate_action_activity(
        input: ValidateActionActivityInput,
    ) -> RegistryActionValidateResponse:
        """Validate an action.
        Goals:
        - Validate the action arguments against the UDF spec.
        - Return the validated arguments.
        """
        try:
            async with get_async_session_context_manager() as session:
                result = await validate_registry_action_args(
                    session=session,
                    action_name=input.task.action,
                    args=input.task.args,
                    action_ref=input.task.ref,
                )

                if result.status == "error":
                    logger.warning(
                        "Error validating UDF args",
                        message=result.msg,
                        details=result.detail,
                    )
                return RegistryActionValidateResponse.from_validation_result(result)
        except KeyError as e:
            raise RegistryError(
                f"Action {input.task.action!r} not found in registry",
            ) from e

    @staticmethod
    @activity.defn
    def noop_gather_action_activity(input: RunActionInput, role: Role) -> Any:
        """No-op gather action activity."""
        return input.exec_context.get(ExprContext.ACTIONS, {}).get(input.task.ref)

    @staticmethod
    @activity.defn
    async def run_action_activity(input: RunActionInput, role: Role) -> Any:
        """Run an action.
        Goals:
        - Think of this as a controller activity that will orchestrate the execution of the action.
        - The implementation of the action is located elsewhere (registry service on API)
        """
        ctx_run.set(input.run_context)
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

        # Set logical_time for deterministic FN.now() etc.
        # logical_time = time_anchor + elapsed workflow time
        env_context = input.exec_context.get(ExprContext.ENV) or {}
        workflow_context = env_context.get("workflow") or {}
        if logical_time := workflow_context.get("logical_time"):
            # logical_time may be serialized as ISO string through Temporal
            if isinstance(logical_time, str):
                logical_time = datetime.fromisoformat(logical_time)
            ctx_logical_time.set(logical_time)

        act_info = activity.info()
        act_attempt = act_info.attempt
        log.debug(
            "Action activity details",
            task=task,
            attempt=act_attempt,
            retry_policy=task.retry_policy,
        )
        try:
            async for attempt_manager in AsyncRetrying(
                retry=retry_if_exception_type(RateLimitExceeded),
                stop=stop_after_attempt(20),
                wait=wait_exponential(min=4, max=300),
            ):
                with attempt_manager:
                    log.info(
                        "Begin action attempt",
                        attempt_number=attempt_manager.retry_state.attempt_number,
                    )
                    client = ExecutorClient(role=role)
                    return await client.run_action_memory_backend(input)
        except ExecutorClientError as e:
            # We only expect ExecutorClientError to be raised from the executor client
            kind = e.__class__.__name__
            msg = str(e)
            log.info("Executor client error", error=msg, detail=e.detail)
            err_info = ActionErrorInfo(
                ref=task.ref,
                message=msg,
                type=kind,
                attempt=act_attempt,
                stream_id=input.stream_id,
            )
            err_msg = err_info.format("run_action")
            raise ApplicationError(err_msg, err_info, type=kind) from e
        except ApplicationError as e:
            # Unexpected application error - depends
            log.error("ApplicationError occurred", error=e)
            err_info = ActionErrorInfo(
                ref=task.ref,
                message=str(e),
                type=e.type or e.__class__.__name__,
                attempt=act_attempt,
                stream_id=input.stream_id,
            )
            err_msg = err_info.format("run_action")
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
            err_msg = err_info.format("run_action")
            raise ApplicationError(
                err_msg, err_info, type=kind, non_retryable=True
            ) from e

    @staticmethod
    @activity.defn
    def parse_wait_until_activity(
        wait_until: str,
    ) -> str | None:
        """Parse the wait until datetime. We wrap this in an activity to avoid
        non-determinism errors when using the `dateparser` library
        """
        dt = dateparser.parse(
            wait_until, settings={"TIMEZONE": "UTC", "RETURN_AS_TIMEZONE_AWARE": True}
        )
        return dt.isoformat() if dt else None

    @staticmethod
    @activity.defn
    def evaluate_single_expression_activity(
        expression: str,
        operand: dict[str, Any],
    ) -> Any:
        """Evaluate a single templated expression synchronously.

        Additional validation is performed so that *invalid* or *empty* expressions
        no longer fail silently – instead we raise a ``TracecatExpressionError``
        which will cause the activity to fail fast and surface an explicit error
        to the calling workflow.
        """
        expr_str = expression.strip()

        # Fail fast on empty / whitespace‐only expressions so that users receive a
        # clear error instead of silently evaluating to ``False``.
        if not expr_str:
            raise TracecatExpressionError("Expression cannot be empty")

        # Evaluate the expression. Any parsing / evaluation errors raised inside
        # ``TemplateExpression`` are propagated unchanged so that Temporal marks
        # the activity as failed.
        # Internally, this will raise a ``TracecatExpressionError`` if the expression
        # is malformed/invalid.
        expr = TemplateExpression(expr_str, operand=operand)
        return expr.result()

    @staticmethod
    @activity.defn
    def evaluate_templated_object_activity(obj: Any, operand: dict[str, Any]) -> Any:
        """Evaluate templated objects using the expression engine."""

        return eval_templated_object(obj, operand=operand)
