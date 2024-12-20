from __future__ import annotations

import re
from typing import Any

from lark import Visitor

from tracecat.expressions import patterns
from tracecat.expressions.common import ExprOperand, ExprType
from tracecat.expressions.parser.core import parser
from tracecat.expressions.parser.evaluator import ExprEvaluator
from tracecat.expressions.parser.validator import ExprValidator
from tracecat.types.exceptions import TracecatExpressionError


class Expression:
    """An expression that can be evaluated."""

    def __init__(
        self,
        expression: str,
        *,
        operand: ExprOperand | None = None,
        visitor: Visitor | None = None,
    ) -> None:
        self._expr = expression
        self._operand = operand
        self._parser = parser
        self._visitor = visitor

    def __str__(self) -> str:
        return self.__repr__()

    def __repr__(self) -> str:
        return f"Expression(expression={self._expr}, operand={self._operand})"

    def result(self) -> Any:
        """Evaluate the expression and return the result."""

        # NOTE: These exceptions are the top-level exceptions caught by the workflow engine
        try:
            parse_tree = self._parser.parse(self._expr)
        except TracecatExpressionError as e:
            raise TracecatExpressionError(
                f"Error parsing expression `{self._expr}`\n\n{e}",
                detail=str(e),
            ) from e

        try:
            visitor = ExprEvaluator(operand=self._operand)
            if parse_tree is None:
                raise ValueError(f"Parser returned None for expression `{self._expr}`")
            return visitor.evaluate(parse_tree)
        except TracecatExpressionError as e:
            raise TracecatExpressionError(
                f"Error evaluating expression `{self._expr}`\n\n{e}",
                detail=str(e),
            ) from e

    def validate(
        self,
        visitor: ExprValidator,
        *,
        loc: str | None = None,
        exclude: set[ExprType] | None = None,
    ) -> None:
        """Validate the expression."""
        # 1) Parse the expression into AST
        try:
            parse_tree = self._parser.parse(self._expr)
            if parse_tree is None:
                raise TracecatExpressionError(
                    f"Parser returned None for expression `{self._expr}`"
                )
        except TracecatExpressionError as e:
            return visitor.add(
                status="error",
                msg=f"[{loc or "parser"}]\n\nError parsing expression `{self._expr}`\n\n{e}",
            )

        # 2) Validate the AST
        try:
            return visitor.visit_with_locator(parse_tree, loc=loc, exclude=exclude)
        except TracecatExpressionError as e:
            return visitor.add(
                status="error",
                msg=f"[{loc or "validator"}]\n\nError validating expression `{self._expr}`\n\n{e}",
            )


class TemplateExpression:
    """Expression with template syntax."""

    expr: Expression

    def __init__(
        self,
        template: str,
        operand: ExprOperand | None = None,
        pattern: re.Pattern[str] = patterns.TEMPLATE_STRING,
        **kwargs,
    ) -> None:
        match = pattern.match(template)
        if match is None:
            raise TracecatExpressionError(
                f"Template expression {template!r} does not match expected pattern. "
            )
        expr = match.group("expr")
        if expr is None:
            raise TracecatExpressionError(
                f"Template expression {template!r} matched pattern but contained no expression. "
            )
        self.expr = Expression(expr, operand=operand)

    def __str__(self) -> str:
        return self.__repr__()

    def __repr__(self) -> str:
        return f"TemplateExpression(expr={self.expr})"

    def result(self) -> Any:
        return self.expr.result()
