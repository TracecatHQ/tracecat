import re
from collections.abc import Callable
from functools import partial
from typing import Any

from tracecat.expressions import patterns
from tracecat.expressions.common import ExprOperand, IterableExpr
from tracecat.expressions.core import Expression, TemplateExpression


def _eval_templated_obj_rec[T: (str, list[Any], dict[str, Any])](
    obj: T, operator: Callable[[str], Any]
) -> T:
    """Process jsonpaths in strings, lists, and dictionaries."""
    match obj:
        case str():
            return operator(obj)
        case list():
            return [_eval_templated_obj_rec(item, operator) for item in obj]
        case dict():
            return {
                operator(k) if isinstance(k, str) else k: _eval_templated_obj_rec(
                    v, operator
                )
                for k, v in obj.items()
            }
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
    pattern: re.Pattern[str] = patterns.TEMPLATE_STRING,
) -> list[str]:
    """Extract secret paths used within template expressions.

    This scans only inside ${{ ... }} blocks and collects all occurrences of
    SECRETS.<name>.<key> even when multiple appear in a single template.
    """
    secrets: set[str] = set()
    # Patterns to match quoted strings (both single and double quotes)
    # This captures content within quotes so we can exclude it from secret matching
    single_quote_pattern = re.compile(r"'[^']*'")
    double_quote_pattern = re.compile(r'"[^"]*"')
    
    inner_secret_pattern = re.compile(
        r"SECRETS\.(?P<secret>[A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*)"
    )

    def operator(line: str) -> Any:
        """Collect secrets from template expressions in the string."""
        for tmpl in re.finditer(pattern, line):
            expr = tmpl.group("expr")
            
            # Find all quoted string ranges in the expression
            quoted_ranges = []
            # Check for single-quoted strings
            for quoted_match in single_quote_pattern.finditer(expr):
                quoted_ranges.append((quoted_match.start(), quoted_match.end()))
            # Check for double-quoted strings
            for quoted_match in double_quote_pattern.finditer(expr):
                quoted_ranges.append((quoted_match.start(), quoted_match.end()))
            
            # Find all secret matches and filter out those inside quoted strings
            for match in re.finditer(inner_secret_pattern, expr):
                match_start, match_end = match.span()
                
                # Check if this match is inside any quoted string
                inside_quotes = any(
                    start <= match_start < end and start < match_end <= end
                    for start, end in quoted_ranges
                )
                
                if not inside_quotes:
                    secrets.add(match.group("secret"))

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
