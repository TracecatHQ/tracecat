"""Anthropic LLM client.

Docs: https://docs.anthropic.com/en/api/client-sdks
"""

from typing import Any

from anthropic import AsyncAnthropic, AsyncAnthropicBedrock
from anthropic.types import (
    Message,
    MessageParam,
    ThinkingConfigParam,
    ToolChoiceParam,
    ToolParam,
)

from tracecat.logger import logger


def _get_anthropic_client(
    api_key: str | None = None,
    aws_credentials: dict[str, Any] | None = None,
    *,
    base_url: str | None = None,
) -> AsyncAnthropic | AsyncAnthropicBedrock:
    if api_key:
        client = AsyncAnthropic(api_key=api_key, base_url=base_url)
    elif aws_credentials:
        client = AsyncAnthropicBedrock(
            **aws_credentials,
            base_url=base_url,
        )
    else:
        raise ValueError("No Anthropic API key or AWS credentials provided")
    return client


async def async_anthropic_call(
    prompt: str,
    *,
    model: str,
    memory: list[MessageParam] | None = None,
    system: str | None = None,
    thinking: ThinkingConfigParam | None = None,
    tool_choice: ToolChoiceParam | None = None,
    tools: list[ToolParam] | None = None,
    api_key: str | None = None,
    aws_credentials: dict[str, Any] | None = None,
    base_url: str | None = None,
) -> Message:
    """Call the Anthropic Bedrock API with the given prompt and return the response."""

    async with _get_anthropic_client(
        api_key=api_key,
        aws_credentials=aws_credentials,
        base_url=base_url,
    ) as client:
        messages = []
        if memory:
            messages.extend(memory)

        messages.append({"role": "user", "content": prompt})
        kwargs = {"model": model, "messages": messages}

        if system:
            kwargs["system"] = system

        if thinking:
            kwargs["thinking"] = thinking

        if tool_choice:
            kwargs["tool_choice"] = tool_choice

        if tools:
            kwargs["tools"] = tools

        logger.debug(
            "🧠 Calling LLM chat completion",
            provider="anthropic",
            model=model,
            prompt=prompt,
            system=system,
        )

        response = await client.messages.create(**kwargs)
        return response
