import re
from collections.abc import Callable
from functools import partial
from typing import Any, TypeVar

from tracecat.expressions import patterns
from tracecat.expressions.common import ExprOperand, IterableExpr
from tracecat.expressions.core import Expression, TemplateExpression

T = TypeVar("T", str, list[Any], dict[str, Any])


def _eval_templated_obj_rec(obj: T, operator: Callable[[str], Any]) -> T:
    """Process jsonpaths in strings, lists, and dictionaries."""
    match obj:
        case str():
            return operator(obj)
        case list():
            return [_eval_templated_obj_rec(item, operator) for item in obj]
        case dict():
            return {k: _eval_templated_obj_rec(v, operator) for k, v in obj.items()}
        case _:
            return obj


def _eval_expression_op(match: re.Match[str], operand: ExprOperand | None) -> str:
    expr = match.group("template")
    result = TemplateExpression(expr, operand=operand).result()
    try:
        return str(result)
    except Exception as e:
        raise ValueError(f"Error evaluating str expression: {expr!r}") from e


def eval_templated_object(
    obj: Any,
    *,
    operand: ExprOperand | None = None,
    pattern: re.Pattern[str] = patterns.TEMPLATE_STRING,
) -> Any:
    """Populate templated fields with actual values."""
    evaluator = partial(_eval_expression_op, operand=operand)

    def operator(line: str) -> Any:
        """Evaluate the templated string.

        When we reach this point, the target is a string.
        The string could be an inline templated future or just the templated
        future itself.
        Note that we don't remove leading/trailing whitespace from the string.
        Case A - Inline template: "The answer is ${{42}}!!!"
        Case B - Template only: "${{42}}"

        """
        if is_template_only(line) and len(pattern.findall(line)) == 1:
            # Non-inline template
            # If the template expression isn't given a reolve type, its underlying
            # value is returned as is.
            return TemplateExpression(line, operand=operand).result()
        # Inline template
        # If the template expression is inline, we evaluate the result
        # and attempt to cast each underlying value into a string.
        return pattern.sub(evaluator, line)

    processed_kwargs = _eval_templated_obj_rec(obj, operator)
    return processed_kwargs


def is_template_only(template: str) -> bool:
    return template.startswith("${{") and template.endswith("}}")


def extract_templated_secrets(
    templated_obj: Any,
    *,
    pattern: re.Pattern[str] = patterns.SECRET_SCAN_TEMPLATE,
) -> list[str]:
    """Extract secrets from templated objects."""
    secrets: set[str] = set()

    def operator(line: str) -> Any:
        """Collect secrets from the templated string."""
        for match in re.finditer(pattern, line):
            secret = match.group("secret")
            secrets.add(secret)

    _eval_templated_obj_rec(templated_obj, operator)
    return list(secrets)


def extract_expressions(templated_obj: Any) -> list[Expression]:
    """Extract all templates from a templated object."""
    exprs: list[Expression] = []

    def operator(line: str) -> None:
        exprs.extend(
            Expression(match.group("expr"))
            for match in re.finditer(patterns.TEMPLATE_STRING, line)
        )

    _eval_templated_obj_rec(templated_obj, operator)
    return exprs


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
