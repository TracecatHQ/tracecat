from __future__ import annotations

import abc
import re
from collections import defaultdict
from collections.abc import Mapping
from typing import Any, cast

from lark import Token, Tree, Visitor

from tracecat.expressions import patterns
from tracecat.expressions.common import ExprContext, ExprOperand, ExprType
from tracecat.expressions.parser.core import parser
from tracecat.expressions.parser.evaluator import ExprEvaluator
from tracecat.expressions.parser.validator import (
    BaseExprValidator,
    ExprValidator,
    TemplateActionExprValidator,
)
from tracecat.logger import logger
from tracecat.parse import traverse_expressions
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

    def __call__(self) -> Any:
        if self._visitor is None:
            raise TracecatExpressionError(
                "Visitor is required to evaluate an expression"
            )
        match getattr(self._visitor, "_visitor_name", None):
            case (
                ExprValidator._visitor_name | TemplateActionExprValidator._visitor_name
            ):
                return self.result()
            case ExprExtractor._visitor_name:
                visitor = cast(ExprExtractor, self._visitor)
                return self.extract(visitor)
            case _:
                raise TracecatExpressionError("Unsupported visitor")

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
        visitor: BaseExprValidator,
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
                msg=f"[{loc or 'parser'}]\n\nError parsing expression `{self._expr}`\n\n{e}",
            )

        # 2) Validate the AST
        try:
            return visitor.visit_with_locator(parse_tree, loc=loc, exclude=exclude)
        except TracecatExpressionError as e:
            return visitor.add(
                status="error",
                msg=f"[{loc or 'validator'}]\n\nError validating expression `{self._expr}`\n\n{e}",
            )

    def extract(self, visitor: ExprExtractor) -> Mapping[ExprContext, set[str]]:
        parse_tree = self._parser.parse(self._expr)
        if parse_tree is None:
            raise TracecatExpressionError(
                f"Parser returned None for expression `{self._expr}`"
            )
        try:
            visitor.visit(parse_tree)
            return visitor.results()
        except Exception as e:
            raise TracecatExpressionError(
                f"Error extracting components from expression `{self._expr}`\n\n{e}"
            ) from e


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


class ExprExtractor(Visitor, abc.ABC):
    """Extract components from an expression."""

    _visitor_name = "ExprExtractor"

    @abc.abstractmethod
    def results(self) -> Mapping[ExprContext, set[str]]:
        raise NotImplementedError


class RegistryActionExtractor(ExprExtractor):
    def __init__(self) -> None:
        self._results = defaultdict[ExprContext, set[str]](set)
        self.logger = logger.bind(visitor=self._visitor_name)

    def results(self) -> Mapping[ExprContext, set[str]]:
        return self._results

    def actions(self, node: Tree[Token]) -> None:
        token = node.children[0]
        self.logger.trace("Visit action expression", node=node, child=token)
        if not isinstance(token, Token):
            raise ValueError("Expected a string token")
        jsonpath = token.lstrip(".")
        # ACTIONS.<ref>.<jsonpath...>
        ref, _ = jsonpath.split(".", 1)
        self._results[ExprContext.ACTIONS].add(ref)

    def secrets(self, node: Tree[Token]) -> None:
        token = node.children[0]
        self.logger.trace("Visit secret expression", node=node, child=token)
        if not isinstance(token, Token):
            raise ValueError("Expected a string token")
        jsonpath = token.lstrip(".")
        # SECRETS.<secret>.<key>
        secret, _ = jsonpath.split(".", 1)
        self._results[ExprContext.SECRETS].add(secret)


def extract_expressions(args: Mapping[str, Any]) -> Mapping[ExprContext, set[str]]:
    extractor = RegistryActionExtractor()
    for expr_str in traverse_expressions(args):
        Expression(expr_str, visitor=extractor)()
    return extractor.results()
