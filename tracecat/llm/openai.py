"""OpenAI LLM client.

Docs: https://platform.openai.com/docs/guides/text-generation
"""

from enum import StrEnum
from typing import overload

from openai import AsyncOpenAI
from openai.types.chat.chat_completion import ChatCompletion
from openai.types.chat.chat_completion_message_param import ChatCompletionMessageParam
from openai.types.chat.parsed_chat_completion import ParsedChatCompletion
from pydantic import BaseModel

from tracecat.logger import logger


# Support the same models as Cursor:
# https://docs.cursor.com/settings/models
class OpenAIModel(StrEnum):
    # Default ChatGPT model. Does not support structured output.
    CHATGPT_4O = "chatgpt-4o-latest"
    # https://platform.openai.com/docs/models/gpt-4o
    GPT4O = "gpt-4o"
    # https://platform.openai.com/docs/models/gpt-4o-mini
    GPT4O_MINI = "gpt-4o-mini"
    # https://platform.openai.com/docs/models/o1
    O1 = "o1"
    # https://platform.openai.com/docs/models/o1-mini
    O1_MINI = "o1-mini"
    # https://platform.openai.com/docs/models/o3-mini
    O3_MINI = "o3-mini"


# Must support structured outputs
# https://platform.openai.com/docs/guides/structured-outputs#supported-models
DEFAULT_OPENAI_MODEL = OpenAIModel.GPT4O_MINI


# Call with structured output
@overload
async def async_openai_call(
    prompt: str,
    *,
    model: OpenAIModel = DEFAULT_OPENAI_MODEL,
    memory: list[ChatCompletionMessageParam] | None = None,
    system_prompt: str | None = None,
    response_format: BaseModel,
    api_key: str,
) -> ParsedChatCompletion: ...


# Call without structured output
@overload
async def async_openai_call(
    prompt: str,
    *,
    model: OpenAIModel = DEFAULT_OPENAI_MODEL,
    memory: list[ChatCompletionMessageParam] | None = None,
    system_prompt: str | None = None,
    response_format: None = None,
    api_key: str,
) -> ChatCompletion: ...


async def async_openai_call(
    prompt: str,
    *,
    model: OpenAIModel = DEFAULT_OPENAI_MODEL,
    memory: list[ChatCompletionMessageParam] | None = None,
    system_prompt: str | None = None,
    response_format: BaseModel | None = None,
    api_key: str,
) -> ChatCompletion | ParsedChatCompletion:
    """Call the OpenAI API with the given prompt and return the response."""
    client = AsyncOpenAI(api_key=api_key)
    logger.debug(
        "🧠 Calling LLM chat completion", provider="openai", model=model, prompt=prompt
    )
    if system_prompt:
        messages: list[ChatCompletionMessageParam] = [
            {"role": "developer", "content": system_prompt},
            {"role": "user", "content": prompt},
        ]
    elif memory:
        messages: list[ChatCompletionMessageParam] = [
            *memory,
            {"role": "user", "content": prompt},
        ]
    else:
        messages: list[ChatCompletionMessageParam] = [
            {"role": "user", "content": prompt}
        ]

    kwargs = {
        "model": model,
        "messages": messages,
    }
    if response_format is not None:
        kwargs["response_format"] = response_format
        response = await client.beta.chat.completions.parse(**kwargs)
    else:
        response = await client.chat.completions.create(**kwargs)
    return response
