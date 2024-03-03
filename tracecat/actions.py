from __future__ import annotations

import re
import textwrap
from collections.abc import Awaitable, Callable
from functools import partial
from typing import Any, Literal, TypeVar
from uuid import uuid4

import httpx
import jsonpath_ng
from pydantic import BaseModel, Field
from tenacity import retry, stop_after_attempt, wait_exponential

from tracecat.config import MAX_RETRIES
from tracecat.llm import DEFAULT_MODEL_TYPE, ModelType, async_openai_call
from tracecat.logger import standard_logger
from tracecat.types import TemplatedField

logger = standard_logger(__name__)

# TODO: Add support for the rest of the Actions
ActionType = Literal[
    "webhook",
    "http_request",
    "condition",
    "llm",
    "receive_email",
    "send_email",
    "transform",
]

ALNUM_AND_WHITESPACE_PATTERN = r"^[a-zA-Z0-9\s]+$"
# ACtion ID = Hexadecimal workflow ID + lower snake case action title
ACTION_ID_PATTERN = r"^[a-zA-Z0-9]+\.[a-z0-9\_]+$"


class Action(BaseModel):
    """An action in a workflow graph.

    An action is an instance of a Action with templated fields."""

    id: str = Field(pattern=ACTION_ID_PATTERN, max_length=50)
    type: ActionType
    # Only alphanumeric characters and spaces
    title: str = Field(pattern=ALNUM_AND_WHITESPACE_PATTERN, max_length=50)
    tags: dict[str, Any] | None = None
    # Templated variables to be replaced with actual values
    # based on the results of the previous step
    templated_fields: list[TemplatedField] = Field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Action:
        action_type = data.pop("type")
        action_cls = ACTION_FACTORY[action_type]
        return action_cls(**data)

    @property
    def workflow_run_id(self) -> str:
        return action_id_to_workflow_run_id(self.id)

    @property
    def action_key(self) -> str:
        return action_id_to_action_key(self.id)


class ActionResult(BaseModel):
    """The result of an action."""

    id: str = Field(default_factory=lambda: uuid4().hex)
    action_id: str = Field(pattern=ACTION_ID_PATTERN, max_length=50)
    action_title: str = Field(pattern=ALNUM_AND_WHITESPACE_PATTERN, max_length=50)
    data: dict[str, Any] = Field(default_factory=dict)
    should_continue: bool = True

    @property
    def workflow_run_id(self) -> str:
        return action_id_to_workflow_run_id(self.action_id)

    @property
    def action_key(self) -> str:
        return action_id_to_action_key(self.action_id)


class WebhookAction(Action):
    type: Literal["webhook"] = Field("webhook", frozen=True)

    url: str | None = None
    method: Literal["GET", "POST"] = "POST"


class HTTPRequestAction(Action):
    type: Literal["http_request"] = Field("http_request", frozen=True)

    url: str | None = None
    method: Literal["GET", "POST", "PUT", "PATCH", "DELETE"] = "GET"
    headers: dict[str, str] = Field(default_factory=dict)
    payload: dict[str, Any] = Field(default_factory=dict)


class ConditionAction(Action):
    type: Literal["condition"] = Field("condition", frozen=True)

    # TODO: Replace placeholder
    event: str | None = None


class LLMAction(Action):
    """
    Represents an LLM action.

    Attributes:
        type (Literal["llm"]): The type of the action, which is always "llm".
        instructions (str): The instructions for the LLM action.
        system_context (str | None): The system context for the LLM action, if any.
        model (ModelType): The model type for the LLM action.
        response_schema (dict[str, Any] | None): The response schema for the LLM action, if any.
        kwargs (dict[str, Any] | None): Additional keyword arguments for the LLM action, if any.
    """

    type: Literal["llm"] = Field("llm", frozen=True)

    instructions: str
    system_context: str | None = None
    model: ModelType = DEFAULT_MODEL_TYPE
    response_schema: dict[str, Any] | None = None
    kwargs: dict[str, Any] | None = None


ActionTrail = dict[str, ActionResult]
ActionSubclass = WebhookAction | HTTPRequestAction | ConditionAction | LLMAction


ACTION_FACTORY: dict[str, type[Action]] = {
    "webhook": WebhookAction,
    "http_request": HTTPRequestAction,
    "condition": ConditionAction,
    "llm": LLMAction,
}


def action_id_to_workflow_run_id(action_id: str) -> str:
    return action_id.split(".")[0]


def action_id_to_action_key(action_id: str) -> str:
    return action_id.split(".")[1]


DEFAULT_TEMPLATE_PATTERN = re.compile(r"{{\s*(?P<jsonpath>.*?)\s*}}")


def evaluate_jsonpath_str(
    match: re.Match[str],
    action_trail: dict[str, Any],
    regex_group: str = "jsonpath",
) -> str:
    """Replacement function to be used with re.sub()."""
    jsonpath = match.group(regex_group)
    jsonpath_expr = jsonpath_ng.parse(jsonpath)
    matches = [found.value for found in jsonpath_expr.find(action_trail)]
    if len(matches) == 0:
        logger.debug(f"No match found for {jsonpath}.")
        return match.group(0)
    elif len(matches) == 1:
        logger.debug(f"Match found for {jsonpath}: {matches[0]}.")
        return str(matches[0])
    else:
        logger.debug(f"Multiple matches found for {jsonpath}: {matches}.")
        return str(matches)


T = TypeVar("T", str, list[Any], dict[str, Any])


def evaluate_jsonpath(
    obj: T,
    pattern: re.Pattern[str],
    evaluator: Callable[[re.Match[str]], str],
) -> T:
    """Process jsonpaths in strings, lists, and dictionaries."""
    if isinstance(obj, str):
        return pattern.sub(evaluator, obj)
    elif isinstance(obj, list):
        return [evaluate_jsonpath(item, pattern, evaluator) for item in obj]
    elif isinstance(obj, dict):
        return {
            evaluate_jsonpath(k, pattern, evaluator): evaluate_jsonpath(
                v, pattern, evaluator
            )
            for k, v in obj.items()
        }
    else:
        return obj


def evaluate_templated_fields(
    action_kwargs: dict[str, Any],
    action_trail_json: dict[str, Any],
    template_pattern: re.Pattern[str] = DEFAULT_TEMPLATE_PATTERN,
) -> dict[str, Any]:
    """Populate the templated fields with actual values."""

    processed_kwargs = {}
    jsonpath_str_evaluator = partial(
        evaluate_jsonpath_str, action_trail=action_trail_json
    )

    for field_name, field_value in action_kwargs.items():
        logger.debug(f"{field_name = } {field_value = }")

        processed_kwargs[field_name] = evaluate_jsonpath(
            field_value,
            template_pattern,
            jsonpath_str_evaluator,
        )
    return processed_kwargs


async def run_action(
    type: ActionType,
    id: str,
    title: str,
    action_trail: dict[str, ActionResult],
    templated_fields: list[TemplatedField],
    tags: dict[str, Any] | None = None,
    **action_kwargs: Any,
) -> ActionResult:
    """Run an action.

    In this step we should populate the templated fields with actual values.
    Each action should only receive the actual values it needs to run.

    Actions
    -------
     - webhook: Forward the data in the POST body to the next node
    - http_equest: Send an HTTP request to the specified URL, then parse the result.
    - conditional: Conditional logic to trigger other actions based on the result of the previous action.
    - llm: Apply a language model to the data.
    - receive_email: Receive an email and parse the data.
    - send_email: Send an email.
    - transform: Apply a transformation to the data.
    """

    logger.debug(f"Running action {title} with id {id} of type {type}.")
    action_runner = ACTION_RUNNER_FACTORY[type]

    action_trail_json = {
        result.action_key: result.data for result in action_trail.values()
    }
    logger.debug(f"{action_trail_json = }")
    processed_action_kwargs = evaluate_templated_fields(
        action_kwargs, action_trail_json
    )
    logger.debug(f"{processed_action_kwargs = }")

    try:
        result = await action_runner(
            action_trail=action_trail, **processed_action_kwargs
        )
    except Exception as e:
        logger.error(f"Error running action {title} with id {id}.", exc_info=e)
        raise
    return ActionResult(action_id=id, action_title=title, data=result)


async def run_webhook_action(
    action_trail: ActionTrail, url: str, method: str
) -> dict[str, Any]:
    """Run a webhook action."""
    logger.debug("Perform webhook action")
    logger.debug(f"{url = }")
    logger.debug(f"{method = }")
    return {"data": "test_webhook"}


@retry(
    stop=stop_after_attempt(MAX_RETRIES),
    wait=wait_exponential(multiplier=1, min=4, max=10),
)
async def run_http_request_action(
    action_trail: ActionTrail,
    url: str,
    method: str,
    headers: dict[str, str] | None,
    payload: dict[str, str | bytes] | None,
) -> dict[str, Any]:
    """Run an HTTP request action."""
    logger.debug("Perform HTTP request action")
    logger.debug(f"{url = }")
    logger.debug(f"{method = }")
    logger.debug(f"{headers = }")
    logger.debug(f"{payload = }")

    try:
        async with httpx.AsyncClient(http2=True) as client:
            response = await client.request(
                method=method,
                url=url,
                headers=headers,
                data=payload,
            )
        response.raise_for_status()
        data: dict[str, Any] = response.json()
    except httpx.HTTPStatusError as e:
        logger.error(f"HTTP request failed with status {e.response.status_code}.")
        raise
    return data


async def run_conditional_action(
    action_trail: ActionTrail, event: str
) -> dict[str, Any]:
    """Run a conditional action."""
    logger.debug(f"Run conditional event {event}.")
    return {"data": "test_conditional"}


async def run_llm_action(
    action_trail: ActionTrail,
    instructions: str,
    system_context: str | None = None,
    model: ModelType = DEFAULT_MODEL_TYPE,
    response_schema: dict[str, Any] | None = None,
    kwargs: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run an LLM action."""
    logger.debug("Perform LLM action")
    logger.debug(f"{instructions = }")
    logger.debug(f"{response_schema = }")

    system_context = (
        "You are an expert decision maker and instruction follower."
        " You will be given JSON data as context to help you make a decision."
    )
    kwargs = kwargs or {}
    if response_schema is None:
        prompt = textwrap.dedent(
            f"""

            Your objective is the following: {instructions}

            You have also been provided with the following JSON data of the previous task execution results.
            The keys are the action ids and the values are the results of the actions.
            ```
            {action_trail}
            ```

            You must complete the objective using the past task execution data.
            """
        )
        logger.debug(f"Prompt: {prompt}")
        text_response: str = await async_openai_call(
            prompt=prompt,
            model=model,
            system_context=system_context,
            response_format="text",
            **kwargs,
        )
        return {"response": text_response}
    else:
        prompt = textwrap.dedent(
            f"""

            Your objective is the following: {instructions}

            You have also been provided with the following JSON data of the previous task execution results:
            ```
            {action_trail}
            ```

            You must complete the objective using the past task execution data.

            Create a `JSONDataResponse` according to the following pydantic model:
            ```
            class JSONDataResponse(BaseModel):
            {"\n".join(f"\t{k}: {v}" for k, v in response_schema.items())}
            ```
            """
        )
        logger.debug(f"Prompt: {prompt}")
        json_response: dict[str, Any] = await async_openai_call(
            prompt=prompt,
            model=model,
            system_context=system_context,
            response_format="json_object",
            **kwargs,
        )
        if "JSONDataResponse" in json_response:
            inner_dict: dict[str, str] = json_response["JSONDataResponse"]
            return inner_dict
        return json_response


_ActionRunner = Callable[..., Awaitable[dict[str, Any]]]

ACTION_RUNNER_FACTORY: dict[ActionType, _ActionRunner] = {
    "webhook": run_webhook_action,
    "http_request": run_http_request_action,
    "condition": run_conditional_action,
    "llm": run_llm_action,
}
