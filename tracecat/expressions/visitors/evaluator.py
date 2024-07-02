"""This module has no concern over how an expression is traversed, but rather what to do when visiting each node of the expression tree."""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

from tracecat.expressions import functions, patterns
from tracecat.expressions.shared import ExprContext, ExprContextType, IterableExpr
from tracecat.expressions.visitors.base import ExprVisitor
from tracecat.logging import logger
from tracecat.types.exceptions import TracecatExpressionError


class ExprEvaluatorVisitor(ExprVisitor):
    """Evaluate the expression tree by visiting each node and returning the result."""

    _visitor_name = "ExprEvaluator"

    def __init__(self, context: ExprContextType) -> None:
        self.context = context
        self.logger = logger.bind(visitor=self._visitor_name)

    def visit_action_expr(self, expr: str) -> Any:
        self.logger.trace("Visit action expression", expr=expr)
        return functions.eval_jsonpath(expr, self.context[ExprContext.ACTIONS])

    def visit_secret_expr(self, expr: str) -> Any:
        self.logger.trace("Visit secret expression", expr=expr)
        return functions.eval_jsonpath(expr, self.context[ExprContext.SECRETS])

    def visit_input_expr(self, expr: str) -> Any:
        self.logger.trace("Visit input expression", expr=expr)
        return functions.eval_jsonpath(expr, self.context[ExprContext.INPUTS])

    def visit_trigger_expr(self, expr: str) -> Any:
        self.logger.trace("Visit trigger expression", expr=expr)
        return functions.eval_jsonpath(expr, self.context[ExprContext.TRIGGER])

    def visit_env_expr(self, expr: str) -> Any:
        self.logger.trace("Visit env expression", expr=expr)
        return functions.eval_jsonpath(expr, self.context[ExprContext.ENV])

    def visit_local_vars_expr(self, expr: str) -> Any:
        self.logger.trace("Visit local vars expression", expr=expr)
        return functions.eval_jsonpath(expr, self.context[ExprContext.LOCAL_VARS])

    def visit_function_expr(self, expr: str, fn_name: str, fn_args: list[Any]) -> Any:
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

        fn = functions.FUNCTION_MAPPING.get(fn_name)
        if fn is None:
            raise TracecatExpressionError(
                f"Unknown function {fn_name!r} in expression {expr!r}."
                f" ({is_mapped=})"
            )
        final_fn = fn.map if is_mapped else fn
        result = final_fn(*fn_args)
        self.logger.trace(f"Function {fn_name!r} returned {result!r}")
        return result

    def visit_iterator_expr(self, iter_var_expr: str, collection: Iterable[Any]) -> Any:
        self.logger.trace(
            "Visit iterator expression",
            iter_var_expr=iter_var_expr,
            collection=collection,
        )
        if not re.match(r"^var\.", iter_var_expr):
            raise ValueError(
                f"Invalid iterator variable: {iter_var_expr!r}. Please use `var.your.variable`"
            )
        # Ensure that our collection is an iterable
        # We have to evaluate the collection expression
        if not hasattr(collection, "__iter__"):
            raise ValueError(
                f"Invalid iterator collection: {collection!r}. Must be an iterable."
            )

        # Reset the loop flag
        return IterableExpr(iter_var_expr, collection)

    def visit_ternary_expr(
        self, cond_expr: str, true_expr: str, false_expr: str
    ) -> Any:
        self.logger.trace(
            "Visit ternary expression",
            cond_expr=cond_expr,
            true_expr=true_expr,
            false_expr=false_expr,
        )
        raise NotImplementedError("Ternary expressions has no implementation.")

    def visit_cast_expr(self, expr: str, typename: str) -> Any:
        self.logger.trace(f"Visit cast expression: {expr} as {typename}")
        return functions.cast(expr, typename)

    def visit_literal_expr(self, expr: str) -> None:
        self.logger.trace("Visit literal expression", expr=expr)
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
