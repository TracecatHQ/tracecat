from __future__ import annotations

import re
from collections.abc import Awaitable, Iterable, Iterator
from itertools import chain
from typing import Any, Literal, override

from pydantic import BaseModel, Field

from tracecat.concurrency import GatheringTaskGroup
from tracecat.dsl.common import DSLNodeResult
from tracecat.expressions import functions, patterns
from tracecat.expressions.shared import ExprType
from tracecat.expressions.visitors.base import ExprVisitor
from tracecat.logging import logger
from tracecat.types.exceptions import TracecatExpressionError
from tracecat.types.validation import ExprValidationResult


class ExprValidationContext(BaseModel):
    """Container for the validation context of an expression tree."""

    action_refs: set[str]
    inputs_context: Any = Field(default_factory=dict)
    trigger_context: Any = Field(default_factory=dict)


class ExprValidatorVisitor(ExprVisitor):
    """Validate the expression tree by visiting each node and returning the result."""

    _visitor_name = "ExprValidator"

    def __init__(
        self,
        task_group: GatheringTaskGroup,
        validation_context: ExprValidationContext,
        validators: dict[ExprType, Awaitable[ExprValidationResult]] | None = None,
    ) -> None:
        self._task_group = task_group
        # Contextual information
        self._context = validation_context
        self._results: list[ExprValidationResult] = []

        # External validators
        self._validators = validators or {}

        self.logger = logger.bind(visitor=self._visitor_name)

    @override
    def handle_error(self, expr: str) -> Any:
        return self.add(
            status="error",
            msg=f"Invalid expression: {expr!r}",
            type=ExprType.GENERIC,
        )

    @override
    def visit_trailing_cast_expr(self, expr_result: Any, rtype: str) -> Any:
        self.logger.trace(
            "Visit trailing cast expression", expr_result=expr_result, rtype=rtype
        )
        if rtype not in functions.BUILTIN_TYPE_NAPPING:
            self.add(
                status="error",
                msg=f"Invalid type {rtype!r} in trailing cast expression."
                f" Valid types are {list(functions.BUILTIN_TYPE_NAPPING.keys())}",
                type=ExprType.TYPECAST,
            )
        else:
            self.add(status="success", type=ExprType.TYPECAST)

    def add(
        self,
        status: Literal["success", "error", "pending"],
        msg: str = "",
        type: ExprType = ExprType.GENERIC,
    ) -> None:
        self._results.append(ExprValidationResult(status=status, msg=msg, type=type))

    def results(self) -> Iterator[ExprValidationResult]:
        """Return all validation results."""
        yield from chain(self._task_group.results(), self._results)

    def errors(self) -> list[ExprValidationResult]:
        """Return all validation errors."""
        return [res for res in self.results() if res.status == "error"]

    def visit_action_expr(self, expr: str) -> None:
        self.logger.trace("Visit action expression", expr=expr)
        ref, prop, *_ = expr.split(".")
        if ref not in self._context.action_refs:
            self.add(
                status="error",
                msg=f"Invalid action reference {ref!r} in ACTION expression {expr!r}",
                type=ExprType.ACTION,
            )
        elif prop not in DSLNodeResult.__annotations__:
            valid = list(DSLNodeResult.__annotations__.keys())
            self.add(
                status="error",
                msg=f"Invalid property {prop!r} for action reference {ref!r} in ACTION expression {expr!r}."
                f" Use one of {valid}, e.g. `{ref}.{valid[0]}`",
                type=ExprType.ACTION,
            )
        else:
            self.add(status="success", type=ExprType.ACTION)

    def visit_secret_expr(self, expr: str) -> None:
        self.logger.trace("Visit secret expression", expr=expr)
        # These are secrets used in the DSL args, different from those required by UDFs
        # For each secret, check if it exists in the secrets manager
        try:
            name, key = expr.split(".")
        except ValueError:
            return self.add(
                status="error",
                msg=f"Invalid secret usage: {expr!r}. Must be in the format `SECRETS.my_secret.KEY`",
                type=ExprType.SECRET,
            )

        coro = self._validators[ExprType.SECRET](name, key)
        self._task_group.create_task(coro)

    def visit_input_expr(self, expr: str) -> None:
        self.logger.trace("Visit input expression", expr=expr)
        # Check that the input exists in the inputs context
        try:
            functions.eval_jsonpath(expr, self._context.inputs_context)
            self.add(status="success", type=ExprType.INPUT)
        except TracecatExpressionError as e:
            return self.add(
                status="error",
                msg=f"Invalid input expression: {expr!r}. {e}",
                type=ExprType.INPUT,
            )

    def visit_trigger_expr(self, expr: str) -> None:
        self.logger.trace("Visit trigger expression", expr=expr)
        self.add(status="success", type=ExprType.TRIGGER)

    def visit_env_expr(self, expr: str) -> None:
        self.logger.trace("Visit env expression", expr=expr)
        self.add(status="success", type=ExprType.ENV)

    def visit_local_vars_expr(self, expr: str) -> None:
        self.logger.trace("Visit local vars expression", expr=expr)
        self.add(status="success", type=ExprType.LOCAL_VARS)

    def visit_function_expr(self, expr: str, fn_name: str, fn_args: list[Any]) -> None:
        # All the args have been resolved
        is_mapped = fn_name.endswith(".map")
        fn_name = fn_name.rsplit(".", 1)[0] if is_mapped else fn_name
        self.logger.trace(
            "Visit function expression",
            expr=expr,
            fn_name=fn_name,
            fn_args=fn_args,
            is_mapped=is_mapped,
        )

        if fn_name not in functions.FUNCTION_MAPPING:
            self.add(
                status="error",
                msg=f"Unknown function name {fn_name!r} in function expression {expr!r} ({is_mapped=})",
                type=ExprType.FUNCTION,
            )
        else:
            self.add(status="success", type=ExprType.FUNCTION)

    def visit_iterator_expr(
        self, iter_var_expr: str, collection: Iterable[ExprValidationResult]
    ) -> None:
        self.logger.trace(
            "Visit iterator expression",
            iter_var_expr=iter_var_expr,
            collection=collection,
        )
        if not re.match(r"^var\.", iter_var_expr):
            self.add(
                status="error",
                msg=f"Invalid iterator variable: {iter_var_expr!r}."
                " Please use `var.your.variable`",
                type=ExprType.ITERATOR,
            )
        # Ensure that our collection is an iterable
        # We have to evaluate the collection expression
        elif not hasattr(collection, "__iter__"):
            self.add(
                status="error",
                msg=f"Invalid iterator collection: {collection!r}. Must be an iterable.",
                type=ExprType.ITERATOR,
            )
        else:
            self.add(status="success", type=ExprType.ITERATOR)

    def visit_ternary_expr(
        self, cond_expr: str, true_expr: str, false_expr: str
    ) -> None:
        self.logger.trace(
            "Visit ternary expression",
            cond_expr=cond_expr,
            true_expr=true_expr,
            false_expr=false_expr,
        )
        self.add(status="success", type=ExprType.TERNARY)

    def visit_cast_expr(self, expr: Any, typename: str) -> None:
        self.logger.trace("Visit cast expression", expr=expr, typename=typename)
        if typename not in functions.BUILTIN_TYPE_NAPPING:
            return self.add(
                status="error",
                msg=f"Invalid type {typename!r} in expression {expr!r}."
                f" Valid types are {list(functions.BUILTIN_TYPE_NAPPING.keys())}",
                type=ExprType.TYPECAST,
            )

        # Expr can be anything. Check if it's castable
        # If expr is None, means it's not concrete and we can't check it here
        if expr is None:
            return self.add(status="success", type=ExprType.TYPECAST)
        try:
            functions.BUILTIN_TYPE_NAPPING[typename](expr)
            self.add(status="success", type=ExprType.TYPECAST)
        except Exception:
            self.add(
                status="error",
                msg=f"Invalid cast expression of value {expr!r} into type {typename!r}",
                type=ExprType.TYPECAST,
            )

    def visit_literal_expr(self, expr: str) -> Any | None:
        self.logger.trace("Visit literal expression", expr=expr)

        def _eval():
            if match := re.match(patterns.STRING_LITERAL, expr):
                return match.group("str_literal").strip()
            if expr in ("True", "False"):
                # Boolean literal
                return expr == "True"
            if expr == "None":
                return None
            if "." in expr:
                return float(expr)
            return int(expr)

        try:
            result = _eval()
            self.add(status="success", type=ExprType.LITERAL)
            # Actually return the literal value
            return result
        except ValueError:
            self.add(
                status="error",
                msg=f"Invalid literal expression: {expr!r}",
                type=ExprType.LITERAL,
            )
