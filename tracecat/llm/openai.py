"""OpenAI LLM client.

Docs: https://platform.openai.com/docs/guides/text-generation
"""

from enum import StrEnum

from openai import AsyncOpenAI, AsyncStream
from openai.types.chat.chat_completion import ChatCompletion
from openai.types.chat.chat_completion_chunk import ChatCompletionChunk
from openai.types.chat.chat_completion_message_param import ChatCompletionMessageParam

from tracecat.logger import logger


class OpenAIModel(StrEnum):
    # Support the same models as Cursor:
    # https://docs.cursor.com/settings/models
    CHATGPT_4O = "chatgpt-4o-latest"
    GPT4O = "gpt-4o"
    GPT4O_MINI = "gpt-4o-mini"
    O1 = "o1"
    O1_MINI = "o1-mini"
    O3_MINI = "o3-mini"


DEFAULT_MODEL = OpenAIModel.CHATGPT_4O


async def async_openai_call(
    *,
    prompt: str,
    system_prompt: str | None = None,
    memory: list[ChatCompletionMessageParam] | None = None,
    model: OpenAIModel = DEFAULT_MODEL,
    stream: bool = False,
    api_key: str,
) -> AsyncStream[ChatCompletionChunk] | ChatCompletion:
    """Call the OpenAI API with the given prompt and return the response."""
    client = AsyncOpenAI(api_key=api_key)
    logger.info("🧠 Calling LLM chat", provider="openai", model=model, prompt=prompt)
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

    response = await client.chat.completions.create(
        model=model,
        messages=messages,
        stream=stream,
    )
    return response
