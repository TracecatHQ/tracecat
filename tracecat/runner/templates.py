import os
import re
from collections.abc import Callable
from functools import partial
from typing import Any, TypeVar

import jsonpath_ng
from jsonpath_ng.exceptions import JsonPathParserError

from tracecat.logger import standard_logger

logger = standard_logger(__name__)

JSONPATH_TEMPLATE_PATTERN = re.compile(r"{{\s*(?P<jsonpath>.*?)\s*}}")
SECRET_TEMPLATE_PATTERN = re.compile(r"{{\s*SECRETS\.(?P<secret_name>.*?)\s*}}")

T = TypeVar("T", str, list[Any], dict[str, Any])


def _evaluate_jsonpath_str(
    match: re.Match[str],
    action_trail: dict[str, Any],
    regex_group: str = "jsonpath",
) -> str:
    """Replacement function to be used with re.sub().

    Note
    ----
    This function only gets called when there's a match, i.e. match is not None.
    This means we don't have to deal with the case where there are no templates.

    Cases
    -----
    1. Input was just a plan string. Return the original string.
    2. Input was a jsonpath. Return the value found in the action trail.

    """

    jsonpath = match.group(regex_group)
    logger.debug(f"{"*"*10} Evaluating jsonpath {jsonpath} {"*"*10}")
    try:
        jsonpath_expr = jsonpath_ng.parse(jsonpath)
    except JsonPathParserError as e:
        raise ValueError(f"Invalid jsonpath {jsonpath!r}.") from e
    logger.debug(f"{jsonpath_expr = }")
    matches = [found.value for found in jsonpath_expr.find(action_trail)]
    if len(matches) == 1:
        logger.debug(f"Match found for {jsonpath}: {matches[0]}.")
        return str(matches[0])
    elif len(matches) > 1:
        logger.debug(f"Multiple matches found for {jsonpath}: {matches}.")
        return str(matches)
    else:
        # We know that if this function is called, there was a templated field.
        # Therefore, it means the jsonpath was valid but there was no match.
        raise ValueError(
            f"jsonpath has no field {jsonpath!r}. Action trail: {action_trail}."
        )


def _evaluate_secret_str(match: re.Match[str], regex_group: str = "secret_name") -> str:
    tag = match.group(regex_group)
    logger.debug(f"{"*"*10} Evaluating secret {tag} {"*"*10}")
    secret = os.environ.get(tag)
    if secret is None:
        raise ValueError(f"Secret {tag!r} not found in environment.")
    return secret


def _evaluate_nested_templates_rec(
    obj: T,
    pattern: re.Pattern[str],
    evaluator: Callable[[re.Match[str]], str],
) -> T:
    """Process jsonpaths in strings, lists, and dictionaries."""
    match obj:
        case str():
            # Matches anything in {{ ... }}
            return pattern.sub(evaluator, obj)
        case list():
            return [
                _evaluate_nested_templates_rec(item, pattern, evaluator) for item in obj
            ]
        case dict():
            return {
                _evaluate_nested_templates_rec(
                    k, pattern, evaluator
                ): _evaluate_nested_templates_rec(v, pattern, evaluator)
                for k, v in obj.items()
            }
        case _:
            return obj


def evaluate_templated_secrets(
    *,
    templated_fields: dict[str, Any],
    template_pattern: re.Pattern[str] = SECRET_TEMPLATE_PATTERN,
) -> dict[str, Any]:
    """Populate templated secrets with actual values."""

    processed_kwargs = {}
    evaluator = partial(_evaluate_secret_str)

    logger.debug(f"{"*"*10} Evaluating templated secrets {"*"*10}")
    for field_name, field_value in templated_fields.items():
        logger.debug(f"{field_name = } {field_value = }")

        processed_kwargs[field_name] = _evaluate_nested_templates_rec(
            field_value,
            template_pattern,
            evaluator,
        )
    logger.debug(f"{"*"*10}")
    return processed_kwargs


def evaluate_templated_fields(
    *,
    templated_fields: dict[str, Any],
    source_data: dict[str, Any],
    template_pattern: re.Pattern[str] = JSONPATH_TEMPLATE_PATTERN,
) -> dict[str, Any]:
    """Populate templated fields with actual values."""

    processed_kwargs = {}
    jsonpath_str_evaluator = partial(_evaluate_jsonpath_str, action_trail=source_data)

    logger.debug(f"{"*"*10} Evaluating templated fields {"*"*10}")
    for field_name, field_value in templated_fields.items():
        logger.debug(f"{field_name = } {field_value = }")

        processed_kwargs[field_name] = _evaluate_nested_templates_rec(
            field_value,
            template_pattern,
            jsonpath_str_evaluator,
        )
    logger.debug(f"{"*"*10}")
    return processed_kwargs
