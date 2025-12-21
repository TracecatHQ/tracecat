"""JSONPath evaluation utilities for the registry.

This module provides a lightweight JSONPath evaluation function that avoids
importing heavy tracecat modules during SDK-style invocation.
"""

from collections.abc import Mapping
from enum import StrEnum
from typing import Any

import jsonpath_ng.ext
import jsonpath_ng.jsonpath as jsonpath_nodes
from jsonpath_ng.exceptions import JsonPathParserError

from tracecat_registry._internal.exceptions import TracecatExpressionError
from tracecat_registry._internal.logger import logger


def eval_jsonpath(
    expr: str,
    operand: Mapping[str | StrEnum, Any],
    *,
    strict: bool = False,
) -> Any | None:
    """Evaluate a jsonpath expression on the target object (operand)."""

    if operand is None or not isinstance(operand, dict | list):
        logger.error(f"Invalid operand for jsonpath: {operand}")
        raise TracecatExpressionError(
            f"A dict or list operand is required as jsonpath target. Got {type(operand)}"
        )
    try:
        # Try to evaluate the expression
        jsonpath_expr = jsonpath_ng.ext.parse(expr)
    except JsonPathParserError as e:
        logger.error(f"Invalid jsonpath expression: {expr!r}")
        raise TracecatExpressionError(f"Invalid jsonpath {expr!r}") from e
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
            logger.error(f"Jsonpath no match: {expr!r} in {operand}")
            raise TracecatExpressionError(
                f"Couldn't resolve expression {expr!r} in the context",
                detail={"expression": expr, "operand": operand},
            )
        # Return None instead of empty list
        return None
