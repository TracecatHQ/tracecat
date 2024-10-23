from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx
from pydantic import BaseModel
from temporalio import activity
from temporalio.exceptions import ApplicationError

from tracecat.contexts import ctx_logger, ctx_role, ctx_run
from tracecat.dsl.models import ActionStatement, ArgsT, RunActionInput
from tracecat.expressions.shared import context_locator
from tracecat.logger import logger
from tracecat.registry.actions.models import RegistryActionValidateResponse
from tracecat.registry.client import RegistryClient
from tracecat.types.auth import Role
from tracecat.types.exceptions import RegistryActionError, TracecatException


def _contextualize_message(
    task: ActionStatement[ArgsT], msg: str | BaseException, *, loc: str = "run_action"
) -> str:
    return f"[{context_locator(task, loc)}]\n\n{msg}"


class ValidateActionActivityInput(BaseModel):
    role: Role
    task: ActionStatement[ArgsT]


class DSLActivities:
    """Container for all UDFs registered in the registry."""

    def __new__(cls):  # type: ignore
        raise RuntimeError("This class should not be instantiated")

    @classmethod
    def load(cls) -> list[Callable[[RunActionInput[ArgsT]], Any]]:
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
        client = RegistryClient(role=input.role)
        return await client.validate_action(
            action_name=input.task.action, args=input.task.args
        )

    @staticmethod
    @activity.defn
    async def run_action_activity(input: RunActionInput[ArgsT]) -> Any:
        """Run an action.
        Goals:
        - Think of this as a controller activity that will orchestrate the execution of the action.
        - The implementation of the action is located elsewhere (registry service on API)
        """
        ctx_run.set(input.run_context)
        ctx_role.set(input.role)
        task = input.task
        environment = input.run_context.environment
        action_name = task.action

        act_logger = logger.bind(
            task_ref=task.ref,
            action_name=action_name,
            wf_id=input.run_context.wf_id,
            role=input.role,
            environment=environment,
        )
        ctx_logger.set(act_logger)

        # NOTE(arch): Should we move this to the registry service?
        # - Reasons for
        #   - Process secrets in the registry executors in a fully isolated environment
        #   - We can reuse the same logic for local execution
        #   - We can add more context to the expression resolution (e.g. loop iteration)
        try:
            # Delegate to the registry client
            client = RegistryClient(role=input.role)
            return await client.call_action(input)
        except RegistryActionError as e:
            act_logger.error("Registry action error occurred", error=e)
            raise ApplicationError(
                _contextualize_message(task, e),
                non_retryable=True,
                type=e.__class__.__name__,
            ) from e
        except TracecatException as e:
            err_type = e.__class__.__name__
            msg = _contextualize_message(task, e)
            act_logger.error(
                "Application exception occurred", error=msg, detail=e.detail
            )
            raise ApplicationError(
                msg, e.detail, non_retryable=True, type=err_type
            ) from e
        except httpx.HTTPStatusError as e:
            act_logger.error("HTTP status error occurred", error=e)
            raise ApplicationError(
                _contextualize_message(
                    task, f"HTTP status error {e.response.status_code}"
                ),
                non_retryable=True,
                type=e.__class__.__name__,
            ) from e
        except httpx.ReadTimeout as e:
            act_logger.error("HTTP read timeout occurred", error=e)
            raise ApplicationError(
                _contextualize_message(task, "HTTP read timeout"),
                non_retryable=True,
                type=e.__class__.__name__,
            ) from e
        except ApplicationError as e:
            act_logger.error("ApplicationError occurred", error=e)
            raise ApplicationError(
                _contextualize_message(task, e.message),
                non_retryable=e.non_retryable,
                type=e.type,
            ) from e
        except Exception as e:
            act_logger.error("Unexpected error occurred", error=e)
            raise ApplicationError(
                _contextualize_message(
                    task, f"Unexpected error {e.__class__.__name__}: {e}"
                ),
                non_retryable=True,
                type=e.__class__.__name__,
            ) from e
