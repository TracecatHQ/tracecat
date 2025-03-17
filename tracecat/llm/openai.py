"""OpenAI LLM client.

Docs: https://platform.openai.com/docs/guides/text-generation
"""

from enum import StrEnum
from typing import Any

from openai import AsyncOpenAI
from openai.types.responses import Response, ResponseInputParam

from tracecat.logger import logger


# Support the same models as Cursor:
# https://docs.cursor.com/settings/models
class OpenAIModel(StrEnum):
    CHATGPT_4O = "chatgpt-4o-latest"
    """Default ChatGPT model. Does not support structured output."""
    GPT4O = "gpt-4o"
    """https://platform.openai.com/docs/models/gpt-4o. Supports structured output."""
    GPT4O_MINI = "gpt-4o-mini"
    """https://platform.openai.com/docs/models/gpt-4o-mini. Supports structured output."""
    O1 = "o1"
    """https://platform.openai.com/docs/models/o1. Does not support structured output."""
    O1_MINI = "o1-mini"
    """https://platform.openai.com/docs/models/o1-mini. Does not support structured output."""
    O3_MINI = "o3-mini"
    """https://platform.openai.com/docs/models/o3-mini. Does not support structured output."""


# Must support structured outputs
# https://platform.openai.com/docs/guides/structured-outputs#supported-models
DEFAULT_OPENAI_MODEL = OpenAIModel.GPT4O_MINI


async def async_openai_call(
    prompt: str | list[dict[str, Any]],
    *,
    model: OpenAIModel = DEFAULT_OPENAI_MODEL,
    memory: list[ResponseInputParam] | None = None,
    instructions: str | None = None,
    text_format: dict[str, Any] | None = None,
    api_key: str,
) -> Response:
    """Call the OpenAI API with the given prompt and return the response."""
    client = AsyncOpenAI(api_key=api_key)
    logger.debug(
        "🧠 Calling LLM chat completion", provider="openai", model=model, prompt=prompt
    )

    messages = []
    if memory:
        messages.extend(memory)

    if isinstance(prompt, list):
        messages.extend(prompt)
    else:
        messages.append({"role": "user", "content": prompt})

    kwargs = {"model": model, "input": messages}
    if instructions:
        kwargs["instructions"] = instructions
    if text_format:
        kwargs["text"] = {"format": text_format}

    response = await client.responses.create(**kwargs)
    return response
