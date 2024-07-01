from __future__ import annotations

import json
import re
from typing import Any, TypeVar

from tracecat.expressions import patterns
from tracecat.expressions.shared import ExprContext, ExprType
from tracecat.expressions.visitors import ExprValidatorVisitor, ExprVisitor
from tracecat.expressions.visitors.evaluator import ExprEvaluatorVisitor
from tracecat.logging import logger
from tracecat.types.exceptions import TracecatExpressionError

T = TypeVar("T")

OperandType = dict[str, Any]


class Expression:
    """An expression that can be evaluated."""

    def __init__(
        self,
        expression: str,
        *,
        operand: OperandType | None = None,
        include: set[ExprContext] | None = None,
        exclude: set[ExprContext] | None = None,
        **kwargs,
    ) -> None:
        self._expr = expression
        self._operand = operand
        self._parser = ExpressionParser(
            include_contexts=include,
            exclude_contexts=exclude,
            **kwargs,
        )

    def __str__(self) -> str:
        return self.__repr__()

    def __repr__(self) -> str:
        return f"Expression(expression={self._expr}, operand={self._operand})"

    def result(self) -> Any:
        """Evaluate the expression and return the result."""

        visitor = ExprEvaluatorVisitor(self._operand)
        return self._parser.walk_expr(self._expr, visitor=visitor)

    def validate(self, visitor: ExprValidatorVisitor) -> None:
        """Validate the expression."""
        self._parser.walk_expr(self._expr, visitor=visitor)


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


#####################
# Expression Parser #
#####################


class StopParser(Exception):
    def __init__(self, depth: int):
        self.depth = depth
        super().__init__()


class ExpressionParser:
    """Expression parser that can parse and evaluate expressions.

    Visitor pattern is used to traverse the expression tree.
    """

    def __init__(
        self,
        *,
        pattern: re.Pattern[str] = patterns.EXPRESSION_PATTERN,
        include_contexts: set[ExprContext] | None = None,
        exclude_contexts: set[ExprContext] | None = None,
        raise_on_stop: bool = False,
    ):
        self.raise_on_stop = raise_on_stop
        # Store the top level expression type
        # This is useful to introspect the type of the expression
        # because we don't store it as some IR/AST
        self._type = None
        self._flags = 0
        self._pattern = pattern
        if include_contexts is not None and exclude_contexts is not None:
            # If we have both include and exclude contexts, we take the overlap
            self._exclude_contexts = exclude_contexts | (
                set(ExprContext) - include_contexts
            )
        elif include_contexts is not None:
            # if we only have include contexts, take the difference from all contexts
            self._exclude_contexts = set(ExprContext) - include_contexts
        elif exclude_contexts is not None:
            # If we only have exclude contexts, use this
            self._exclude_contexts = exclude_contexts
        else:
            self._exclude_contexts = set()

        self.logger = logger

    @property
    def type(self) -> ExprType | None:
        return self._type

    def walk_expr(self, expr: str, visitor: ExprVisitor, depth: int = 0) -> Any:
        """Walk through the expression and visit each node using the visitor."""
        self.logger.trace("Walk expression", expr=expr, depth=depth)
        match = self._pattern.match(expr)
        if not match:
            return visitor.handle_error(expr)

        try:
            matcher = match.groupdict()
            match matcher:
                case {"action_expr": action_expr} if action_expr:
                    result = self._walk_action_expr(visitor, action_expr, depth + 1)
                case {"secret_expr": secret_expr} if secret_expr:
                    result = self._walk_secret_expr(visitor, secret_expr, depth + 1)
                case {
                    "fn_expr": fn_expr,
                    "fn_name": fn_name,
                    "fn_args": fn_args,
                } if fn_expr and fn_name:
                    result = self._walk_function_expr(
                        visitor, fn_expr, fn_name, fn_args, depth + 1
                    )
                case {"input_expr": input_expr} if input_expr:
                    result = self._walk_input_expr(visitor, input_expr, depth + 1)
                case {"trigger_expr": trigger_expr} if trigger_expr:
                    result = self._walk_trigger_expr(visitor, trigger_expr, depth + 1)
                case {
                    "iter_var_expr": iter_var_expr,
                    "iter_collection_expr": iter_collection_expr,
                } if iter_var_expr and iter_collection_expr:
                    result = self._walk_iterator_expr(
                        visitor, iter_var_expr, iter_collection_expr, depth + 1
                    )
                case {
                    "ternary_true_expr": ternary_true_expr,
                    "ternary_cond_expr": ternary_cond_expr,
                    "ternary_false_expr": ternary_false_expr,
                } if ternary_true_expr and ternary_cond_expr and ternary_false_expr:
                    result = self._walk_ternary_expr(
                        visitor,
                        cond_expr=ternary_cond_expr,
                        true_expr=ternary_true_expr,
                        false_expr=ternary_false_expr,
                        depth=depth + 1,
                    )
                case {
                    "cast_type": cast_type,
                    "cast_expr": cast_expr,
                } if cast_type and cast_expr:
                    result = self._walk_cast_expr(
                        visitor, cast_expr, cast_type, depth + 1
                    )
                case {"literal_expr": literal_expr} if literal_expr:
                    result = self._walk_literal_expr(visitor, literal_expr, depth + 1)
                case {"env_expr": env_expr} if env_expr:
                    result = self._walk_env_expr(visitor, env_expr, depth + 1)
                case {"vars_expr": vars_expr} if vars_expr:
                    result = self._walk_local_vars_expr(visitor, vars_expr, depth + 1)
                case _:
                    msg = f"Couldn't match: {json.dumps(matcher, indent=2)}"
                    self.logger.error(msg)
                    raise TracecatExpressionError(msg)
            if rtype := matcher.get("context_expr_rtype", None):
                return visitor.visit_trailing_cast_expr(result, rtype)
            return result
        except StopParser as e:
            if depth > 0 or self.raise_on_stop:
                self.logger.trace("Stop parser", expr=expr, depth=depth)
                raise e
        return "${{ " + expr + " }}"

    # These methods help to walk through the expression and visit each node using the visitor
    def _walk_action_expr(self, visitor: ExprVisitor, expr: str, depth: int):
        if ExprContext.ACTIONS in self._exclude_contexts:
            raise StopParser(depth)
        return visitor.visit_action_expr(expr)

    def _walk_secret_expr(self, visitor: ExprVisitor, expr: str, depth: int):
        if ExprContext.SECRETS in self._exclude_contexts:
            raise StopParser(depth)
        return visitor.visit_secret_expr(expr)

    def _walk_input_expr(self, visitor: ExprVisitor, expr: str, depth: int):
        if ExprContext.INPUTS in self._exclude_contexts:
            raise StopParser(depth)
        return visitor.visit_input_expr(expr)

    def _walk_trigger_expr(self, visitor: ExprVisitor, expr: str, depth: int):
        if ExprContext.TRIGGER in self._exclude_contexts:
            raise StopParser(depth)
        return visitor.visit_trigger_expr(expr)

    def _walk_env_expr(self, visitor: ExprVisitor, expr: str, depth: int):
        if ExprContext.ENV in self._exclude_contexts:
            raise StopParser(depth)
        return visitor.visit_env_expr(expr)

    def _walk_local_vars_expr(self, visitor: ExprVisitor, expr: str, depth: int):
        if ExprContext.LOCAL_VARS in self._exclude_contexts:
            raise StopParser(depth)
        return visitor.visit_local_vars_expr(expr)

    def _walk_function_expr(
        self, visitor: ExprVisitor, expr: str, fn_name: str, fn_args: str, depth: int
    ):
        if ExprContext.FN in self._exclude_contexts:
            return expr
        parsed_args = []
        for arg in split_arguments(fn_args):
            parsed_arg = self.walk_expr(arg, visitor, depth + 1)
            parsed_args.append(parsed_arg)

        return visitor.visit_function_expr(expr, fn_name, parsed_args)

    def _walk_iterator_expr(
        self,
        visitor: ExprVisitor,
        iter_var_expr: str,
        iter_collection_expr: str,
        depth: int,
    ):
        collection = self.walk_expr(iter_collection_expr, visitor, depth + 1)
        return visitor.visit_iterator_expr(iter_var_expr, collection)

    def _walk_ternary_expr(
        self,
        visitor: ExprVisitor,
        cond_expr: str,
        true_expr: str,
        false_expr: str,
        depth: int,
    ):
        condition = self.walk_expr(cond_expr, visitor, depth + 1)
        if condition:
            return self.walk_expr(true_expr, visitor, depth + 1)
        return self.walk_expr(false_expr, visitor, depth + 1)

    def _walk_cast_expr(
        self, visitor: ExprVisitor, expr: str, typename: str, depth: int
    ):
        inner = self.walk_expr(expr, visitor, depth + 1)
        return visitor.visit_cast_expr(inner, typename)

    def _walk_literal_expr(self, visitor: ExprVisitor, expr: str, depth: int):
        if (match := re.match(patterns.LIST_LITERAL, expr)) is not None:
            param_pack_str = match.group("list_literal").strip()
            args = split_arguments(param_pack_str)
            return [self.walk_expr(arg, visitor, depth + 1) for arg in args]
        return visitor.visit_literal_expr(expr)


def split_arguments(arguments: str) -> list[str]:
    """Split arguments by commas, but ignore commas inside nested structures or quoted strings."""
    parts = []
    bracket_level = 0
    in_quotes = False
    quote_char = ""
    current_part = []

    for char in arguments:
        if char in "\"'":
            if in_quotes:
                if char == quote_char:
                    in_quotes = False
                    quote_char = ""
            else:
                in_quotes = True
                quote_char = char
            current_part.append(char)
        elif char == "," and bracket_level == 0 and not in_quotes:
            parts.append("".join(current_part).strip())
            current_part = []
        else:
            if not in_quotes:
                if char in "([{":
                    bracket_level += 1
                elif char in ")]}":
                    bracket_level -= 1
            current_part.append(char)

    parts.append("".join(current_part).strip())  # Add the last part
    return parts
