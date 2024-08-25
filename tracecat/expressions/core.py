import re
from typing import Any

from tracecat.expressions import patterns
from tracecat.expressions.parser.core import parser
from tracecat.expressions.parser.evaluator import ExprEvaluator
from tracecat.expressions.parser.validator import ExprValidator
from tracecat.expressions.shared import ExprContext
from tracecat.types.exceptions import TracecatExpressionError

OperandType = dict[str, Any]


class Expression:
    """An expression that can be evaluated."""

    def __init__(
        self,
        expression: str,
        *,
        operand: OperandType | None = None,
        **kwargs,
    ) -> None:
        self._expr = expression
        self._operand = operand
        self._parser = parser

    def __str__(self) -> str:
        return self.__repr__()

    def __repr__(self) -> str:
        return f"Expression(expression={self._expr}, operand={self._operand})"

    def result(self) -> Any:
        """Evaluate the expression and return the result."""

        try:
            parse_tree = self._parser.parse(self._expr)
        except TracecatExpressionError as e:
            raise TracecatExpressionError(
                f"[parser] Error parsing expression `{self._expr}`\n\n{e}",
                detail=str(e),
            ) from e

        try:
            visitor = ExprEvaluator(self._operand)
            return visitor.evaluate(parse_tree)
        except TracecatExpressionError as e:
            raise TracecatExpressionError(
                f"[evaluator] Error evaluating expression `{self._expr}`\n\n{e}",
                detail=str(e),
            ) from e

    def validate(self, visitor: ExprValidator, *, loc: str | None = None) -> None:
        """Validate the expression."""
        # 1) Parse the expression into AST
        try:
            parse_tree = self._parser.parse(self._expr)
        except TracecatExpressionError as e:
            return visitor.add(
                status="error",
                msg=f"[{loc or "parser"}]\n\nError parsing expression `{self._expr}`\n\n{e}",
            )

        # 2) Validate the AST
        try:
            return visitor.visit(parse_tree)
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
        operand: OperandType | None = None,
        pattern: re.Pattern[str] = patterns.TEMPLATE_STRING,
        include: set[ExprContext] | None = None,
        exclude: set[ExprContext] | None = None,
        **kwargs,
    ) -> None:
        match = pattern.match(template)
        if (expr := match.group("expr")) is None:
            raise TracecatExpressionError(f"Invalid template expression: {template!r}")
        self.expr = Expression(
            expr, operand=operand, include=include, exclude=exclude, **kwargs
        )

    def __str__(self) -> str:
        return self.__repr__()

    def __repr__(self) -> str:
        return f"TemplateExpression(expr={self.expr})"

    def result(self) -> Any:
        return self.expr.result()
