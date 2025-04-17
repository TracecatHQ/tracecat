from typing import Annotated, Any

from typing_extensions import Doc

from tracecat.llm.anthropic_bedrock import (
    async_anthropic_bedrock_call,
    DEFAULT_ANTHROPIC_MODEL,
)
from tracecat_registry import registry


@registry.register(
    default_title="Call Anthropic Bedrock (messages)",
    description="Call an LLM via Anthropic Bedrock messages API.",
    display_group="Anthropic",
    doc_url="https://docs.anthropic.com/claude/reference/bedrock-python-sdk",
    namespace="llm.anthropic_bedrock",
)
async def call(
    prompt: Annotated[
        str,
        Doc("Prompt or conversation history to send to the LLM"),
    ],
    completion: Annotated[
        str | None,
        Doc("Optionally prefix the LLM's response"),
    ] = None,
    model: Annotated[
        str,
        Doc("Model to use"),
    ] = DEFAULT_ANTHROPIC_MODEL,
    memory: Annotated[
        list[dict[str, Any]] | None,
        Doc("Past messages to include in the conversation."),
    ] = None,
    system_prompt: Annotated[
        str | None, Doc("Insert a system message at the beginning of the conversation.")
    ] = None,
    max_tokens: Annotated[
        int,
        Doc("Maximum number of tokens to generate. 1024 by default."),
    ] = 1024,
) -> dict[str, Any]:
    response = await async_anthropic_bedrock_call(
        prompt=prompt,
        completion=completion,
        model=model,
        memory=memory,
        system_prompt=system_prompt,
        max_tokens=max_tokens
    )
    return response.model_dump()