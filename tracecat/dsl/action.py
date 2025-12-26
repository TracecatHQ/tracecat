from __future__ import annotations

from collections.abc import Callable
from typing import Any

import dateparser
from pydantic import BaseModel
from temporalio import activity

from tracecat.auth.types import Role
from tracecat.db.engine import get_async_session_context_manager
from tracecat.dsl.schemas import ActionStatement, RunActionInput
from tracecat.exceptions import RegistryError, TracecatExpressionError
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
