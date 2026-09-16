"""Select the action results needed to evaluate a workflow return value."""

import re
from typing import Any

from lark import Token, Tree

from tracecat.dsl.schemas import ExecutionContext
from tracecat.exceptions import TracecatExpressionError
from tracecat.expressions.core import Expression, ExprExtractor
from tracecat.parse import traverse_expressions


class _ReturnActionExtractor(ExprExtractor[set[str] | None]):
    """Return static action refs, or None when a path can span multiple actions."""

    def __init__(self) -> None:
        self._refs: set[str] | None = set()

    def results(self) -> set[str] | None:
        return self._refs

    def actions(self, node: Tree[Token]) -> None:
        if self._refs is None:
            return
        path = node.children[0]
        # Only prune paths rooted at one unquoted action ref. Root wildcards,
        # recursive descent, and bracket selectors retain the full context.
        if isinstance(path, Token) and (
            match := re.match(r"^\.([a-zA-Z_][a-zA-Z0-9_]*)(?=\.|\[|$)", path)
        ):
            self._refs.add(match[1])
        else:
            self._refs = None


def build_return_context(obj: Any, context: ExecutionContext) -> ExecutionContext:
    """Shallow-copy context with only statically referenced action results.

    Keep non-action namespaces intact. Unsupported selectors retain all actions;
    malformed expressions are left to the return activity's error boundary.
    Result data is neither traversed nor materialized here.
    """
    extractor = _ReturnActionExtractor()
    try:
        for expression in traverse_expressions(obj):
            Expression(expression, visitor=extractor).visit()
    except TracecatExpressionError:
        return context

    refs = extractor.results()
    if refs is None:
        return context

    operand = context.copy()
    operand["ACTIONS"] = {
        ref: result for ref, result in context["ACTIONS"].items() if ref in refs
    }
    return operand
