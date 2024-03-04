from __future__ import annotations

import asyncio
import logging
import random
import re
import textwrap
from collections.abc import Awaitable, Callable, Iterable
from enum import StrEnum, auto
from functools import partial
from typing import TYPE_CHECKING, Any, Literal, TypeVar
from uuid import uuid4

import httpx
import jsonpath_ng
from pydantic import BaseModel, Field
from tenacity import retry, stop_after_attempt, wait_exponential

from tracecat.config import MAX_RETRIES
from tracecat.llm import DEFAULT_MODEL_TYPE, ModelType, async_openai_call
from tracecat.logger import standard_logger

if TYPE_CHECKING:
    from tracecat.workflows import Workflow

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


class ActionRun(BaseModel):
    """A run of an action to be executed as part of a workflow run."""

    run_id: str
    run_kwargs: dict[str, Any] | None = None
    action_id: str


class ActionRunStatus(StrEnum):
    """Status of an action run."""

    QUEUED = auto()
    PENDING = auto()
    RUNNING = auto()
    FAILURE = auto()
    SUCCESS = auto()


class Action(BaseModel):
    """An action in a workflow graph.

    An action is an instance of a Action with templated fields."""

    id: str = Field(pattern=ACTION_ID_PATTERN, max_length=50)
    type: ActionType
    title: str = Field(pattern=ALNUM_AND_WHITESPACE_PATTERN, max_length=50)
    tags: dict[str, Any] | None = None

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
        """The workflow-specific unique key of the action. This is the action title in lower snake case."""
        return action_id_to_action_key(self.id)


class ActionRunResult(BaseModel):
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
    llm_kwargs: dict[str, Any] | None = None


ActionTrail = dict[str, ActionRunResult]
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
        logger.debug(f"No match found for {jsonpath}, returning the original string.")
        # NOTE: We may also benefit from checking whether there was actually
        # at least 1 template that should have been substituted.
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
    match obj:
        case str():
            return pattern.sub(evaluator, obj)
        case list():
            return [evaluate_jsonpath(item, pattern, evaluator) for item in obj]
        case dict():
            return {
                evaluate_jsonpath(k, pattern, evaluator): evaluate_jsonpath(
                    v, pattern, evaluator
                )
                for k, v in obj.items()
            }
        case _:
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

    logger.debug("**Evaluating templated fields**")
    for field_name, field_value in action_kwargs.items():
        logger.debug(f"{field_name = } {field_value = }")

        processed_kwargs[field_name] = evaluate_jsonpath(
            field_value,
            template_pattern,
            jsonpath_str_evaluator,
        )
    logger.debug(f"{"*"*10}")
    return processed_kwargs


def _get_dependencies_results(
    dependencies: Iterable[str], action_result_store: dict[str, ActionTrail]
) -> dict[str, ActionRunResult]:
    """Return a combined trail of the execution results of the dependencies.

    The keys are the action IDs and the values are the results of the actions.
    """
    combined_trail: dict[str, ActionRunResult] = {}
    for dep in dependencies:
        past_action_result = action_result_store[dep]
        combined_trail |= past_action_result
    return combined_trail


async def _wait_for_dependencies(
    dependencies: Iterable[str], task_status: dict[str, ActionRunStatus]
) -> None:
    while not all(task_status.get(d) == ActionRunStatus.SUCCESS for d in dependencies):
        await asyncio.sleep(random.uniform(0, 0.5))


async def start_action_run(
    action_run: ActionRun,
    # Shared data structures
    workflow_ref: Workflow,
    ready_jobs_queue: asyncio.Queue[ActionRun],
    running_jobs_store: dict[str, asyncio.Task[None]],
    action_result_store: dict[str, ActionTrail],
    action_run_status_store: dict[str, ActionRunStatus],
    # Dynamic data
    pending_timeout: float | None = None,
    custom_logger: logging.Logger | None = None,
) -> None:
    action_id = action_run.action_id
    upstream_deps = workflow_ref.action_dependencies[action_id]
    downstream_deps = workflow_ref.adj_list[action_id]

    custom_logger = custom_logger or logger
    custom_logger.debug(f"Action {action_id} waiting for dependencies {upstream_deps}.")
    try:
        await asyncio.wait_for(
            _wait_for_dependencies(upstream_deps, action_run_status_store),
            timeout=pending_timeout,
        )

        action_trail = _get_dependencies_results(upstream_deps, action_result_store)

        custom_logger.debug(
            f"Running action {action_id!r}. Trail {action_trail.keys()}."
        )
        action_run_status_store[action_id] = ActionRunStatus.RUNNING
        action_ref = workflow_ref.action_map[action_id]
        result = await run_action(
            custom_logger=custom_logger,
            action_trail=action_trail,
            action_run_kwargs=action_run.run_kwargs,
            **action_ref.model_dump(),
        )

        # Mark the action as completed
        action_run_status_store[action_id] = ActionRunStatus.SUCCESS

        # Store the result in the action result store.
        # Every action has its own result and the trail of actions that led to it.
        # The schema is {<action ID> : <action result>, ...}
        action_result_store[action_id] = action_trail | {action_id: result}
        custom_logger.debug(f"Action {action_id!r} completed with result {result}.")

        # Broadcast the results to the next actions and enqueue them
        for next_action_id in downstream_deps:
            if next_action_id not in action_run_status_store:
                action_run_status_store[next_action_id] = ActionRunStatus.QUEUED
                ready_jobs_queue.put_nowait(
                    ActionRun(
                        run_id=action_run.run_id,
                        action_id=next_action_id,
                    )
                )

    except TimeoutError:
        custom_logger.error(
            f"Action {action_id} timed out waiting for dependencies {upstream_deps}."
        )
    except asyncio.CancelledError:
        custom_logger.warning(f"Action {action_id!r} was cancelled.")
    except Exception as e:
        custom_logger.error(f"Action {action_id!r} failed with error {e}.")
    finally:
        if action_run_status_store[action_id] != ActionRunStatus.SUCCESS:
            # Exception was raised before the action was marked as successful
            action_run_status_store[action_id] = ActionRunStatus.FAILURE
        running_jobs_store.pop(action_id, None)
        custom_logger.debug(f"Remaining tasks: {running_jobs_store.keys()}")


async def run_action(
    type: ActionType,
    id: str,
    title: str,
    action_trail: dict[str, ActionRunResult],
    tags: dict[str, Any] | None = None,
    action_run_kwargs: dict[str, Any] | None = None,
    custom_logger: logging.Logger = logger,
    **action_kwargs: Any,
) -> ActionRunResult:
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

    custom_logger.debug(f"{"*" * 10} Running action {"*" * 10}")
    custom_logger.debug(f"{id = }")
    custom_logger.debug(f"{title = }")
    custom_logger.debug(f"{type = }")
    custom_logger.debug(f"{tags = }")
    custom_logger.debug(f"{action_run_kwargs = }")
    custom_logger.debug(f"{"*" * 20}")

    action_runner = _ACTION_RUNNER_FACTORY[type]

    action_trail_json = {
        result.action_key: result.data for result in action_trail.values()
    }
    custom_logger.debug(f"Before template eval: {action_trail_json = }")
    processed_action_kwargs = evaluate_templated_fields(
        action_kwargs, action_trail_json
    )

    # Only pass the action trail to the LLM action
    if type == "llm":
        processed_action_kwargs.update(action_trail=action_trail)
    custom_logger.debug(f"{processed_action_kwargs = }")

    try:
        result = await action_runner(
            custom_logger=custom_logger,
            action_run_kwargs=action_run_kwargs,
            **processed_action_kwargs,
        )
    except Exception as e:
        custom_logger.error(f"Error running action {title} with id {id}.", exc_info=e)
        raise
    return ActionRunResult(action_id=id, action_title=title, data=result)


async def run_webhook_action(
    url: str,
    method: str,
    action_run_kwargs: dict[str, Any] | None = None,
    custom_logger: logging.Logger = logger,
) -> dict[str, Any]:
    """Run a webhook action."""
    custom_logger.debug("Perform webhook action")
    custom_logger.debug(f"{url = }")
    custom_logger.debug(f"{method = }")
    action_run_kwargs = action_run_kwargs or {}
    custom_logger.debug(f"{action_run_kwargs = }")
    return {"url": url, "method": method, "payload": action_run_kwargs}


@retry(
    stop=stop_after_attempt(MAX_RETRIES),
    wait=wait_exponential(multiplier=1, min=4, max=10),
)
async def run_http_request_action(
    url: str,
    method: str,
    headers: dict[str, str],
    payload: dict[str, str | bytes],
    action_run_kwargs: dict[str, Any] | None = None,
    custom_logger: logging.Logger = logger,
) -> dict[str, Any]:
    """Run an HTTP request action."""
    custom_logger.debug("Perform HTTP request action")
    custom_logger.debug(f"{url = }")
    custom_logger.debug(f"{method = }")
    custom_logger.debug(f"{headers = }")
    custom_logger.debug(f"{payload = }")

    try:
        async with httpx.AsyncClient(http2=True) as client:
            response = await client.request(
                method=method,
                url=url,
                headers=headers,
                json=payload,
            )
        response.raise_for_status()
        data: dict[str, Any] = response.json()
    except httpx.HTTPStatusError as e:
        custom_logger.error(
            f"HTTP request failed with status {e.response.status_code}."
        )
        raise
    return data


async def run_conditional_action(
    event: str,
    action_run_kwargs: dict[str, Any] | None = None,
    custom_logger: logging.Logger = logger,
) -> dict[str, Any]:
    """Run a conditional action."""
    custom_logger.debug(f"Run conditional event {event}.")
    return {"data": "test_conditional"}


async def run_llm_action(
    action_trail: ActionTrail,
    instructions: str,
    system_context: str | None = None,
    model: ModelType = DEFAULT_MODEL_TYPE,
    response_schema: dict[str, Any] | None = None,
    llm_kwargs: dict[str, Any] | None = None,
    action_run_kwargs: dict[str, Any] | None = None,
    custom_logger: logging.Logger = logger,
) -> dict[str, Any]:
    """Run an LLM action."""
    custom_logger.debug("Perform LLM action")
    custom_logger.debug(f"{instructions = }")
    custom_logger.debug(f"{response_schema = }")

    llm_kwargs = llm_kwargs or {}
    system_context = (
        "You are an expert decision maker and instruction follower."
        " You will be given JSON data as context to help you complete your task."
        " You do exactly as the user asks."
    )
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
        custom_logger.debug(f"Prompt: {prompt}")
        text_response: str = await async_openai_call(
            prompt=prompt,
            model=model,
            system_context=system_context,
            response_format="text",
            **llm_kwargs,
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
        custom_logger.debug(f"Prompt: {prompt}")
        json_response: dict[str, Any] = await async_openai_call(
            prompt=prompt,
            model=model,
            system_context=system_context,
            response_format="json_object",
            **llm_kwargs,
        )
        if "JSONDataResponse" in json_response:
            inner_dict: dict[str, str] = json_response["JSONDataResponse"]
            return inner_dict
        return json_response


_ActionRunner = Callable[..., Awaitable[dict[str, Any]]]

_ACTION_RUNNER_FACTORY: dict[ActionType, _ActionRunner] = {
    "webhook": run_webhook_action,
    "http_request": run_http_request_action,
    "condition": run_conditional_action,
    "llm": run_llm_action,
}
