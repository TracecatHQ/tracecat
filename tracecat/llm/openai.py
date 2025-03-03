"""Core LLM functionality."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from openai import AsyncOpenAI, AsyncStream
from openai.types.chat.chat_completion import ChatCompletion
from openai.types.chat.chat_completion_chunk import ChatCompletionChunk

from tracecat.logger import logger


class OpenAIModel(StrEnum):
    GPT4O = "gpt-4o"
    GPT4_TURBO = "gpt-4-turbo"
    GPT4_TURBO_PREVIEW = "gpt-4-turbo-preview"
    GPT4_0125_PREVIEW = "gpt-4-0125-preview"
    GPT4_VISION_PREVIEW = "gpt-4-vision-preview"
    GPT35_TURBO_0125 = "gpt-3.5-turbo-0125"


async def async_openai_call(
    *,
    prompt: str,
    model: OpenAIModel,
    api_key: str,
    temperature: float = 0.2,
    response_format: Literal["json_object", "text"] = "text",
    stream: bool = False,
    **kwargs,
) -> AsyncStream[ChatCompletionChunk] | ChatCompletion:
    """Call the OpenAI API with the given prompt and return the response."""
    client = AsyncOpenAI(api_key=api_key)
    logger.info("🧠 Calling LLM chat", provider="openai", model=model, prompt=prompt)
    response = await client.chat.completions.create(
        model=model,
        response_format={"type": response_format},
        messages=[{"role": "user", "content": prompt}],
        temperature=temperature,
        stream=stream,
        **kwargs,
    )
    return response
