"""Core LLM functionality."""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from typing import Any, Literal

import ollama
import orjson
from openai import AsyncOpenAI, AsyncStream
from openai.types.chat.chat_completion import Choice
from openai.types.chat.chat_completion_chunk import ChatCompletionChunk
from openai.types.chat.chat_completion_message_param import ChatCompletionMessageParam

from tracecat import config
from tracecat.concurrency import GatheringTaskGroup
from tracecat.logger import logger
from tracecat.secrets import secrets_manager


class OllamaModel(StrEnum):
    LLAMA32 = "llama3.2"
    LLAMA32_1B = "llama3.2:1b"


class OpenAIModel(StrEnum):
    GPT4O = "gpt-4o"
    GPT4_TURBO = "gpt-4-turbo"
    GPT4_TURBO_PREVIEW = "gpt-4-turbo-preview"
    GPT4_0125_PREVIEW = "gpt-4-0125-preview"
    GPT4_VISION_PREVIEW = "gpt-4-vision-preview"
    GPT35_TURBO_0125 = "gpt-3.5-turbo-0125"


ModelType = OllamaModel | OpenAIModel
DEFAULT_MODEL_TYPE: ModelType = OpenAIModel.GPT4O
DEFAULT_SYSTEM_CONTEXT = "You are a helpful assistant."

# Create sets for easy membership testing
OLLAMA_MODELS = {model.value for model in OllamaModel}
OPENAI_MODELS = {model.value for model in OpenAIModel}


async def async_openai_call(
    *,
    prompt: str,
    model: OpenAIModel,
    temperature: float = 0.2,
    system_context: str = DEFAULT_SYSTEM_CONTEXT,
    response_format: Literal["json_object", "text"] = "text",
    stream: bool = False,
    parse_json: bool = True,
    **kwargs,
) -> AsyncStream[ChatCompletionChunk] | str | dict[str, Any]:
    """Call the OpenAI API with the given prompt and return the response.

    Returns
    -------
    dict[str, Any]
        The message object from the OpenAI ChatCompletion API.
    """
    if not (api_key := secrets_manager.get("OPENAI_API_KEY")):
        raise ValueError("`OPENAI_API_KEY` not found in the secret manager.")
    client = AsyncOpenAI(api_key=api_key)

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

    messages: list[ChatCompletionMessageParam] = [
        {"role": "system", "content": system_context},
        {"role": "user", "content": prompt},
    ]

    logger.info(f"🧠 Calling OpenAI API with {model} model...")
    if stream:
        response = await client.chat.completions.create(
            model=model,
            response_format={"type": response_format},
            messages=messages,
            temperature=temperature,
            stream=True,
            **kwargs,
        )
        return response
    else:
        response = await client.chat.completions.create(
            model=model,
            response_format={"type": response_format},
            messages=messages,
            temperature=temperature,
            stream=False,
            **kwargs,
        )
        logger.info("🧠 Usage", usage=response.usage)
        return parse_choice(response.choices[0])


def _get_ollama_client() -> ollama.AsyncClient:
    return ollama.AsyncClient(host=config.OLLAMA__API_URL)


async def async_ollama_call(*, prompt: str, model: OllamaModel) -> Mapping[str, Any]:
    client = _get_ollama_client()
    try:
        response = await client.chat(
            model=model, messages=[{"role": "user", "content": prompt}]
        )
    except ollama.ResponseError as e:
        logger.error("Error when calling ollama", error=e.error)
        if e.status_code == 404:
            logger.info("Pulling model from ollama", model=model)
            raise ValueError(f"Model {model} not found") from e
        raise
    return response


async def route_llm_call(
    *,
    prompt: str,
    model: ModelType,
    system_context: str = DEFAULT_SYSTEM_CONTEXT,
    additional_config: dict[str, Any] | None = None,
    **kwargs,
):
    kwargs.update(additional_config or {})

    if model in OllamaModel:
        return await async_ollama_call(
            prompt=prompt,
            model=OllamaModel(model),
            **kwargs,
        )
    elif model in OpenAIModel:
        return await async_openai_call(
            prompt=prompt,
            model=OpenAIModel(model),
            system_context=system_context,
            **kwargs,
        )
    else:
        raise ValueError(f"Unsupported model: {model}")


async def preload_ollama_models(models: list[str]) -> list[Mapping[str, Any]]:
    client = _get_ollama_client()
    async with GatheringTaskGroup() as tg:
        for model in models:
            tg.create_task(client.pull(model))
    return tg.results()
