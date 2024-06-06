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
The root level namespace of the expression, e.g. "SECRETS" or "$" or "FNS".
Anything that isn't a global context like SECRETS/FNS is assumed to be a jsonpath, e.g. "$.webhook.result"

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

import operator
import re
from collections.abc import Iterable
from typing import Any, Literal, TypeVar

import jsonpath_ng
from jsonpath_ng.exceptions import JsonPathParserError

from tracecat.templates import patterns

T = TypeVar("T")


def _bool(x: Any) -> bool:
    if isinstance(x, bool):
        return x
    if isinstance(x, str):
        return x.lower() in ("true", "1")
    # Everything else is False
    return False


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
}


class Expression:
    """An expression.
    i.e. `${{ <expression> -> <type> }}`
    """

    _context: Literal["SECRETS", "FNS", "ACTIONS", "INPUTS", "ENV"]
    """The context of the expression, e.g. SECRETS, FNS, ACTIONS, INPUTS, or a jsonpath."""

    _expr: str
    """The expression to evaluate, e.g. webhook.result."""

    _operand: OperandType | None

    def __init__(
        self, expression: ExprStr, *, operand: OperandType | None = None
    ) -> None:
        self._template = expression
        match = patterns.EXPR_PATTERN.match(expression)
        if not match:
            raise ValueError(f"Invalid expression: {expression!r}")

        # Top level types
        self._context = match.group("context")
        self._expr = match.group("expr")
        self._resolve_typename = match.group("rtype")
        # If no type is specified, do not cast the result
        self._resolve_type = _BUILTIN_TYPE_NAP.get(self._resolve_typename)

        # Operand
        self._operand = operand

    def __str__(self) -> str:
        return self.__repr__()

    def __repr__(self) -> str:
        return (
            "Expression("
            f"template={self._template},"
            f" expression={self._expr},"
            f" typename={self._resolve_typename},"
            f" type={self._resolve_type}),"
            f" operand={self._operand})"
        )

    def _resolve_fn(self) -> Any:
        """Resolve a funciton expression."""

        matched_fn = patterns.EXPR_INLINE_FN.match(self._expr)
        if not matched_fn:
            raise ValueError(f"Invalid function expression: {self._expr!r}")
        fn_name = matched_fn.group("func")
        fn_args = re.split(r",\s*", matched_fn.group("args"))

        # Resolve all args into the correct type
        resolved_args = self._evaluate_function_args(fn_args)
        result = _FN_TABLE[fn_name](*resolved_args)

        return result

    def _evaluate_function_args(self, args: Iterable[str]) -> tuple[str]:
        """Evaluate function args inside a template expression"""

        return tuple(eval_inline_expression(arg, self._operand) for arg in args)

    def result(self) -> Any:
        """Evaluate the templated expression and return the result."""
        match self._context:
            case "FNS":
                ret = self._resolve_fn()
            case _:
                if not self._operand:
                    raise ValueError("Operand is required for templated jsonpath.")
                ret = eval_jsonpath(self._expr, self._operand[self._context])

        if not self._resolve_type:
            return ret
        try:
            # Attempt to cast the result into the desired type
            return self._resolve_type(ret)
        except Exception as e:
            raise ValueError(f"Could not cast {ret!r} to {self._resolve_type!r}") from e


class TemplateExpression:
    """Expression with template syntax."""

    expr: Expression

    def __init__(
        self,
        template: str,
        operand: OperandType | None = None,
        pattern: re.Pattern[str] = patterns.TEMPLATED_OBJ,
    ) -> None:
        match = pattern.match(template)
        if (expr := match.group("expr")) is None:
            raise ValueError(f"Invalid template expression: {template!r}")
        self.expr = Expression(expr, operand=operand)

    def __str__(self) -> str:
        return self.__repr__()

    def __repr__(self) -> str:
        return f"TemplateExpression(expr={self.expr})"

    def result(self) -> Any:
        return self.expr.result()


def eval_jsonpath(expr: str, operand: dict[str, Any]) -> Any:
    if operand is None or not isinstance(operand, dict):
        raise ValueError("A dict-type operand is required for templated jsonpath.")
    try:
        # Try to evaluate the expression
        jsonpath_expr = jsonpath_ng.parse(expr)
    except JsonPathParserError as e:
        raise ValueError(f"Invalid jsonpath {expr!r}") from e
    matches = [found.value for found in jsonpath_expr.find(operand)]
    if len(matches) == 1:
        return matches[0]
    elif len(matches) > 1:
        return matches
    else:
        # We know that if this function is called, there was a templated field.
        # Therefore, it means the jsonpath was valid but there was no match.
        raise ValueError(f"Operand has no path {expr!r}. Operand: {operand}.")


def eval_inline_expression(expr: str, operand: OperandType) -> Any:
    """Evaluate inline expressions like
    - Expression: (with context) e.g. 'INPUTS.result', 'ACTIONS.step2.result'
    - Inline typecast: values like e.g. 'int(5)', 'str("hello")'
    """

    if match := patterns.EXPR_PATTERN.match(expr):
        full_expr = match.group("full")
        return Expression(full_expr, operand=operand).result()

    if match := patterns.INLINE_TYPECAST.match(expr):
        type_name = match.group("type")
        value = match.group("value")
        return _BUILTIN_TYPE_NAP[type_name](value)

    raise ValueError(f"Invalid function argument: {expr!r}")
