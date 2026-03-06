"""Lightweight templating helpers for MCP stdio env resolution."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from functools import partial
from typing import Any

from tracecat.expressions import patterns
from tracecat.expressions.common import ExprOperand
from tracecat.expressions.parser.core import parser
from tracecat.expressions.parser.evaluator import ExprEvaluator
from tracecat.parse import traverse_expressions

_SECRET_NAME_PATTERN = re.compile(r"\bSECRETS\.([A-Za-z0-9_]+)")
_VARIABLE_NAME_PATTERN = re.compile(r"\bVARS\.([A-Za-z0-9_]+)")


@dataclass(frozen=True, slots=True)
class CollectedMCPExpressions:
    """Secret and variable names referenced by MCP stdio templates."""

    secrets: set[str]
    variables: set[str]


def _evaluate_expression(expr: str, operand: ExprOperand[str] | None) -> Any:
    parse_tree = parser.parse(expr)
    if parse_tree is None:
        raise ValueError(f"Parser returned None for expression {expr!r}")
    visitor = ExprEvaluator(operand=operand)
    return visitor.evaluate(parse_tree)


def _eval_templated_obj_rec[T: (str, list[Any], dict[str, Any])](
    obj: T,
    operator: Callable[[str], Any],
    *,
    key_operator: Callable[[str], str] | None = None,
) -> T:
    match obj:
        case str():
            return operator(obj)
        case list():
            return [
                _eval_templated_obj_rec(item, operator, key_operator=key_operator)
                for item in obj
            ]
        case dict():
            return {
                (
                    key_operator(key)
                    if isinstance(key, str) and key_operator is not None
                    else operator(key)
                    if isinstance(key, str)
                    else key
                ): _eval_templated_obj_rec(
                    value,
                    operator,
                    key_operator=key_operator,
                )
                for key, value in obj.items()
            }
        case _:
            return obj


def _eval_expression_op(match: re.Match[str], operand: ExprOperand[str] | None) -> str:
    expr = match.group("expr")
    if expr is None:
        raise ValueError("Template match missing expression group")
    result = _evaluate_expression(expr, operand)
    return str(result)


def is_template_only(template: str) -> bool:
    """Return whether a string is a single `${{ ... }}` template."""
    return template.startswith("${{") and template.endswith("}}")


def eval_mcp_templated_object(
    obj: Any,
    *,
    operand: ExprOperand[str] | None = None,
    pattern: re.Pattern[str] = patterns.TEMPLATE_STRING,
) -> Any:
    """Populate templated fields with evaluated values."""
    evaluator = partial(_eval_expression_op, operand=operand)

    def operator(line: str) -> Any:
        if is_template_only(line) and len(pattern.findall(line)) == 1:
            expr_match = pattern.match(line)
            if expr_match is None:
                return line
            expr = expr_match.group("expr")
            if expr is None:
                return line
            return _evaluate_expression(expr, operand)
        return pattern.sub(evaluator, line)

    def key_operator(line: str) -> str:
        return pattern.sub(evaluator, line)

    return _eval_templated_obj_rec(obj, operator, key_operator=key_operator)


def collect_mcp_expressions(templated_obj: Any) -> CollectedMCPExpressions:
    """Collect secret and variable names from MCP stdio templates."""
    secrets: set[str] = set()
    variables: set[str] = set()
    for expr_str in traverse_expressions(templated_obj):
        secrets.update(_SECRET_NAME_PATTERN.findall(expr_str))
        variables.update(_VARIABLE_NAME_PATTERN.findall(expr_str))
    return CollectedMCPExpressions(secrets=secrets, variables=variables)
