import re
from collections.abc import Callable, Mapping
from functools import partial
from typing import Any, TypeVar

from tracecat.templates import patterns
from tracecat.templates.expressions import TemplateExpression

T = TypeVar("T", str, list[Any], dict[str, Any])


OperatorType = Callable[[re.Match[str]], str]
OperandType = Mapping[str, Any]


def _eval_templated_obj_rec(obj: T, operator: OperatorType) -> T:
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


def _eval_expression_op(match: re.Match[str], operand: dict[str, Any]) -> str:
    expr = match.group("template")
    result = TemplateExpression(expr, operand=operand).result()
    try:
        return str(result)
    except Exception as e:
        raise ValueError(f"Error evaluating str expression: {expr!r}") from e


def eval_templated_object(
    obj: Any,
    *,
    operand: OperandType | None = None,
    pattern: re.Pattern[str] = patterns.TEMPLATED_OBJ,
) -> dict[str, Any]:
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
        if _is_template_only(line) and len(pattern.findall(line)) == 1:
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


def _is_template_only(template: str) -> bool:
    return template.startswith("${{") and template.endswith("}}")


def extract_templated_secrets(
    templated_obj: Any,
    *,
    pattern: re.Pattern[str] = patterns.SECRET_TEMPLATE,
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
