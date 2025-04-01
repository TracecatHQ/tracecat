from __future__ import annotations

import asyncio
from collections.abc import Callable
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

from tracecat.contexts import ctx_logger, ctx_run
from tracecat.db.engine import get_async_session_context_manager
from tracecat.dsl.models import (
    ActionErrorInfo,
    ActionStatement,
    ExecutionContext,
    RunActionInput,
)
from tracecat.ee.store.service import ObjectStore
from tracecat.executor.client import ExecutorClient
from tracecat.expressions.eval import eval_templated_object
from tracecat.logger import logger
from tracecat.registry.actions.models import RegistryActionValidateResponse
from tracecat.types.auth import Role
from tracecat.types.exceptions import (
    ExecutorClientError,
    RateLimitExceeded,
    RegistryError,
)
from tracecat.validation.service import validate_registry_action_args


class ValidateActionActivityInput(BaseModel):
    role: Role
    task: ActionStatement


class ResolveConditionActivityInput(BaseModel):
    """Input for the resolve run if activity."""

    context: ExecutionContext
    """The context of the workflow."""

    condition_expr: str
    """The condition expression to evaluate."""


class DSLActivities:
    """Container for all UDFs registered in the registry."""

    def __new__(cls):  # type: ignore
        raise RuntimeError("This class should not be instantiated")

    @classmethod
    def load(cls) -> list[Callable[[RunActionInput], Any]]:
        """Load and return all UDFs in the class."""
        return [
            getattr(cls, method_name)
            for method_name in dir(cls)
            if hasattr(
                getattr(cls, method_name),
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

        act_info = activity.info()
        act_attempt = act_info.attempt
        log.info(
            "Run action activity",
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
                        "Tenacity retrying",
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
            )
            err_msg = err_info.format("run_action")
            raise ApplicationError(
                err_msg, err_info, type=kind, non_retryable=True
            ) from e

    @staticmethod
    @activity.defn
    async def parse_wait_until_activity(
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
    async def resolve_condition_activity(input: ResolveConditionActivityInput) -> bool:
        """Resolve a condition expression. Throws an ApplicationError if the result
        cannot be converted to a boolean.
        """
        logger.debug("Resolve condition", condition=input.condition_expr)
        # Don't block the main workflow thread
        result = await resolve_templated_object(input.condition_expr, input.context)
        try:
            conditional_result = bool(result)
            logger.debug(
                "Resolved condition",
                result=result,
                conditional_result=conditional_result,
            )
            return conditional_result
        except Exception:
            raise ApplicationError(
                "Condition result could not be converted to a boolean",
                non_retryable=True,
            ) from None


async def resolve_templated_object(obj: Any, context: ExecutionContext) -> Any:
    resolved_context = await ObjectStore.get().resolve_object_refs(obj, context)
    return await asyncio.to_thread(eval_templated_object, obj, operand=resolved_context)
