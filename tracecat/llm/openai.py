"""OpenAI LLM client.

Docs: https://platform.openai.com/docs/guides/text-generation
"""

from typing import Any, overload

from openai import AsyncOpenAI
from openai.types.chat.chat_completion import ChatCompletion
from openai.types.chat.chat_completion_message_param import ChatCompletionMessageParam
from openai.types.chat.parsed_chat_completion import ParsedChatCompletion
from openai.types.responses import Response, ResponseInputParam

from tracecat.logger import logger

# Must support structured outputs
# https://platform.openai.com/docs/guides/structured-outputs#supported-models
DEFAULT_OPENAI_MODEL = "gpt-4o-mini"


async def async_openai_call(
    prompt: str,
    *,
    model: str = DEFAULT_OPENAI_MODEL,
    memory: list[ResponseInputParam] | None = None,
    instructions: str | None = None,
    text_format: dict[str, Any] | None = None,
    api_key: str,
    base_url: str | None = None,
) -> Response:
    """Call the OpenAI API with the given prompt and return the response."""
    client = AsyncOpenAI(api_key=api_key, base_url=base_url)
    messages = []
    if memory:
        messages.extend(memory)

    messages.append({"role": "user", "content": prompt})
    kwargs = {"model": model, "input": messages}
    if instructions:
        kwargs["instructions"] = instructions
    if text_format:
        kwargs["text"] = {"format": text_format}

    logger.debug(
        "🧠 Calling LLM chat completion",
        provider="openai-responses",
        model=model,
        prompt=prompt,
    )

    response = await client.responses.create(**kwargs)
    return response


# Call with structured output
@overload
async def async_openai_chat_completion(
    prompt: str,
    *,
    model: str = DEFAULT_OPENAI_MODEL,
    memory: list[ChatCompletionMessageParam] | None = None,
    system_prompt: str | None = None,
    response_format: dict[str, Any],
    api_key: str,
    base_url: str | None = None,
) -> ParsedChatCompletion: ...


# Call without structured output
@overload
async def async_openai_chat_completion(
    prompt: str,
    *,
    model: str = DEFAULT_OPENAI_MODEL,
    memory: list[ChatCompletionMessageParam] | None = None,
    system_prompt: str | None = None,
    response_format: None = None,
    api_key: str,
    base_url: str | None = None,
) -> ChatCompletion: ...


async def async_openai_chat_completion(
    prompt: str,
    *,
    model: str = DEFAULT_OPENAI_MODEL,
    memory: list[ChatCompletionMessageParam] | None = None,
    system_prompt: str | None = None,
    response_format: dict[str, Any] | None = None,
    api_key: str,
    base_url: str | None = None,
) -> ChatCompletion:
    """Call the OpenAI API with the given prompt and return the response."""
    client = AsyncOpenAI(api_key=api_key, base_url=base_url)
    messages: list[ChatCompletionMessageParam] = []

    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})

    if memory:
        messages.extend(memory)

    messages.append({"role": "user", "content": prompt})

    kwargs = {"model": model, "messages": messages}
    if response_format:
        kwargs["response_format"] = response_format

    logger.debug(
        "🧠 Calling LLM chat completion",
        provider="openai-completions",
        model=model,
        prompt=prompt,
    )

    response = await client.chat.completions.create(**kwargs)
    return response
