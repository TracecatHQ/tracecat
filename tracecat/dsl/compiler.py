"""Compile expression dependencies without reading runtime action results."""

import re
from typing import Any

from lark import Token, Tree

from tracecat.dsl.common import DSLInput
from tracecat.dsl.schemas import DSLDependencyPlan
from tracecat.exceptions import TracecatExpressionError
from tracecat.expressions.core import Expression, ExprExtractor
from tracecat.parse import traverse_expressions

_ACTION_REF_PATTERN = re.compile(r"^\.([a-z0-9_]+)(?=\.|\[|$)")


class _ActionRefExtractor(ExprExtractor[set[str]]):
    def __init__(self) -> None:
        self._refs: set[str] = set()

    def results(self) -> set[str]:
        return self._refs

    def actions(self, node: Tree[Token]) -> None:
        path = node.children[0]
        if not isinstance(path, Token) or not (
            match := _ACTION_REF_PATTERN.match(path)
        ):
            raise TracecatExpressionError(
                "Action references must use ACTIONS.<action_ref> syntax"
            )
        # Named JSONPath operators can leave the selected action (e.g. `parent`).
        # Conservatively skip compilation rather than omit cross-action reads.
        if "`" in path:
            raise TracecatExpressionError(
                "Named JSONPath operators require the original action context"
            )
        self._refs.add(match[1])


def _action_refs(obj: Any, valid_refs: set[str]) -> list[str]:
    extractor = _ActionRefExtractor()
    for expression in traverse_expressions(obj):
        Expression(expression, visitor=extractor).visit()
    refs = extractor.results()
    if unknown := refs - valid_refs:
        raise TracecatExpressionError(
            f"Expressions reference unknown actions: {', '.join(sorted(unknown))}"
        )
    # Stable ordering for activity results and subsequent context construction.
    return sorted(refs)


def compile_dsl_dependencies(dsl: DSLInput) -> DSLDependencyPlan:
    """Extract direct action dependencies separately for each consumer."""
    valid_refs = {action.ref for action in dsl.actions}
    return DSLDependencyPlan(
        actions={
            action.ref: _action_refs(action.model_dump(), valid_refs)
            for action in dsl.actions
        },
        returns=_action_refs(dsl.returns, valid_refs),
    )
