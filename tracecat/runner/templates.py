import asyncio
import re
from collections.abc import Awaitable, Callable
from functools import partial
from typing import Any, TypeVar

import httpx
import jsonpath_ng
from jsonpath_ng.exceptions import JsonPathParserError

from tracecat.auth import AuthenticatedAPIClient
from tracecat.contexts import ctx_session_role
from tracecat.db import Secret
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


def _evaluate_templated_dict(
    obj: dict[str, T], operator: Callable[[re.Match[str]], str]
) -> dict[str, T]:
    return {
        _evaluate_nested_templates_rec(k, operator): _evaluate_nested_templates_rec(
            v, operator
        )
        for k, v in obj.items()
    }


def _evaluate_nested_templates_rec(
    obj: T,
    operator: Callable[[re.Match[str]], str],
) -> T:
    """Process jsonpaths in strings, lists, and dictionaries."""
    match obj:
        case str():
            # Matches anything in {{ ... }}
            return operator(obj)
        case list():
            return [_evaluate_nested_templates_rec(item, operator) for item in obj]
        case dict():
            return _evaluate_templated_dict(obj, operator)
        case _:
            return obj


def evaluate_templated_fields(
    *,
    templated_fields: dict[str, Any],
    source_data: dict[str, Any],
    template_pattern: re.Pattern[str] = JSONPATH_TEMPLATE_PATTERN,
) -> dict[str, Any]:
    """Populate templated fields with actual values."""

    jsonpath_str_evaluator = partial(_evaluate_jsonpath_str, action_trail=source_data)

    def operator(obj: T) -> str:
        return template_pattern.sub(jsonpath_str_evaluator, obj)

    logger.debug(f"{"*"*10} Evaluating templated fields {"*"*10}")
    processed_kwargs = _evaluate_templated_dict(templated_fields, operator)
    return processed_kwargs


async def _load_secret(secret_name_with_key: str) -> str:
    """Load a secret on behalf of the current workflow run.

    Parameters
    ----------
    secret_name_with_key : str
        The name of the secret and the key to retrieve. Format: <secret_name>.<key>
    """
    try:
        # NOTE(perf): We can frontload these requests before starting
        # the workflow, then look up the encrypted secrets in a local cache.
        role = ctx_session_role.get()

        secret_name, key_name = secret_name_with_key.split(".")
        async with AuthenticatedAPIClient(role=role) as client:
            response = await client.get(f"/secrets/{secret_name}")
            response.raise_for_status()
        secret = Secret.model_validate_json(response.content)
        keys = secret.keys or []
        # Find the matching key name. These should be unique.
        matched_keys = [k for k in keys if k.key == key_name]
        if len(matched_keys) != 1:
            raise ValueError(
                f"{len(matched_keys)} keys found for {secret_name_with_key!r}."
            )
        return matched_keys[0].value

    except httpx.HTTPStatusError as e:
        msg = f"Error loading {secret_name_with_key!r}. {response.text}"
        logger.error(msg)
        raise ValueError(msg) from e
    except ValueError as e:
        msg = f"Could not parse secret response for {secret_name_with_key!r}."
        logger.error(msg)
        raise ValueError(msg) from e
    except Exception as e:
        logger.error(e)
        raise ValueError(f"Secret {secret_name_with_key!r} could not be loaded.") from e


async def _evaluate_secret_str(
    templated_str: str,
    *,
    template_pattern: re.Pattern[str] = SECRET_TEMPLATE_PATTERN,
    regex_group: str = "secret_name",
    secret_getter: Awaitable[str, str] = _load_secret,
) -> str:
    """2 pass: 1. Compute all the secret values 2. Substitute the secret value."""

    # NOTE: Need to call a funciton repl to evaluate multiple appearances
    matches = [
        match.group(regex_group) for match in template_pattern.finditer(templated_str)
    ]
    logger.debug(f"{"*"*10} Evaluating secrets {"*"*10}")
    tasks = [secret_getter(m) for m in matches]
    secret_values = await asyncio.gather(*tasks)
    replacement_map = dict(zip(matches, secret_values, strict=True))

    def evaluator(match: re.Match[str]) -> str:
        key = match.group(regex_group)
        return replacement_map[key]

    replaced_str = template_pattern.sub(evaluator, templated_str)

    return replaced_str


async def _async_evaluate_dict(
    obj: dict[str, T], operator: Awaitable[re.Match[str], str]
) -> dict[str, T]:
    """Parallelize the evaluation of the keys and values of a dictionary."""
    task_dict = {}
    async with asyncio.TaskGroup() as tg:
        for k, v in obj.items():
            key_task = tg.create_task(_async_evaluate_nested_templates_rec(k, operator))
            value_task = tg.create_task(
                _async_evaluate_nested_templates_rec(v, operator)
            )
            task_dict[key_task] = value_task
    # At this point, all the tasks have completed. Safe to call result on all.
    return {k.result(): v.result() for k, v in task_dict.items()}


async def _async_evaluate_nested_templates_rec(
    obj: T,
    operator: Awaitable[re.Match[str], str],
) -> T:
    """Process jsonpaths in strings, lists, and dictionaries."""
    match obj:
        case str():
            # Matches anything in {{ ... }}
            return await operator(obj)
        case list():
            tasks = [
                _async_evaluate_nested_templates_rec(item, operator) for item in obj
            ]
            return await asyncio.gather(*tasks)
        case dict():
            return await _async_evaluate_dict(obj, operator)
        case _:
            return obj


async def evaluate_templated_secrets(
    *,
    templated_fields: dict[str, Any],
    template_pattern: re.Pattern[str] = SECRET_TEMPLATE_PATTERN,
) -> dict[str, Any]:
    """Populate templated secrets with actual values."""

    operator = partial(_evaluate_secret_str, template_pattern=template_pattern)

    logger.debug(f"{"*"*10} Evaluating templated secrets {"*"*10}")
    processed_kwargs = await _async_evaluate_dict(templated_fields, operator)
    return processed_kwargs
