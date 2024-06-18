"""Core LLM functionality."""

from __future__ import annotations

from typing import Any, Literal

import orjson
from loguru import logger
from openai import AsyncOpenAI
from openai.types.chat.chat_completion import ChatCompletion, Choice
from tenacity import retry, stop_after_attempt, wait_exponential

from tracecat.config import LLM_MAX_RETRIES

ModelType = Literal[
    "gpt-4o",
    "gpt-4-turbo",
    "gpt-4-turbo-preview",
    "gpt-4-0125-preview",
    "gpt-4-vision-preview",
    "gpt-3.5-turbo-0125",
]
DEFAULT_MODEL_TYPE: ModelType = "gpt-4o"
DEFAULT_SYSTEM_CONTEXT = "You are a helpful assistant."


@retry(
    stop=stop_after_attempt(LLM_MAX_RETRIES),
    wait=wait_exponential(multiplier=1, min=4, max=10),
    reraise=True,
)
async def retryable_async_openai_call(  # type: ignore
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

    logger.info("🧠 Calling OpenAI API with {} model...", model)
    response: ChatCompletion = await client.chat.completions.create(  # type: ignore[call-overload]
        model=model,
        response_format={"type": response_format},
        messages=messages,
        temperature=temperature,
        stream=stream,
        **kwargs,
    )
    # TODO: Should track these metrics
    logger.info("🧠 Usage", usage=response.usage)
    if stream:
        return response

    return parse_choice(response.choices[0])
