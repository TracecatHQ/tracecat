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
import operator
import re
from collections.abc import Iterable, Sequence
from enum import IntEnum, StrEnum, auto
from typing import Any, TypeVar

import jsonpath_ng
from jsonpath_ng.exceptions import JsonPathParserError

from tracecat.templates import patterns
from tracecat.types.exceptions import TracecatExpressionError

T = TypeVar("T")


def _bool(x: Any) -> bool:
    if isinstance(x, bool):
        return x
    if isinstance(x, str):
        return x.lower() in ("true", "1")
    # Use default bool for everything else
    return bool(x)


_BUILTIN_TYPE_NAP = {
    "int": int,
    "float": float,
    "str": str,
    "bool": _bool,
    # TODO: Perhaps support for URLs for files?
}
ExprStr = str
"""An annotated template string that can be resolved into a type T."""

OperandType = dict[str, Any]
_FN_TABLE = {
    # Comparison
    "less_than": operator.lt,
    "less_than_or_equal": operator.le,
    "greater_than": operator.gt,
    "greater_than_or_equal": operator.ge,
    "not_equal": operator.ne,
    "is_equal": operator.eq,
    "not_null": lambda x: x is not None,
    "is_null": lambda x: x is None,
    # Regex
    "regex_match": lambda pattern, text: bool(re.match(pattern, text)),
    "regex_not_match": lambda pattern, text: not bool(re.match(pattern, text)),
    # Membership
    "contains": lambda item, container: item in container,
    "does_not_contain": lambda item, container: item not in container,
    # Math
    "add": operator.add,
    "sub": operator.sub,
    "mul": operator.mul,
    "div": operator.truediv,
    "mod": operator.mod,
    "pow": operator.pow,
    # Transform
    "join": lambda items, sep: sep.join(items),
    "concat": lambda *items: "".join(items),
    # Logical
    "and": lambda a, b: a and b,
    "or": lambda a, b: a or b,
    "not": lambda a: not a,
}


class Expression:
    """An expression that can be evaluated."""

    def __init__(
        self, expression: ExprStr, *, operand: OperandType | None = None
    ) -> None:
        self._expr = expression
        self._operand = operand
        self._parser = ExpressionParser(context=operand)

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
    ) -> None:
        match = pattern.match(template)
        if (expr := match.group("expr")) is None:
            raise TracecatExpressionError(f"Invalid template expression: {template!r}")
        self.expr = Expression(expr, operand=operand)

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


ExprContextType = dict[ExprContext, Any]


class ExprType(StrEnum):
    ACTION = auto()
    SECRET = auto()
    FUNCTION = auto()
    INPUT = auto()
    ENV = auto()
    VARS = auto()
    LITERAL = auto()
    TYPECAST = auto()
    ITERATOR = auto()
    TERNARY = auto()


class ParserFlags(IntEnum):
    LOOP = 1


class ExpressionParser:
    def __init__(
        self,
        context: ExprContextType,
        *,
        pattern: re.Pattern[str] = patterns.EXPRESSION_PATTERN,
        exclude: Sequence[ExprType] | None = None,
    ):
        self.context = context
        # Store the top level expression type
        # This is useful to introspect the type of the expression
        # because we don't store it as some IR/AST
        self._type = None
        self._flags = 0
        self._pattern = pattern
        self._exclude = exclude or []  # Not implemented yet

    @property
    def type(self):
        return self._type

    def parse_expr(self, expr: str):
        """Parse a single expression and print the matched parts."""
        match = self._pattern.match(expr)
        if not match:
            raise ValueError(f"Invalid expression: {expr!r}")

        matcher = match.groupdict()
        rtype = matcher.get("context_expr_rtype", None)
        match matcher:
            case {"action_expr": action_expr} if action_expr:
                self._type = ExprType.ACTION
                result = self._parse_action_expr(action_expr)
            case {"secret_expr": secret_expr} if secret_expr:
                self._type = ExprType.SECRET
                result = self._parse_secret_expr(secret_expr)
            case {
                "fn_expr": fn_expr,
                "fn_name": fn_name,
                "fn_args": fn_args,
            } if fn_expr and fn_name:
                self._type = ExprType.FUNCTION
                args = (s for s in re.split(r",\s*", fn_args) if s.strip())
                result = self._parse_function_expr(fn_name, args)
            case {"input_expr": input_expr} if input_expr:
                self._type = ExprType.INPUT
                result = self._parse_input_expr(input_expr)
            case {"trigger_expr": trigger_expr} if trigger_expr:
                self._type = ExprType.TRIGGER
                result = self._parse_trigger_expr(trigger_expr)
            case {
                "iter_var_expr": iter_var_expr,
                "iter_collection_expr": iter_collection_expr,
            } if iter_var_expr and iter_collection_expr:
                self._type = ExprType.ITERATOR
                result = self._parse_iterator_expr(iter_var_expr, iter_collection_expr)
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
                self._type = ExprType.VARS
                if self._flags & ParserFlags.LOOP:
                    # If we're inside a loop, we are assigning an action-local variable
                    result = vars_expr
                else:
                    # If we're outside a loop, we are accessing an action-local variable
                    result = self._parse_expr(vars_expr)
            case _:
                raise ValueError(f"Couldn't match: {json.dumps(matcher, indent=2)}")
        if rtype:
            return self._cast_result(result, rtype)
        return result

    def _cast_result(self, result: Any, rtype: str):
        return _BUILTIN_TYPE_NAP[rtype](result)

    def _parse_trigger_expr(self, expr: str):
        return eval_jsonpath(expr, self.context[ExprContext.TRIGGER])

    def _parse_input_expr(self, expr: str):
        return eval_jsonpath(expr, self.context[ExprContext.INPUTS])

    def _parse_env_expr(self, expr: str):
        return eval_jsonpath(expr, self.context[ExprContext.ENV])

    def _parse_action_expr(self, expr: str):
        return eval_jsonpath(expr, self.context[ExprContext.ACTIONS])

    def _parse_secret_expr(self, expr: str):
        return eval_jsonpath(expr, self.context[ExprContext.SECRETS])

    def _parse_function_expr(self, qualname: str, args: Iterable[str]):
        resolved_args = tuple(self.parse_expr(arg) for arg in args)
        result = _FN_TABLE[qualname](*resolved_args)
        return result

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

        print(f"Collection: {collection}")

        # Reset the loop flag
        self._flags &= ~ParserFlags.LOOP

    def _parse_ternary_expr(self, cond_expr: str, true_expr: str, false_expr: str):
        if bool(self.parse_expr(cond_expr)):
            return self.parse_expr(true_expr)
        return self.parse_expr(false_expr)

    def _parse_cast_expr(self, expr: str, typename: str):
        inner = self.parse_expr(expr)
        return self._cast_result(inner, typename)

    def _parse_literal_expr(self, expr: str):
        if (match := re.match(patterns.STRING_LITERAL, expr)) is not None:
            return match.group("str_literal")
        if expr in ("True", "False"):
            # Boolean literal
            return expr == "True"
        if expr == "None":
            return None
        if "." in expr:
            return float(expr)
        return int(expr)
