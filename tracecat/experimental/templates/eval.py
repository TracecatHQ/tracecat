import re
from collections.abc import Callable
from functools import partial
from typing import Any, TypeVar

from tracecat.experimental.templates import patterns
from tracecat.experimental.templates.future import TemplatedFuture

T = TypeVar("T", str, list[Any], dict[str, Any])


OperatorType = Callable[[re.Match[str]], str]
OperandType = dict[str, Any]


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


def _eval_future_op(
    match: re.Match[str],
    operand: dict[str, Any],
):
    return _eval_templated_future(match.group("template"), operand)


def _eval_templated_future(expr: str, operand: OperandType) -> Any:
    fut = TemplatedFuture(expr, operand=operand)
    return fut.result()


def eval_templated_object(
    templated_obj: Any,
    *,
    operand: OperandType,
    pattern: re.Pattern[str] = patterns.TEMPLATED_OBJ,
) -> dict[str, Any]:
    """Populate templated fields with actual values."""

    evaluator = partial(_eval_future_op, operand=operand)

    def operator(line: str) -> Any:
        """Evaluate the templated string.

        When we reach this point, the target is a string.
        The string could be an inline templated future or just the templated
        future itself.
        Case A - Inline template: "The answer is ${{42}}!!!"
        Case B - Template only: "${{42}}"

        """
        target = line.strip()
        if _is_template_only(target):
            return _eval_templated_future(target, operand)
        return pattern.sub(evaluator, target)

    processed_kwargs = _eval_templated_obj_rec(templated_obj, operator)
    return processed_kwargs


def _is_template_only(template: str) -> bool:
    return template.startswith("${{") and template.endswith("}}")


if __name__ == "__main__":
    data = {
        "ACTIONS": {
            "webhook": {
                "result": 42,
                "url": "https://example.com",
            }
        }
    }

    templates = [
        {
            "test": {
                "data": "INLINE: ${{ ACTIONS.webhook.result }}",
                "url": "${{ ACTIONS.webhook.url}}",
                "number": "${{ ACTIONS.webhook.result -> int }}",
                "number_whitespace": "${{ ACTIONS.webhook.result -> int }}",
            }
        },
        "Inline substitution ${{ ACTIONS.webhook.result}} like this",
        "${{ ACTIONS.webhook.url}}",
    ]

    res = eval_templated_object(templates, operand=data)
    import json

    print(json.dumps(res, indent=2))
