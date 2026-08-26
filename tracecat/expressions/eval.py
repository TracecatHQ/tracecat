import re
from collections.abc import Callable
from functools import partial
from typing import Any

from tracecat.exceptions import TracecatExpressionError
from tracecat.expressions import patterns
from tracecat.expressions.common import ExprContext, ExprOperand, IterableExpr
from tracecat.expressions.core import (
    CollectedExprs,
    Expression,
    ExprPathCollector,
    ExprResolutionPolicy,
    SecretPathExtractor,
    TemplateExpression,
)
from tracecat.parse import traverse_expressions


def _eval_templated_obj_rec(
    obj: Any,
    operator: Callable[[str], Any],
    key_operator: Callable[[str], Any] | None = None,
) -> Any:
    """Process jsonpaths in strings, lists, and dictionaries."""
    match obj:
        case str():
            return operator(obj)
        case list():
            return [
                _eval_templated_obj_rec(item, operator, key_operator) for item in obj
            ]
        case dict():
            processed: dict[Any, Any] = {}
            for key, value in obj.items():
                processed_key = (
                    (key_operator or operator)(key) if isinstance(key, str) else key
                )
                if processed_key in processed:
                    raise TracecatExpressionError(
                        "Expression resolution produced a duplicate dictionary key",
                        detail={"code": "expression_key_collision"},
                    )
                processed[processed_key] = _eval_templated_obj_rec(
                    value,
                    operator,
                    key_operator,
                )
            return processed
        case _:
            return obj


def _eval_expression_op(
    match: re.Match[str],
    operand: ExprOperand | None,
    policy: ExprResolutionPolicy | None,
) -> str:
    expr = match.group("template")
    result = TemplateExpression(
        expr,
        operand=operand,
        policy=policy,
        standalone=False,
    ).result()
    try:
        return str(result)
    except Exception as e:
        raise ValueError(f"Error evaluating str expression: {expr!r}") from e


def _make_templated_string_operator(
    *,
    operand: ExprOperand | None,
    pattern: re.Pattern[str],
    policy: ExprResolutionPolicy | None,
) -> Callable[[str], Any]:
    evaluator = partial(_eval_expression_op, operand=operand, policy=policy)

    def operator(line: str) -> Any:
        """Evaluate one standalone or inline templated string."""
        if is_template_only(line) and len(pattern.findall(line)) == 1:
            return TemplateExpression(line, operand=operand, policy=policy).result()
        return pattern.sub(evaluator, line)

    return operator


def eval_templated_object(
    obj: Any,
    *,
    operand: ExprOperand | None = None,
    pattern: re.Pattern[str] = patterns.TEMPLATE_STRING,
    policy: ExprResolutionPolicy | None = None,
    key_policy: ExprResolutionPolicy | None = None,
) -> Any:
    """Populate templated fields with actual values."""
    operator = _make_templated_string_operator(
        operand=operand,
        pattern=pattern,
        policy=policy,
    )
    key_operator = (
        _make_templated_string_operator(
            operand=operand,
            pattern=pattern,
            policy=key_policy,
        )
        if key_policy is not None
        else None
    )

    processed_kwargs = _eval_templated_obj_rec(obj, operator, key_operator)
    return processed_kwargs


def is_template_only(template: str) -> bool:
    return template.startswith("${{") and template.endswith("}}")


def extract_templated_secrets(templated_obj: Any) -> list[str]:
    """Extract secrets from templated objects using AST parsing."""
    # Extract and parse all template expressions from all strings
    extractor = SecretPathExtractor()
    for expr_str in traverse_expressions(templated_obj):
        Expression(expr_str, visitor=extractor).visit()
    # Get the results and return only the secrets
    results = extractor.results()
    secrets = set(results.get(ExprContext.SECRETS, []))
    return sorted(secrets)


def collect_expressions(templated_obj: Any) -> CollectedExprs:
    """Collect secrets and variables from expressions."""
    visitor = ExprPathCollector()
    for expr_str in traverse_expressions(templated_obj):
        Expression(expr_str, visitor=visitor).visit()
    return visitor.results()


def extract_expressions(templated_obj: Any) -> list[Expression]:
    """Extract all templates from a templated object."""
    return [Expression(expr_str) for expr_str in traverse_expressions(templated_obj)]


def get_iterables_from_expression(
    expr: str | list[str], operand: ExprOperand
) -> list[IterableExpr[Any]]:
    iterable_exprs: IterableExpr[Any] | list[IterableExpr[Any]] = eval_templated_object(
        expr, operand=operand
    )
    if isinstance(iterable_exprs, IterableExpr):
        iterable_exprs = [iterable_exprs]
    elif not (
        isinstance(iterable_exprs, list)
        and all(isinstance(expr, IterableExpr) for expr in iterable_exprs)
    ):
        raise ValueError(
            "Invalid for_each expression. Must be an IterableExpr or a list of IterableExprs."
        )
    return iterable_exprs
