"""Anthropic Bedrock LLM client.

Docs: https://docs.anthropic.com/claude/reference/bedrock-python-sdk
"""


from anthropic import AsyncAnthropicBedrock
from anthropic.types import Message, MessageParam

from tracecat.logger import logger

DEFAULT_ANTHROPIC_MODEL = "us.anthropic.claude-3-7-sonnet-20250219-v1:0"


async def async_anthropic_bedrock_call(
    prompt: str,
    completion: str | None = None,
    *,
    model: str = DEFAULT_ANTHROPIC_MODEL,
    memory: list[MessageParam] | None = None,
    system_prompt: str | None = None,
    max_tokens: int
) -> Message:
    """Call the Anthropic Bedrock API with the given prompt and return the response."""
    client = AsyncAnthropicBedrock()
    logger.debug(
        "🧠 Calling LLM chat completion",
        provider="anthropic_bedrock",
        model=model,
        prompt=prompt,
    )

    messages = []
    if memory:
        messages.extend(memory)

    messages.append({"role": "user", "content": prompt})
    if completion:
        messages.append({"role": "assistant", "content": completion})
    kwargs = {"model": model, "messages": messages, "max_tokens": max_tokens}

    if system_prompt:
        kwargs["system"] = system_prompt

    response = await client.messages.create(**kwargs)
    return response
