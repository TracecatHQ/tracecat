"""JSONPath evaluation utilities for the registry.

This module provides a lightweight JSONPath evaluation function that avoids
importing heavy tracecat modules during SDK-style invocation.
"""

import threading
from functools import lru_cache
from typing import Any

import jsonpath_ng.jsonpath as jsonpath_nodes
from jsonpath_ng.exceptions import JsonPathParserError
from jsonpath_ng.ext.parser import ExtentedJsonPathParser

from tracecat_registry._internal.exceptions import TracecatExpressionError
from tracecat_registry._internal.logger import logger

# jsonpath_ng builds a new PLY parser per parse() call, and without a shipped
# parsetab module every call re-imports the missing table module and regenerates
# the LALR table. That import-lock traffic can deadlock the process if a Temporal
# activity thread is cancelled while holding the import lock. Build one parser at
# import time (before any worker threads exist) and serialize access: PLY's
# LRParser mutates shared state during parse and is not thread-safe. Parsed
# expression trees are immutable, so caching and sharing them across threads is
# safe.
_JSONPATH_PARSER = ExtentedJsonPathParser()
_JSONPATH_PARSER_LOCK = threading.Lock()


@lru_cache(maxsize=4096)
def _parse_jsonpath(expr: str) -> jsonpath_nodes.JSONPath:
    """Parse a jsonpath expression using the shared process-wide parser."""
    with _JSONPATH_PARSER_LOCK:
        return _JSONPATH_PARSER.parse(expr)


def eval_jsonpath(
    expr: str,
    operand: dict[str, Any] | list[Any],
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
        jsonpath_expr = _parse_jsonpath(expr)
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
