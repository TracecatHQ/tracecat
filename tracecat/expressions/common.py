from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from enum import StrEnum, auto
from typing import Any, TypeVar

import jsonpath_ng.ext
import jsonpath_ng.jsonpath as jsonpath_nodes
from jsonpath_ng.exceptions import JsonPathParserError

from tracecat.exceptions import TracecatExpressionError
from tracecat.logger import logger

# Maximum number of key segments allowed after the variable name in VARS expressions.
# This is currently limited to support `VARS.<name>.<key>` paths, and can be increased
# when deeper variable nesting is officially supported.
MAX_VARS_PATH_DEPTH = 1


class TracecatEnum(StrEnum):
    def __repr__(self) -> str:
        return str(self)


class ExprContext(TracecatEnum):
    """Expression contexts."""

    # Global contexts
    ACTIONS = "ACTIONS"
    """Actions context"""

    SECRETS = "SECRETS"
    """Secrets context"""

    VARS = "VARS"
    """Workspace variables context"""

    FN = "FN"
    """Function context"""

    ENV = "ENV"
    """Environment context"""

    TRIGGER = "TRIGGER"
    """Trigger context"""
    # Action-local variables
    LOCAL_VARS = "var"
    """Action-local variables context"""

    TEMPLATE_ACTION_INPUTS = "inputs"
    """Template action inputs context"""

    TEMPLATE_ACTION_STEPS = "steps"
    """Template action steps context"""


class ExprType(TracecatEnum):
    GENERIC = auto()
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
    TRIGGER = auto()
    TEMPLATE_ACTION_STEP = auto()
    TEMPLATE_ACTION_INPUT = auto()
    VARIABLE = auto()


VISITOR_NODE_TO_EXPR_TYPE = {
    "expression": ExprType.GENERIC,
    "actions": ExprType.ACTION,
    "secrets": ExprType.SECRET,
    "vars": ExprType.VARIABLE,
    "function": ExprType.FUNCTION,
    "inputs": ExprType.INPUT,
    "env": ExprType.ENV,
    "local_vars": ExprType.LOCAL_VARS,
    "literal": ExprType.LITERAL,
    "typecast": ExprType.TYPECAST,
    "iterator": ExprType.ITERATOR,
    "ternary": ExprType.TERNARY,
    "trigger": ExprType.TRIGGER,
}


@dataclass
class IterableExpr[T]:
    """An expression that represents an iterable collection."""

    iterator: str
    collection: Iterable[T]

    def __iter__(self) -> Iterator[tuple[str, T]]:
        for item in self.collection:
            yield self.iterator, item


K = TypeVar("K", str, StrEnum)
ExprOperand = Mapping[K, Any]


def _expr_with_context(expr: str, context_type: ExprContext | None) -> str:
    return f"{context_type}.{expr}" if context_type else expr


def eval_jsonpath(
    expr: str,
    operand: Mapping[str | StrEnum, Any],
    *,
    context_type: ExprContext | None = None,
    strict: bool = False,
) -> Any | None:
    """Evaluate a jsonpath expression on the target object (operand)."""

    if operand is None or not isinstance(operand, dict | list):
        logger.error("Invalid operand for jsonpath", operand=operand)
        raise TracecatExpressionError(
            f"A dict or list operand is required as jsonpath target. Got {type(operand)}"
        )
    try:
        # Try to evaluate the expression
        jsonpath_expr = jsonpath_ng.ext.parse(expr)
    except JsonPathParserError as e:
        logger.error(
            "Invalid jsonpath expression", expr=repr(expr), context_type=context_type
        )
        formatted_expr = _expr_with_context(expr, context_type)
        raise TracecatExpressionError(f"Invalid jsonpath {formatted_expr!r}") from e
    matches = [found.value for found in jsonpath_expr.find(operand)]

    def _contains_filter(path: jsonpath_nodes.JSONPath) -> bool:
        stack: list[jsonpath_nodes.JSONPath] = [path]
        while stack:
            current = stack.pop()
            if hasattr(current, "filter_expr"):
                return True

            for child in (
                getattr(current, "left", None),
                getattr(current, "right", None),
                getattr(current, "child", None),
                getattr(current, "expression", None),
            ):
                if isinstance(child, jsonpath_nodes.JSONPath):
                    stack.append(child)

            for children in (
                getattr(current, "fields", None),
                getattr(current, "fields_list", None),
                getattr(current, "components", None),
            ):
                if isinstance(children, list | tuple):
                    stack.extend(
                        child
                        for child in children
                        if isinstance(child, jsonpath_nodes.JSONPath)
                    )

        return False

    has_wildcard = "[*]" in expr
    has_filter = "[?(" in expr or "[?@" in expr or _contains_filter(jsonpath_expr)

    if len(matches) > 1 or has_wildcard or has_filter:
        # If there are multiple matches or array wildcard, return the list
        return matches
    elif len(matches) == 1:
        # If there is a non-array wildcard single match, return the value
        return matches[0]
    else:
        # We should only reach this point if the jsonpath didn't match
        # If there are no matches, raise an error if strict is True

        if strict:
            # We know that if this function is called, there was a templated field.
            # Therefore, it means the jsonpath was valid but there was no match.
            logger.error("Jsonpath no match", expr=repr(expr), operand=operand)
            formatted_expr = _expr_with_context(expr, context_type)
            raise TracecatExpressionError(
                f"Couldn't resolve expression {formatted_expr!r} in the context",
                detail={"expression": formatted_expr, "operand": operand},
            )
        # Return None instead of empty list
        return None
