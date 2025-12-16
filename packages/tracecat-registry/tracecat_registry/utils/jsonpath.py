"""JSONPath evaluation utilities for registry actions.

Copied from tracecat/expressions/common.py to allow registry actions
to run without importing tracecat.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from enum import StrEnum
from typing import Any

import jsonpath_ng.ext
import jsonpath_ng.jsonpath as jsonpath_nodes
from jsonpath_ng.exceptions import JsonPathParserError

from tracecat_registry.utils.exceptions import ExpressionError

logger = logging.getLogger(__name__)


class ExprContext(StrEnum):
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


def _expr_with_context(expr: str, context_type: ExprContext | None) -> str:
    return f"{context_type}.{expr}" if context_type else expr


def eval_jsonpath(
    expr: str,
    operand: Mapping[str | StrEnum, Any],
    *,
    context_type: ExprContext | None = None,
    strict: bool = False,
) -> Any | None:
    """Evaluate a jsonpath expression on the target object (operand).

    Args:
        expr: JSONPath expression to evaluate.
        operand: The dict or list to evaluate the expression against.
        context_type: Optional context type for error messages.
        strict: If True, raise an error when the expression doesn't match.

    Returns:
        The matched value(s), or None if no match and not strict.

    Raises:
        ExpressionError: If the expression is invalid or doesn't match (when strict).
    """
    if operand is None or not isinstance(operand, dict | list):
        logger.error("Invalid operand for jsonpath: %s", operand)
        raise ExpressionError(
            f"A dict or list operand is required as jsonpath target. Got {type(operand)}"
        )

    try:
        # Try to evaluate the expression
        jsonpath_expr = jsonpath_ng.ext.parse(expr)
    except JsonPathParserError as e:
        logger.error(
            "Invalid jsonpath expression: %r, context_type=%s", expr, context_type
        )
        formatted_expr = _expr_with_context(expr, context_type)
        raise ExpressionError(f"Invalid jsonpath {formatted_expr!r}") from e

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
            logger.error("Jsonpath no match: %r, operand=%s", expr, operand)
            formatted_expr = _expr_with_context(expr, context_type)
            raise ExpressionError(
                f"Couldn't resolve expression {formatted_expr!r} in the context",
                detail={"expression": formatted_expr, "operand": operand},
            )
        # Return None instead of empty list
        return None
