from __future__ import annotations

import abc
import re
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, TypeVar

from lark import Token, Tree, Visitor

from tracecat.exceptions import TracecatExpressionError
from tracecat.expressions import patterns
from tracecat.expressions.common import ExprContext, ExprOperand, ExprType
from tracecat.expressions.parser.core import parser
from tracecat.expressions.parser.evaluator import ExprEvaluator
from tracecat.expressions.validator.validator import BaseExprValidator
from tracecat.logger import logger
from tracecat.parse import traverse_expressions

ExtractorResult = TypeVar("ExtractorResult", covariant=True)
ValidatorResult = TypeVar("ValidatorResult")


class Expression:
    """An expression that can be evaluated."""

    def __init__(
        self,
        expression: str,
        *,
        operand: ExprOperand[str] | None = None,
        visitor: Visitor[Token] | None = None,
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
        return self.visit()

    def visit(self) -> Any:
        match self._visitor:
            case BaseExprValidator():
                return self.result()
            case ExprExtractor():
                return self.extract(self._visitor)
            case None:
                raise TracecatExpressionError(
                    "Visitor is required to evaluate an expression"
                )
            case _:
                raise TracecatExpressionError(f"Unsupported visitor: {self._visitor}")

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
        visitor: BaseExprValidator[ValidatorResult],
        *,
        loc: tuple[str | int, ...] | None = None,
        exclude: set[ExprType] | None = None,
        ref: str | None = None,
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
                msg=str(e),
                ref=ref,
                loc=("parsing", self._expr),
            )

        # 2) Validate the AST
        try:
            return visitor.visit_with_locator(parse_tree, loc=loc, exclude=exclude)
        except TracecatExpressionError as e:
            return visitor.add(
                status="error",
                msg=str(e),
                ref=ref,
                loc=("validation", self._expr),
            )

    def extract(self, visitor: ExprExtractor[ExtractorResult]) -> ExtractorResult:
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
        operand: ExprOperand[str] | None = None,
        pattern: re.Pattern[str] = patterns.TEMPLATE_STRING,
        **kwargs: Any,
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


class ExprExtractor[ExtractorResult](Visitor[Token], abc.ABC):
    """Extract components from an expression."""

    _visitor_name = "ExprExtractor"

    @abc.abstractmethod
    def results(self) -> ExtractorResult:
        raise NotImplementedError


class RegistryActionExtractor(ExprExtractor[Mapping[ExprContext, set[str]]]):
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

    def vars(self, node: Tree[Token]) -> None:
        token = node.children[0]
        self.logger.trace("Visit vars expression", node=node, child=token)
        if not isinstance(token, Token):
            raise ValueError("Expected a string token")
        jsonpath = token.lstrip(".")
        var_name = jsonpath.split(".", 1)[0]
        self._results[ExprContext.VARS].add(var_name)

    def trigger(self, node: Tree[Token]) -> None:
        self.logger.trace("Visit trigger expression", node=node)
        self._results[ExprContext.TRIGGER].add("*")

    def env(self, node: Tree[Token]) -> None:
        self.logger.trace("Visit env expression", node=node)
        self._results[ExprContext.ENV].add("*")

    def local_vars(self, node: Tree[Token]) -> None:
        self.logger.trace("Visit local vars expression", node=node)
        self._results[ExprContext.LOCAL_VARS].add("*")


def extract_expressions(args: Mapping[str, Any]) -> Mapping[ExprContext, set[str]]:
    extractor = RegistryActionExtractor()
    for expr_str in traverse_expressions(args):
        Expression(expr_str, visitor=extractor).visit()
    return extractor.results()


def extract_expression_contexts(expression: str) -> Mapping[ExprContext, set[str]]:
    """Extract expression contexts from a single expression string."""
    extractor = RegistryActionExtractor()
    if match := patterns.TEMPLATE_STRING.match(expression):
        expr = match.group("expr")
        if expr is None:
            return extractor.results()
        expression = expr
    Expression(expression, visitor=extractor).visit()
    return extractor.results()


class SecretPathExtractor(ExprExtractor[Mapping[ExprContext, set[str]]]):
    """Extracts full secret paths including keys."""

    def __init__(self) -> None:
        self._results = defaultdict[ExprContext, set[str]](set)
        self.logger = logger.bind(visitor="SecretPathExtractor")

    def results(self) -> Mapping[ExprContext, set[str]]:
        return self._results

    def secrets(self, node: Tree[Token]) -> None:
        token = node.children[0]
        self.logger.trace("Visit secret expression", node=node, child=token)
        if not isinstance(token, Token):
            raise ValueError("Expected a string token")
        # Get the full path after SECRETS.
        jsonpath = token.lstrip(".")
        # Store the full path (e.g., "a.K1" not just "a")
        self._results[ExprContext.SECRETS].add(jsonpath)


@dataclass(slots=True)
class CollectedExprs:
    secrets: set[str] = field(default_factory=set)
    variables: set[str] = field(default_factory=set)


class ExprPathCollector(ExprExtractor[CollectedExprs]):
    """Collects secrets and variables from expressions."""

    def __init__(self) -> None:
        self._results = CollectedExprs()
        self.logger = logger.bind(visitor="SecretPathExtractor")

    def results(self) -> CollectedExprs:
        return self._results

    def secrets(self, node: Tree[Token]) -> None:
        token = node.children[0]
        self.logger.trace("Visit secret expression", node=node, child=token)
        if not isinstance(token, Token):
            raise ValueError("Expected a string token")
        # Get the full path after SECRETS.
        jsonpath = token.lstrip(".")
        # Store the full path (e.g., "a.K1" not just "a")
        self._results.secrets.add(jsonpath)

    def vars(self, node: Tree[Token]) -> None:
        token = node.children[0]
        self.logger.trace("Visit vars expression", node=node, child=token)
        if not isinstance(token, Token):
            raise ValueError("Expected a string token")
        jsonpath = token.lstrip(".")
        var_name = jsonpath.split(".", 1)[0]
        self._results.variables.add(var_name)
