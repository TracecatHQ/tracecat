"""A module for templated expressions.


Motivation
----------
- Formalize the templated expression syntax and vocabulary.
- By doing so, in TypeScript we can infer the final type of the expression to improve type checking and validation.
    - This helps with type checking in the UI at form submission time

Template
--------
A string with one or more templated expressions, e.g. "${{ $.webhook.result -> int }}"

Expression
----------
The constituients of the template, "${{ <expression> -> <type> }}" "$.webhook.result -> int"
The expression and type together are referred to as typed/annotated expression


Type Coercion
-------------
The type to cast the result to, e.g. "int" or "str" or "float". This is expressed using the -> operator.

Context
-------
The root level namespace of the expression, e.g. "SECRETS" or "$" or "FN".
Anything that isn't a global context like SECRETS/FN is assumed to be a jsonpath, e.g. "$.webhook.result"

Operand
-------
The data structure to evaluate the expression on, e.g. {"webhook": {"result": 42}}

Usage
-----
For secrets, you should:
1. Extract the secrets from the templated object
2. Fetch the secrets
3. Populate the templated object with the actual values

"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from enum import IntEnum, StrEnum, auto
from typing import Any, Generic, TypeVar

import jsonpath_ng
from jsonpath_ng.exceptions import JsonPathParserError

from tracecat.expressions import patterns
from tracecat.expressions.functions import BUILTIN_TYPE_NAPPING, FUNCTION_MAPPING
from tracecat.types.exceptions import TracecatExpressionError

T = TypeVar("T")

ExprStr = str
"""An annotated template string that can be resolved into a type T."""

OperandType = dict[str, Any]


class Expression:
    """An expression that can be evaluated."""

    def __init__(
        self,
        expression: ExprStr,
        *,
        operand: OperandType | None = None,
        include: set[ExprContext] | None = None,
        exclude: set[ExprContext] | None = None,
        **kwargs,
    ) -> None:
        self._expr = expression
        self._operand = operand
        self._parser = ExpressionParser(
            context=operand,
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
        return self._parser.parse_expr(self._expr)


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


def eval_jsonpath(expr: str, operand: dict[str, Any]) -> Any:
    if operand is None or not isinstance(operand, dict):
        raise TracecatExpressionError(
            "A dict-type operand is required for templated jsonpath."
        )
    try:
        # Try to evaluate the expression
        jsonpath_expr = jsonpath_ng.parse(expr)
    except JsonPathParserError as e:
        raise TracecatExpressionError(f"Invalid jsonpath {expr!r}") from e
    matches = [found.value for found in jsonpath_expr.find(operand)]
    if len(matches) == 1:
        return matches[0]
    elif len(matches) > 1:
        return matches
    else:
        # We know that if this function is called, there was a templated field.
        # Therefore, it means the jsonpath was valid but there was no match.
        raise TracecatExpressionError(
            f"Operand has no path {expr!r}. Operand: {operand}."
        )


#####################
# Expression Parser #
#####################


class ExprContext(StrEnum):
    ACTIONS = "ACTIONS"
    SECRETS = "SECRETS"
    FN = "FN"
    INPUTS = "INPUTS"
    ENV = "ENV"
    TRIGGER = "TRIGGER"
    LOCAL_VARS = "var"  # Action-local variables


ExprContextType = dict[ExprContext, Any]


class ExprType(StrEnum):
    ACTION = auto()
    SECRET = auto()
    FUNCTION = auto()
    INPUT = auto()
    ENV = auto()
    LOCAL_VARS = auto()
    LITERAL = auto()
    TYPECAST = auto()
    ITERATOR = auto()
    TERNARY = auto()


class ParserFlags(IntEnum):
    LOOP = 1


class TracecatStopParser(Exception):
    def __init__(self, depth: int):
        self.depth = depth
        super().__init__()


class ExpressionParser:
    def __init__(
        self,
        context: ExprContextType,
        *,
        pattern: re.Pattern[str] = patterns.EXPRESSION_PATTERN,
        include_contexts: set[ExprContext] | None = None,
        exclude_contexts: set[ExprContext] | None = None,
        raise_on_stop: bool = False,
    ):
        self.context = context
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

    @property
    def type(self):
        return self._type

    def parse_expr(self, expr: str, depth: int = 0):
        """Parse a single expression and print the matched parts."""
        match = self._pattern.match(expr)
        if not match:
            raise TracecatExpressionError(f"Invalid expression: {expr!r}")

        matcher = match.groupdict()
        rtype = matcher.get("context_expr_rtype", None)
        try:
            match matcher:
                case {"action_expr": action_expr} if action_expr:
                    self._type = ExprType.ACTION
                    result = self._parse_action_expr(action_expr, depth + 1)
                case {"secret_expr": secret_expr} if secret_expr:
                    self._type = ExprType.SECRET
                    result = self._parse_secret_expr(secret_expr, depth + 1)
                case {
                    "fn_expr": fn_expr,
                    "fn_name": fn_name,
                    "fn_args": fn_args,
                } if fn_expr and fn_name:
                    self._type = ExprType.FUNCTION
                    result = self._parse_function_expr(
                        fn_expr, fn_name, fn_args, depth + 1
                    )
                case {"input_expr": input_expr} if input_expr:
                    self._type = ExprType.INPUT
                    result = self._parse_input_expr(input_expr, depth + 1)
                case {"trigger_expr": trigger_expr} if trigger_expr:
                    self._type = ExprType.TRIGGER
                    result = self._parse_trigger_expr(trigger_expr, depth + 1)
                case {
                    "iter_var_expr": iter_var_expr,
                    "iter_collection_expr": iter_collection_expr,
                } if iter_var_expr and iter_collection_expr:
                    self._type = ExprType.ITERATOR
                    result = self._parse_iterator_expr(
                        iter_var_expr, iter_collection_expr
                    )
                case {
                    "ternary_true_expr": ternary_true_expr,
                    "ternary_cond_expr": ternary_cond_expr,
                    "ternary_false_expr": ternary_false_expr,
                } if ternary_true_expr and ternary_cond_expr and ternary_false_expr:
                    self._type = ExprType.TERNARY
                    result = self._parse_ternary_expr(
                        ternary_cond_expr, ternary_true_expr, ternary_false_expr
                    )
                case {
                    "cast_type": cast_type,
                    "cast_expr": cast_expr,
                } if cast_type and cast_expr:
                    self._type = ExprType.TYPECAST
                    result = self._parse_cast_expr(cast_expr, cast_type)
                case {"literal_expr": literal_expr} if literal_expr:
                    self._type = ExprType.LITERAL
                    result = self._parse_literal_expr(literal_expr)
                case {"env_expr": env_expr} if env_expr:
                    self._type = ExprType.ENV
                    result = self._parse_env_expr(env_expr)
                case {"vars_expr": vars_expr} if vars_expr:
                    # Detect that we're inside a loop
                    self._type = ExprType.LOCAL_VARS
                    if self._flags & ParserFlags.LOOP:
                        # If we're inside a loop, we are assigning an action-local variable
                        result = vars_expr
                    else:
                        # If we're outside a loop, we are accessing an action-local variable
                        result = self._parse_local_vars_expr(vars_expr)
                case _:
                    raise ValueError(f"Couldn't match: {json.dumps(matcher, indent=2)}")
            if rtype:
                return self._cast_result(result, rtype)
            return result
        except TracecatStopParser as e:
            if depth != 0:
                raise e
            if self.raise_on_stop:
                raise e
        return "${{ " + expr + " }}"

    def _cast_result(self, result: Any, rtype: str):
        return BUILTIN_TYPE_NAPPING[rtype](result)

    def _maybe_parse_jsonpath(
        self, expr: str, expr_context: ExprContext, depth: int = 0
    ):
        if expr_context in self._exclude_contexts:
            raise TracecatStopParser(depth=depth)
        return eval_jsonpath(expr, self.context[expr_context])

    def _parse_local_vars_expr(self, expr: str, depth: int = 0):
        return self._maybe_parse_jsonpath(expr, ExprContext.LOCAL_VARS, depth)

    def _parse_trigger_expr(self, expr: str, depth: int = 0):
        return self._maybe_parse_jsonpath(expr, ExprContext.TRIGGER, depth)

    def _parse_input_expr(self, expr: str, depth: int = 0):
        return self._maybe_parse_jsonpath(expr, ExprContext.INPUTS, depth)

    def _parse_env_expr(self, expr: str, depth: int = 0):
        return self._maybe_parse_jsonpath(expr, ExprContext.ENV, depth)

    def _parse_action_expr(self, expr: str, depth: int = 0):
        return self._maybe_parse_jsonpath(expr, ExprContext.ACTIONS, depth)

    def _parse_secret_expr(self, expr: str, depth: int = 0):
        return self._maybe_parse_jsonpath(expr, ExprContext.SECRETS, depth)

    def _parse_function_expr(self, expr: str, qualname: str, args: str, depth: int = 0):
        if ExprContext.FN in self._exclude_contexts:
            return expr
        resolved_args = self._parse_parameter_pack(args, depth + 1)
        result = FUNCTION_MAPPING[qualname](*resolved_args)
        return result

    def _parse_parameter_pack(self, expr: str, depth: int = 0) -> tuple:
        parts = _split_arguments(expr)
        return tuple(self.parse_expr(arg, depth + 1) for arg in parts)

    def _parse_iterator_expr(self, iter_var_expr: str, iter_collection_expr: str):
        # Mark that we're inside a loop
        self._flags |= ParserFlags.LOOP
        # Ensure that our iter_var is a `var` expression
        if not re.match(r"^var\.", iter_var_expr):
            raise ValueError(
                f"Invalid iterator variable: {iter_var_expr!r}. Please use `var.your.variable`"
            )
        # Ensure that our collection is an iterable
        # We have to evaluate the collection expression
        collection = self.parse_expr(iter_collection_expr)
        if not hasattr(collection, "__iter__"):
            raise ValueError(
                f"Invalid iterator collection: {iter_collection_expr!r}. Must be an iterable."
            )

        # Reset the loop flag
        self._flags &= ~ParserFlags.LOOP
        return IterableExpr(iter_var_expr, collection)

    def _parse_ternary_expr(self, cond_expr: str, true_expr: str, false_expr: str):
        if bool(self.parse_expr(cond_expr)):
            return self.parse_expr(true_expr)
        return self.parse_expr(false_expr)

    def _parse_cast_expr(self, expr: str, typename: str):
        inner = self.parse_expr(expr)
        return self._cast_result(inner, typename)

    def _parse_literal_expr(self, expr: str, depth: int = 0):
        if (match := re.match(patterns.STRING_LITERAL, expr)) is not None:
            return match.group("str_literal").strip()
        if (match := re.match(patterns.LIST_LITERAL, expr)) is not None:
            param_pack_str = match.group("list_literal").strip()
            args = self._parse_parameter_pack(param_pack_str, depth + 1)
            return list(args)
        if expr in ("True", "False"):
            # Boolean literal
            return expr == "True"
        if expr == "None":
            return None
        if "." in expr:
            return float(expr)
        return int(expr)


T = TypeVar("T")


def _split_arguments(arguments: str):
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


@dataclass
class IterableExpr(Generic[T]):
    """An expression that represents an iterable collection."""

    iterator: str
    collection: Iterable[T]

    def __iter__(self) -> Iterator[tuple[str, T]]:
        for item in self.collection:
            yield self.iterator, item
