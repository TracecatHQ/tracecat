"""Actions to bj executed as part of a workflow.


Action
------
An action is a blueprint of a task to be executed as part of a workflow.

Action Run
----------
An action run is an instance of an action to be executed as part of a workflow run.

Action Slug
-----------
Lower snake case of the action title.

Action Key
----------
The action key is a unique identifier for an action within a workflow:
action_key = <action_id>.<action_slug>
We can reverse lookup the workflow ID from the action ID.

Action Run ID
-------------
action_run_id = <action_run_prefix>:<action_key><:<workflow_run_id>

For example, "ar:689cd16eba7a4d9897074e7c7ceed797.webhook:2e682fd0500f486d8f64beb911a5a74d"

Entrypoint Key
--------------
The entrypoint key is just the action key of the entrypoint action.

Note that this is different from the action ID which is a surrogate key.
"""

from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable, Callable, Iterable
from enum import StrEnum, auto
from functools import partial
from typing import TYPE_CHECKING, Any, Literal, TypeVar
from uuid import uuid4

import httpx
from pydantic import BaseModel, Field, validator
from tenacity import retry, stop_after_attempt, wait_exponential

from tracecat.config import HTTP_MAX_RETRIES
from tracecat.contexts import ctx_action_run, ctx_session_role, ctx_workflow_run
from tracecat.db import create_vdb_conn
from tracecat.integrations import registry
from tracecat.llm import DEFAULT_MODEL_TYPE, ModelType, async_openai_call
from tracecat.logging import logger
from tracecat.runner.condition import ConditionRuleValidator, ConditionRuleVariant
from tracecat.runner.events import (
    emit_create_action_run_event,
    emit_update_action_run_event,
)
from tracecat.runner.llm import (
    TaskFields,
    TaskFieldsVariant,
    generate_pydantic_json_response_schema,
    get_system_context,
)
from tracecat.runner.mail import (
    SAFE_EMAIL_PATTERN,
    EmailBouncedError,
    EmailNotFoundError,
    ResendMailProvider,
)
from tracecat.runner.templates import (
    evaluate_templated_fields,
    evaluate_templated_secrets,
)
from tracecat.types.actions import ActionType
from tracecat.types.api import CaseContext, ListModel, RunStatus, Suppression, Tag
from tracecat.types.cases import Case

if TYPE_CHECKING:
    from tracecat.runner.workflows import Workflow


T = TypeVar("T", str, list[Any], dict[str, Any])


ALNUM_AND_WHITESPACE_PATTERN = r"^[a-zA-Z0-9\s\_]+$"
# Action Key = Hexadecimal Action ID + Action title slug
ACTION_KEY_PATTERN = r"^[a-zA-Z0-9]+\.[a-z0-9\_]+$"


ACTION_RUN_ID_PREFIX = "ar"


def action_key_to_id(action_key: str) -> str:
    return action_key.split(".")[0]


def action_key_to_slug(action_key: str) -> str:
    return action_key.split(".")[1]


def get_action_run_id(workflow_run_id: str, action_key: str) -> str:
    return f"{ACTION_RUN_ID_PREFIX}:{action_key}:{workflow_run_id}"


def parse_action_run_id(
    ar_id: str, component: Literal["action_key", "workflow_run_id"]
) -> str:
    """Parse an action run ID and return the action key or the run ID.

    Example
    -------
    >>> parse_action_run_id("ar:TEST_ACTION_ID.receive_sentry_event:WORKFLOW_RUN_ID", "action_key")
    "TEST_ACTION_ID.receive_sentry_event"
    >>> parse_action_run_id("ar:TEST_ACTION_ID.receive_sentry_event:WORKFLOW_RUN_ID", "workflow_run_id")
    "WORKFLOW_RUN_ID"
    """
    if not ar_id.startswith(f"{ACTION_RUN_ID_PREFIX}:"):
        raise ValueError(f"Invalid action run ID {ar_id!r}")
    match component:
        case "action_key":
            return ar_id.split(":")[1]
        case "workflow_run_id":
            return ar_id.split(":")[2]
        case _:
            raise ValueError(f"Invalid component {component!r}")


class ActionRun(BaseModel):
    """A run of an action to be executed as part of a workflow run."""

    workflow_run_id: str = Field(frozen=True)
    run_kwargs: dict[str, Any] | None = None
    action_key: str = Field(pattern=ACTION_KEY_PATTERN, frozen=True)

    @property
    def id(self) -> str:
        """The unique identifier of the action run.

        The action key tells us where to find the action in the workflow graph.
        The run ID tells us which workflow run the action is part of.

        We need both to uniquely identify an action run.
        """
        return get_action_run_id(self.workflow_run_id, self.action_key)

    @property
    def action_id(self) -> str:
        return action_key_to_id(self.action_key)

    def deps_downstream(self, workflow: Workflow) -> list[str]:
        downstream_deps_ar_ids = [
            get_action_run_id(self.workflow_run_id, k)
            for k in workflow.adj_list[self.action_key]
        ]
        return downstream_deps_ar_ids

    def deps_upstream(self, workflow: Workflow) -> list[str]:
        upstream_deps_ar_ids = [
            get_action_run_id(self.workflow_run_id, k)
            for k in workflow.action_dependencies[self.action_key]
        ]
        return upstream_deps_ar_ids

    def __hash__(self) -> int:
        return hash(f"{self.workflow_run_id}:{self.action_key}")

    def __eq__(self, other: Any) -> bool:
        match other:
            case ActionRun(
                workflow_run_id=self.workflow_run_id, action_key=self.action_key
            ):
                return True
            case _:
                return False


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

    key: str = Field(pattern=ACTION_KEY_PATTERN)
    type: ActionType
    title: str = Field(pattern=ALNUM_AND_WHITESPACE_PATTERN, max_length=50)
    # tags: dict[str, Any] | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Action:
        action_type = data.pop("type")
        action_cls = ACTION_FACTORY[action_type]
        return action_cls(**data)

    @property
    def id(self) -> str:
        return action_key_to_id(self.key)

    @property
    def slug(self) -> str:
        """The workflow-specific unique key of the action. This is the action slug."""
        return action_key_to_slug(self.key)


class ActionRunResult(BaseModel):
    """The result of an action."""

    id: str = Field(default_factory=lambda: uuid4().hex)
    action_key: str = Field(
        pattern=ACTION_KEY_PATTERN,
        description="Action key = '<action_id>.<action_slug>'",
    )
    output: dict[str, Any] = Field(default_factory=dict)
    should_continue: bool = True

    @property
    def action_id(self) -> str:
        return action_key_to_id(self.action_key)

    @property
    def action_slug(self) -> str:
        return action_key_to_slug(self.action_key)


# NOTE: Might want to switch out to using discriminated unions instead of subclassing
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

    condition_rules: ConditionRuleVariant = Field(..., discriminator="type")


class LLMAction(Action):
    """
    Represents an LLM action.

    Attributes:
        type (Literal["llm"]): The type of the action, which is always "llm".
        task (Literal["translate", "extract", "summarize", "label", "enrich", "question_answering"]): The task for the LLM action.
        message (str): The message for the LLM action. This is the possibly templated message that the LLM action node will receive as input.
        system_context (str | None): The system context for the LLM action, if any.
        model (ModelType): The model type for the LLM action.
        response_schema (dict[str, Any] | None): The response schema for the LLM action, if any.
        kwargs (dict[str, Any] | None): Additional keyword arguments for the LLM action, if any.
    """

    type: Literal["llm"] = Field("llm", frozen=True)

    message: str
    # Discriminated union with str discriminators
    # https://docs.pydantic.dev/latest/concepts/unions/#discriminated-unions-with-str-discriminators
    task_fields: TaskFieldsVariant = Field(..., discriminator="type")
    system_context: str | None = None
    model: ModelType = DEFAULT_MODEL_TYPE
    response_schema: dict[str, Any] | None = None
    llm_kwargs: dict[str, Any] | None = None


class SendEmailAction(Action):
    type: Literal["send_email"] = Field("send_email", frozen=True)

    # Email regex
    recipients: list[str]
    subject: str
    body: str

    @validator("recipients", always=True, pre=True, each_item=True)
    def validate_recipients(cls, v: str) -> str:
        if not SAFE_EMAIL_PATTERN.match(v):
            raise ValueError(f"Invalid email address {v!r}.")
        return v


class OpenCaseAction(Action):
    type: Literal["open_case"] = Field("open_case", frozen=True)

    case_title: str
    payload: dict[str, Any]
    malice: Literal["malicious", "benign"]
    status: Literal["open", "closed", "in_progress", "reported", "escalated"]
    priority: Literal["low", "medium", "high", "critical"]
    # Optional inputs (can be AI suggested)
    context: ListModel[CaseContext]
    action: Literal[
        "ignore", "quarantine", "informational", "sinkhole", "active_compromise"
    ]
    suppression: ListModel[Suppression]
    tags: ListModel[Tag]


class IntegrationAction(Action):
    type: Literal["integration"] = Field("integration", frozen=True)

    qualname: str  # Fully qualified name, e.g. integrations.namespace.func
    params: dict[str, Any] | None = None

    @property
    def platform(self) -> str:
        return self.qualname.split(".")[1]

    @property
    def namespace(self) -> str:
        _, *namespace_terms, _ = self.qualname.split(".")
        return ".".join(namespace_terms)


ACTION_FACTORY: dict[str, type[Action]] = {
    "webhook": WebhookAction,
    "http_request": HTTPRequestAction,
    "condition": ConditionAction,
    "llm": LLMAction,
    "send_email": SendEmailAction,
    "open_case": OpenCaseAction,
    "integration": IntegrationAction,
}


ActionTrail = dict[str, ActionRunResult]
ActionVariant = (
    WebhookAction
    | HTTPRequestAction
    | ConditionAction
    | LLMAction
    | SendEmailAction
    | OpenCaseAction
    | IntegrationAction
)


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
    upstream_deps_ar_ids: Iterable[str],
    action_run_status_store: dict[str, ActionRunStatus],
) -> None:
    while not all(
        action_run_status_store.get(ar_id) == ActionRunStatus.SUCCESS
        for ar_id in upstream_deps_ar_ids
    ):
        await asyncio.sleep(random.uniform(0, 0.5))


async def start_action_run(
    action_run: ActionRun,
    # Shared data structures
    ready_jobs_queue: asyncio.Queue[ActionRun],
    running_jobs_store: dict[str, asyncio.Task[None]],
    action_result_store: dict[str, ActionTrail],
    action_run_status_store: dict[str, ActionRunStatus],
    # Dynamic data
    pending_timeout: float | None = None,
) -> None:
    ctx_action_run.set(action_run)
    workflow = ctx_workflow_run.get().workflow
    ar_id = action_run.id
    run_status: RunStatus = "failure"
    with logger.contextualize(ar_id=ar_id):
        try:
            await emit_create_action_run_event()
            action_key = action_run.action_key
            upstream_deps_ar_ids = action_run.deps_upstream(workflow=workflow)
            logger.debug("Waiting for dependencies", deps=upstream_deps_ar_ids)

            error_msg: str | None = None
            result: ActionRunResult | None = None
            await asyncio.wait_for(
                _wait_for_dependencies(upstream_deps_ar_ids, action_run_status_store),
                timeout=pending_timeout,
            )

            action_trail = _get_dependencies_results(
                upstream_deps_ar_ids, action_result_store
            )

            logger.opt(lazy=True).debug(
                "Running action. Trail {trail}", trail=lambda: list(action_trail.keys())
            )
            action_run_status_store[ar_id] = ActionRunStatus.RUNNING
            action_ref = workflow.actions[action_key]
            await emit_update_action_run_event(status="running")

            # Every single 'run_xxx_action' function should return a dict
            # This dict always contains a key 'output' with the direct result of the action
            # The dict may contain additional keys for metadata or other information
            # Dunder keys should are only used for carrying certain execution context information
            # - __should_continue__: A boolean that indicates whether the workflow should continue
            # - output_type: The type of the output
            # We keep them in the result for debugging purposes, for now
            result = await run_action(
                action_trail=action_trail,
                action_run_kwargs=action_run.run_kwargs,
                **action_ref.model_dump(),
            )

            # Mark the action as completed
            action_run_status_store[action_run.id] = ActionRunStatus.SUCCESS

            # Store the result in the action result store.
            # Every action has its own result and the trail of actions that led to it.
            # The schema is {<action ID> : <action result>, ...}
            action_trail = action_trail | {ar_id: result}
            action_result_store[ar_id] = action_trail
            run_status = "success"
            logger.debug("Action run completed", trail=action_trail)

        except TimeoutError as e:
            error_msg = "Action run timed out waiting for dependencies"
            logger.opt(exception=e).error(error_msg, upstream_deps=upstream_deps_ar_ids)
            run_status = "failure"
        except asyncio.CancelledError as e:
            error_msg = "Action run was cancelled."
            logger.opt(exception=e).warning(error_msg)
            run_status = "canceled"
        except Exception as e:
            error_msg = f"Action run failed with error: {e}."
            logger.opt(exception=e).error(error_msg)
            run_status = "failure"
        finally:
            if action_run_status_store[ar_id] != ActionRunStatus.SUCCESS:
                # Exception was raised before the action was marked as successful
                action_run_status_store[ar_id] = ActionRunStatus.FAILURE

            running_jobs_store.pop(ar_id, None)

        await emit_update_action_run_event(
            status=run_status, error_msg=error_msg, result=result
        )

        # Handle downstream dependencies
        if run_status != "success":
            logger.warning("Action run stopping due to failure.")
            return
        if not result.should_continue:
            logger.info("Action run received stop signal.")
            return
        logger.opt(lazy=True).debug(
            "Remaining action runs {ars}", ars=lambda: list(running_jobs_store.keys())
        )
        try:
            downstream_deps_ar_ids = action_run.deps_downstream(workflow=workflow)
            # Broadcast the results to the next actions and enqueue them
            for next_ar_id in downstream_deps_ar_ids:
                if next_ar_id not in action_run_status_store:
                    action_run_status_store[next_ar_id] = ActionRunStatus.QUEUED
                    ready_jobs_queue.put_nowait(
                        ActionRun(
                            workflow_run_id=action_run.workflow_run_id,
                            action_key=parse_action_run_id(next_ar_id, "action_key"),
                        )
                    )
        except Exception as e:
            logger.error(
                "Action run failed to broadcast results to downstream dependencies.",
                exc_info=e,
            )


async def run_webhook_action(
    url: str,
    method: str,
    # Common
    action_run_kwargs: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run a webhook action."""
    action_run_kwargs = action_run_kwargs or {}
    logger.debug(
        "Perform webhook action",
        url=url,
        method=method,
        ar_kwargs=action_run_kwargs,
    )
    # TODO: Perform whitelist/filter step here using the url and method
    return {
        "output": action_run_kwargs,
        "output_type": "dict",
        "url": url,
        "method": method,
    }


def parse_http_response_data(response: httpx.Response) -> dict[str, Any]:
    """Parse an HTTP response."""

    content_type = response.headers.get("Content-Type")
    if content_type.startswith("application/json"):
        return {
            "output": response.json(),
            "content_type": "application/json",
            "output_type": "dict",
        }
    else:
        return {
            "output": response.text,
            "content_type": "text/plain",
            "output_type": "str",
        }


@retry(
    stop=stop_after_attempt(HTTP_MAX_RETRIES),
    wait=wait_exponential(multiplier=1, min=4, max=10),
    reraise=True,
)
async def run_http_request_action(
    url: str,
    method: str,
    headers: dict[str, str],
    payload: dict[str, str | bytes],
    # Common
    action_run_kwargs: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run an HTTP request action."""
    logger.debug(
        "Perform HTTP request action",
        url=url,
        method=method,
        headers=headers,
        payload=payload,
    )

    try:
        async with httpx.AsyncClient() as client:
            response = await client.request(
                method=method,
                url=url,
                headers=headers,
                json=payload,
            )
        response.raise_for_status()
    except httpx.HTTPStatusError as e:
        logger.error("HTTP request failed with status {}.", e.response.status_code)
        raise
    return parse_http_response_data(response)


async def run_conditional_action(
    # NOTE: This arrives as a dictionary becaused we called `model_dump` on the ConditionAction instance.
    condition_rules: dict[str, Any],
    # Common
    action_run_kwargs: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run a conditional action."""
    logger.debug("Perform conditional rules action", rules=condition_rules)
    rule = ConditionRuleValidator.validate_python(condition_rules)
    rule_match = rule.evaluate()
    return {
        "output": "true" if rule_match else "false",  # Explicitly convert to string
        "output_type": "bool",
        "__should_continue__": rule_match,
    }


async def run_llm_action(
    action_trail: ActionTrail,
    # NOTE: This arrives as a dictionary becaused we called `model_dump` on the LLMAction instance.
    task_fields: dict[str, Any],
    message: str,
    system_context: str | None = None,
    model: ModelType = DEFAULT_MODEL_TYPE,
    response_schema: dict[str, Any] | None = None,
    llm_kwargs: dict[str, Any] | None = None,
    # Common
    action_run_kwargs: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run an LLM action."""
    logger.debug(
        "Perform LLM action",
        message=message,
        response_schema=response_schema,
    )

    llm_kwargs = llm_kwargs or {}

    # TODO(perf): Avoid re-creating the task fields object if possible
    validated_task_fields = TaskFields.from_dict(task_fields)

    if response_schema is None:
        system_context = get_system_context(
            validated_task_fields,
            action_trail=action_trail,
        )
        text_response: str = await async_openai_call(
            prompt=message,
            model=model,
            system_context=system_context,
            response_format="text",
            **llm_kwargs,
        )
        return {"output": text_response, "output_type": "str"}
    else:
        system_context = "\n".join(
            (
                get_system_context(validated_task_fields, action_trail=action_trail),
                generate_pydantic_json_response_schema(response_schema),
            )
        )
        logger.debug("System context:\n{}", system_context)
        json_response: dict[str, Any] = await async_openai_call(
            prompt=message,
            model=model,
            system_context=system_context,
            response_format="json_object",
            **llm_kwargs,
        )
        if "JSONDataResponse" in json_response:
            inner_dict: dict[str, str] = json_response["JSONDataResponse"]
            return inner_dict
        return {"output": json_response, "output_type": "dict"}


async def run_send_email_action(
    recipients: list[str],
    subject: str,
    body: str,
    sender: str = "mail@tracecat.com",
    provider: Literal["resend"] = "resend",
    # Common
    action_run_kwargs: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run a send email action."""
    logger.debug(
        "Perform send email action",
        sender=sender,
        recipients=recipients,
        subject=subject,
        body=body,
    )

    if provider == "resend":
        email_provider = ResendMailProvider(
            sender=sender,
            recipients=recipients,
            subject=subject,
            body=body,
        )
    else:
        msg = "Email provider not recognized"
        logger.warning("{}: {!r}", msg, provider)
        email_response = {
            "status": "error",
            "message": msg,
            "provider": provider,
            "sender": sender,
            "recipients": recipients,
            "subject": subject,
            "body": body,
        }
        return {"output": email_response, "output_type": "dict"}

    try:
        await email_provider.send()
    except httpx.HTTPError as exc:
        msg = "Failed to post email to provider"
        logger.opt(exception=exc).error(msg, exc_info=exc)
        email_response = {
            "status": "error",
            "message": msg,
            "provider": provider,
            "sender": sender,
            "recipients": recipients,
            "subject": subject,
            "body": body,
        }
    except (EmailBouncedError, EmailNotFoundError) as e:
        msg = e.args[0]
        logger.opt(exception=e).warning(msg=msg, error=e)
        email_response = {
            "status": "warning",
            "message": msg,
            "provider": provider,
            "sender": sender,
            "recipients": recipients,
            "subject": subject,
            "body": body,
        }
    else:
        email_response = {
            "status": "ok",
            "message": "Successfully sent email",
            "provider": provider,
            "sender": sender,
            "recipients": recipients,
            "subject": subject,
            "body": body,
        }

    return {"output": email_response, "output_type": "dict"}


async def run_open_case_action(
    # Metadata
    action_run_id: str,
    workflow_id: str,
    # Action Inputs
    case_title: str,
    payload: dict[str, Any],
    malice: Literal["malicious", "benign"],
    status: Literal["open", "closed", "in_progress", "reported", "escalated"],
    priority: Literal["low", "medium", "high", "critical"],
    action: Literal[
        "ignore", "quarantine", "informational", "sinkhole", "active_compromise"
    ],
    context: ListModel[CaseContext] | None = None,
    suppression: ListModel[Suppression] | None = None,
    tags: ListModel[Tag] | None = None,
    # Common
    action_run_kwargs: dict[str, Any] | None = None,
) -> dict[str, str | dict[str, str] | None]:
    db = create_vdb_conn()
    tbl = db.open_table("cases")
    role = ctx_session_role.get()
    if role.user_id is None:
        raise ValueError(f"User ID not found in session context: {role}.")
    case = Case(
        id=action_run_id,
        owner_id=role.user_id,
        workflow_id=workflow_id,
        case_title=case_title,
        payload=payload,
        malice=malice,
        status=status,
        priority=priority,
        context=context,
        action=action,
        suppression=suppression,
        tags=tags,
    )
    logger.opt(lazy=True).debug("Sinking case", case=lambda: case.model_dump())
    try:
        await asyncio.to_thread(tbl.add, [case.flatten()])
    except Exception as e:
        logger.error("Failed to add case to LanceDB.", exc_info=e)
        raise
    return {"output": case.model_dump(), "output_type": "dict"}


async def run_integration_action(
    *,
    qualname: str,
    params: dict[str, Any] | None = None,
    # Common
    action_run_kwargs: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run an integration action."""
    logger.debug("Perform integration action", qualname=qualname, params=params)

    params = params or {}

    func = registry[qualname]
    bound_func = partial(func, **params, __role=ctx_session_role.get())

    result = await asyncio.to_thread(bound_func)

    return {
        "output": result,
        "output_type": registry.metadata[qualname]["return_type"],
    }


async def run_action(
    type: ActionType,
    key: str,
    title: str,
    action_trail: dict[str, ActionRunResult],
    action_run_kwargs: dict[str, Any] | None = None,
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

    logger.info(
        "Running action",
        key=key,
        title=title,
        type=type,
        action_run_kwargs=action_run_kwargs,
        action_kwargs=action_kwargs,
    )

    action_runner = _ACTION_RUNNER_FACTORY[type]

    action_trail_json = {
        result.action_slug: result.output for result in action_trail.values()
    }
    action_kwargs_with_secrets = await evaluate_templated_secrets(
        templated_fields=action_kwargs
    )
    processed_action_kwargs = evaluate_templated_fields(
        templated_fields=action_kwargs_with_secrets, source_data=action_trail_json
    )

    # Only pass the action trail to the LLM action
    if type == "llm":
        processed_action_kwargs.update(action_trail=action_trail)

    elif type == "open_case":
        ar_id = ctx_action_run.get().id
        workflow = ctx_workflow_run.get().workflow
        processed_action_kwargs.update(action_run_id=ar_id, workflow_id=workflow.id)

    logger.debug(
        "Finish processing action kwargs",
        processed_action_kwargs=processed_action_kwargs,
    )

    try:
        # The return value from each action runner call should be more or less what
        # the user can expect to see in the action trail. This makes it very clear
        # what the action is doing and what the output is.
        output = await action_runner(
            action_run_kwargs=action_run_kwargs,
            **processed_action_kwargs,
        )
    except Exception as e:
        logger.opt(exception=e).error("Error running action", key=key)
        raise

    # Leave dunder keys inside as a form of execution context
    should_continue = output.get("__should_continue__", True)
    return ActionRunResult(
        action_key=key, output=output, should_continue=should_continue
    )


_ActionRunner = Callable[..., Awaitable[dict[str, Any]]]

_ACTION_RUNNER_FACTORY: dict[ActionType, _ActionRunner] = {
    "webhook": run_webhook_action,
    "http_request": run_http_request_action,
    "condition": run_conditional_action,
    "llm": run_llm_action,
    "send_email": run_send_email_action,
    "open_case": run_open_case_action,
    "integration": run_integration_action,
}
