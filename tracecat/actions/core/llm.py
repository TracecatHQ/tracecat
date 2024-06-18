"""Core LLM actions."""
# XXX(WARNING): Do not import __future__ annotations from typing
# This will cause class types to be resolved as strings

from typing import Annotated, Any, TypedDict

from pydantic import Field

from tracecat.llm import DEFAULT_MODEL_TYPE, ModelType, retryable_async_openai_call
from tracecat.registry import registry


def _event_context_instructions(event_context: dict[str, Any]) -> str:
    return (
        "Additional Instructions:"
        "\nYou have also been provided with the following JSON object of the previous task execution results,"
        " delimited by triple backticks (```)."
        " The object keys are the action ids and the values are the results of the actions."
        "\n```"
        f"\n{event_context}"
        "\n```"
        "You may use the past action run results to help you complete your task."
        " If you think it isn't helpful, you may ignore it."
    )


def _generate_pydantic_json_response_schema(response_schema: dict[str, Any]) -> str:
    return (
        "\nCreate a `JSONDataResponse` according to the following pydantic model:"
        "\n```"
        "\nclass JSONDataResponse(BaseModel):"
        f"\n{"\n".join(f"\t{k}: {v}" for k, v in response_schema.items())}"
        "\n```"
    )


class LLMResponse(TypedDict):
    data: str | dict[str, Any]


@registry.register(
    namespace="core.ai",
    version="0.1.0",
    description="Perform summarization using a LLM",
    default_title="AI Summarize",
    display_group="AI Actions",
)
async def summarize(
    text: Annotated[str, Field(description="Text passed to the AI")],
    response_schema: Annotated[
        dict[str, str], Field(description="Shape of the response")
    ] = None,
) -> LLMResponse:
    system_context = (
        "You will be provided with a body of text (e.g. paragraph, articles),"
        " and your task is to summarize it."
    )
    return await _call_llm(
        text=text,
        system_context=system_context,
        response_schema=response_schema,
    )


@registry.register(
    namespace="core.ai",
    version="0.1.0",
    description="Perform translation using a LLM",
    default_title="AI Translate",
    display_group="AI Actions",
)
async def translate(
    text: Annotated[str, Field(description="Text passed to the AI")],
    from_language: Annotated[
        str,
        Field(
            description="The source language for the translation. If None, the language(s) will be detected automatically.",
        ),
    ] = None,
    to_language: Annotated[
        str, Field(description="The target language for translation")
    ] = "english",
    response_schema: Annotated[
        dict[str, str], Field(description="Shape of the response")
    ] = None,
    execution_context: Annotated[
        dict[str, Any],
        Field(description="The context of the execution"),
    ] = None,
) -> LLMResponse:
    # The corresponding `message` body for this should be a templated string.
    from_language = from_language or "Non-English"
    system_context = (
        "You are an expert translator. You will be provided with a body of text that may"
        f" contain {from_language.capitalize()} text, and your task is to translate it"
        f" into {to_language.capitalize()}."
        "\nFor example, given the following text:"
        "\nBonjour, comment ça va? My name is John. 你好吗?"
        "\nYou should respond with:"
        "\nHello, how are you? My name is John. How are you?"
    )
    return await _call_llm(
        text=text,
        system_context=system_context,
        response_schema=response_schema,
        event_context=execution_context,
    )


@registry.register(
    namespace="core.ai",
    version="0.1.0",
    description="Perform extraction using a LLM",
    default_title="AI Extract",
    display_group="AI Actions",
)
async def extract(
    text: str,
    groups: list[str],
    response_schema: Annotated[
        dict[str, str], Field(description="Shape of the response")
    ] = None,
) -> LLMResponse:
    if groups:
        task = f"extract one or more lists of keywords from it, grouped by the following categories: {groups}"
    else:
        task = "extract a list of keywords from it"
    system_context = (
        "You will be provided with a body of text (e.g. paragraph, articles),"
        f" and your task is to {task}."
    )
    return await _call_llm(
        text=text,
        system_context=system_context,
        response_schema=response_schema,
        llm_kwargs=None,
        event_context=None,
    )


@registry.register(
    namespace="core.ai",
    version="0.1.0",
    description="Perform labeling using a LLM",
    default_title="AI Label",
    display_group="AI Actions",
)
async def label(
    text: str, labels: list[str], response_schema: dict[str, str]
) -> LLMResponse:
    system_context = (
        "You will be provided with a body of text (e.g. paragraph, articles),"
        f" and your task is to classify it into one of the following categories: {labels}."
    )
    return await _call_llm(
        text=text,
        system_context=system_context,
        response_schema=response_schema,
        llm_kwargs=None,
        event_context=None,
    )


async def _call_llm(
    text: str,
    system_context: str,
    model: ModelType = DEFAULT_MODEL_TYPE,
    response_schema: dict[str, Any] | None = None,
    llm_kwargs: dict[str, Any] | None = None,
    event_context: dict[str, Any] | None = None,
) -> LLMResponse:
    llm_kwargs = llm_kwargs or {}
    event_context = event_context or {}
    formatted_event_context = _event_context_instructions(event_context)
    final_system_context = "\n".join((system_context, formatted_event_context))

    if response_schema is None:
        text_response: str = await retryable_async_openai_call(
            prompt=text,
            model=model,
            system_context=final_system_context,
            response_format="text",
            **llm_kwargs,
        )
        return LLMResponse(data=text_response)
    else:
        json_response: dict[str, Any] = await retryable_async_openai_call(
            prompt=text,
            model=model,
            system_context="\n".join(
                (
                    final_system_context,
                    _generate_pydantic_json_response_schema(response_schema),
                )
            ),
            response_format="json_object",
            **llm_kwargs,
        )
        if "JSONDataResponse" in json_response:
            inner_dict: dict[str, str] = json_response["JSONDataResponse"]
            return LLMResponse(data=inner_dict)
        return LLMResponse(data=json_response)
