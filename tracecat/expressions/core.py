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

        parse_tree = self._parser.parse(self._expr)
        visitor = ExprEvaluator(self._operand)
        return visitor.evaluate(parse_tree)

    def validate(self, visitor: ExprValidator) -> None:
        """Validate the expression."""
        try:
            parse_tree = self._parser.parse(self._expr)
        except TracecatExpressionError as e:
            return visitor.add(
                status="error",
                msg=f"Encountered syntax error during parsing: {e}",
            )
        return visitor.visit(parse_tree)


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
