from __future__ import annotations

from collections.abc import Callable
from typing import Any, Literal

import orjson
from openai import AsyncOpenAI
from openai.types.chat.chat_completion import ChatCompletion, Choice
from pydantic import BaseModel, Field
from tenacity import retry, stop_after_attempt, wait_exponential

from tracecat.config import MAX_RETRIES
from tracecat.logger import standard_logger

logger = standard_logger(__name__)

TaskType = Literal[
    "translate",
    "extract",
    "summarize",
    "label",
    "enrich",
    "question_answering",
    "choice",
]
ModelType = Literal[
    "gpt-4-turbo-preview",
    "gpt-4-0125-preview",
    "gpt-4-vision-preview",
    "gpt-3.5-turbo-0125",
]
DEFAULT_MODEL_TYPE: ModelType = "gpt-4-turbo-preview"
DEFAULT_SYSTEM_CONTEXT = "You are a helpful assistant."


class TaskFields(BaseModel):
    type: TaskType

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TaskFields:
        task_type = data.pop("type")
        task_field_cls = TASK_FIELDS_FACTORY[task_type]
        return task_field_cls(**data)


class TranslateTaskFields(TaskFields):
    type: Literal["translate"] = Field("translate", frozen=True)
    from_language: str | None = Field(
        None,
        description="The source language for the translation. If None, the language(s) will be detected automatically.",
    )
    to_language: str = "english"


class ExtractTaskFields(TaskFields):
    type: Literal["extract"] = Field("extract", frozen=True)
    groups: list[str] | None = None


class LabelTaskFields(TaskFields):
    type: Literal["label"] = Field("label", frozen=True)
    labels: list[str]


class SummarizeTaskFields(TaskFields):
    type: Literal["summarize"] = Field("summarize", frozen=True)


class ChoiceTaskFields(TaskFields):
    type: Literal["choice"] = Field("choice", frozen=True)
    choices: list[str]


class EnrichTaskFields(TaskFields):
    type: Literal["enrich"] = Field("enrich", frozen=True)


TaskFieldsSubclass = (
    TranslateTaskFields
    | ExtractTaskFields
    | LabelTaskFields
    | SummarizeTaskFields
    | ChoiceTaskFields
    | EnrichTaskFields
)
TASK_FIELDS_FACTORY: dict[TaskType, type[TaskFields]] = {
    "translate": TranslateTaskFields,
    "extract": ExtractTaskFields,
    "label": LabelTaskFields,
    "summarize": SummarizeTaskFields,
    "choice": ChoiceTaskFields,
    "enrich": EnrichTaskFields,
}


def action_trail_instructions(action_trail: dict[str, Any]) -> str:
    return (
        "Additional Instructions:"
        "\nYou have also been provided with the following JSON object of the previous task execution results,"
        " delimited by triple backticks (```)."
        " The object keys are the action ids and the values are the results of the actions."
        "\n```"
        f"\n{action_trail}"
        "\n```"
        "You may use the past action run results to help you complete your task."
        " If you think it isn't helpful, you may ignore it."
    )


def _translate_system_context(from_language: str | None, to_language: str) -> str:
    # The corresponding `message` body for this should be a templated string.
    from_language = from_language or "Non-English"
    context = (
        "You are an expert translator. You will be provided with a body of text that may"
        f" contain {from_language.capitalize()} text, and your task is to translate it"
        f" into {to_language.capitalize()}."
        "\nFor example, given the following text:"
        "\nBonjour, comment ça va? My name is John. 你好吗?"
        "\nYou should respond with:"
        "\nHello, how are you? My name is John. How are you?"
    )
    return context


def _label_system_context(labels: list[str]) -> str:
    context = (
        "You will be provided with a body of text (e.g. paragraph, articles),"
        f" and your task is to classify it into one of the following categories: {labels}."
    )
    return context


def _extract_system_context(groups: list[str] | None) -> str:
    if groups:
        task = f"extract one or more lists of keywords from it, grouped by the following categories: {groups}"
    else:
        task = "extract a list of keywords from it"
    context = (
        "You will be provided with a body of text (e.g. paragraph, articles),"
        f" and your task is to {task}."
    )
    return context


def _summary_system_context() -> str:
    return (
        "You will be provided with a body of text (e.g. paragraph, articles),"
        " and your task is to summarize it."
    )


def _question_answering_system_context() -> str:
    return (
        "You are an expert at answering questions. Answer the following question in a concise and direct manner."
        " If you don't know the answer, don't make up an answer but say that you don't know the answer."
    )


def _choice_system_context(choices: list[str]) -> str:
    """Return the system context for a choice task.

    Example use cases
    -----------------
    1. Given a body of text containing some instructions, choose the best option from the following choices.
    2. Given an excerpt, choose the best option from the following choices.
    """
    return (
        "\nYou will be provided with a body of text from which you will use to make a decision."
        " Given a multiple choice question, choose the best option to answer the question."
        " Given a body of text containing some instructions, choose the best option to achieve the objective."
        f" Choose the best option from the following choices: {choices}"
    )


def _enrich_system_context() -> str:
    return (
        "You are an expert at enriching data. You will be provided with a body of text (e.g. paragraph, articles),"
        " and your task is to enrich it."
    )


_LLM_SYSTEM_CONTEXT_FACTORY: dict[TaskType, Callable[..., str]] = {
    "translate": _translate_system_context,
    "extract": _extract_system_context,
    "label": _label_system_context,
    "summarize": _summary_system_context,
    "question_answering": _question_answering_system_context,
    "choice": _choice_system_context,
    "enrich": _enrich_system_context,
}


def get_system_context(task_fields: TaskFields, action_trail: dict[str, Any]) -> str:
    context = _LLM_SYSTEM_CONTEXT_FACTORY[task_fields.type](
        **task_fields.model_dump(exclude={"type"})
    )
    formatted_add_instrs = action_trail_instructions(action_trail)
    return "\n".join((context, formatted_add_instrs))


def generate_pydantic_json_response_schema(response_schema: dict[str, Any]) -> str:
    return (
        "\nCreate a `JSONDataResponse` according to the following pydantic model:"
        "\n```"
        "\nclass JSONDataResponse(BaseModel):"
        f"\n{"\n".join(f"\t{k}: {v}" for k, v in response_schema.items())}"
        "\n```"
    )


@retry(
    stop=stop_after_attempt(MAX_RETRIES),
    wait=wait_exponential(multiplier=1, min=4, max=10),
)
async def async_openai_call(  # type: ignore
    prompt: str,
    model: ModelType = DEFAULT_MODEL_TYPE,
    temperature: float = 0.2,
    system_context: str = DEFAULT_SYSTEM_CONTEXT,
    response_format: Literal["json_object", "text"] = "text",
    stream: bool = False,
    parse_json: bool = True,
    **kwargs,
):
    """Call the OpenAI API with the given prompt and return the response.

    Returns
    -------
    dict[str, Any]
        The message object from the OpenAI ChatCompletion API.
    """
    client = AsyncOpenAI()

    def parse_choice(choice: Choice) -> str | dict[str, Any]:
        # The content will not be null, so we can safely use the `!` operator.
        content = choice.message.content
        if not content:
            logger.warning("No content in response.")
            return ""
        res = content.strip()
        if parse_json and response_format == "json_object":
            json_res: dict[str, Any] = orjson.loads(res)
            return json_res
        return res

    if response_format == "json_object":
        system_context += " Please only output valid JSON."

    messages = [
        {"role": "system", "content": system_context},
        {"role": "user", "content": prompt},
    ]

    logger.info("🧠 Calling OpenAI API with model: %s...", model)
    response: ChatCompletion = await client.chat.completions.create(  # type: ignore[call-overload]
        model=model,
        response_format={"type": response_format},
        messages=messages,
        temperature=temperature,
        stream=stream,
        **kwargs,
    )
    if stream:
        return response

    return parse_choice(response.choices[0])
