from typing import Annotated, Any

from typing_extensions import Doc

from tracecat.llm import (
    async_openai_call,
    async_openai_chat_completion,
    DEFAULT_OPENAI_MODEL,
)
from tracecat_registry import RegistrySecret, registry, secrets

openai_secret = RegistrySecret(
    name="openai",
    keys=["OPENAI_API_KEY"],
)
"""OpenAI secret.

- name: `openai`
- keys:
    - `OPENAI_API_KEY`
"""


@registry.register(
    default_title="Call OpenAI (responses)",
    description="Call an LLM via OpenAI responses API.",
    display_group="OpenAI",
    doc_url="https://platform.openai.com/docs/api-reference/responses/create",
    namespace="llm.openai",
    secrets=[openai_secret],
)
async def call(
    prompt: Annotated[
        str,
        Doc("Prompt or conversation history to send to the LLM"),
    ],
    model: Annotated[
        str,
        Doc("Model to use"),
    ] = DEFAULT_OPENAI_MODEL,
    memory: Annotated[
        list[dict[str, Any]] | None,
        Doc("Past messages to include in the conversation."),
    ] = None,
    instructions: Annotated[
        str | None, Doc("Insert a system message at the beginning of the conversation.")
    ] = None,
    text_format: Annotated[
        dict[str, Any] | None,
        Doc("Configuration options for a text response from the model."),
    ] = None,
    base_url: Annotated[
        str | None,
        Doc("Base URL for OpenAI API. Defaults to `https://api.openai.com`."),
    ] = None,
) -> dict[str, Any]:
    response = await async_openai_call(
        prompt=prompt,
        model=model,
        memory=memory,  # type: ignore
        instructions=instructions,
        text_format=text_format,
        api_key=secrets.get("OPENAI_API_KEY"),
        base_url=base_url,
    )
    json_response = response.model_dump()
    json_response["output_text"] = response.output_text
    return json_response


@registry.register(
    default_title="Call OpenAI (chat completion)",
    description="Call an LLM via OpenAI chat completion API.",
    display_group="OpenAI",
    doc_url="https://platform.openai.com/docs/api-reference/chat/completions",
    namespace="llm.openai",
)
async def chat_completion(
    prompt: Annotated[
        str,
        Doc("Prompt or conversation history to send to the LLM"),
    ],
    model: Annotated[
        str,
        Doc("Model to use"),
    ] = DEFAULT_OPENAI_MODEL,
    memory: Annotated[
        list[dict[str, Any]] | None,
        Doc("Past messages to include in the conversation."),
    ] = None,
    system_prompt: Annotated[
        str | None, Doc("Insert a system message at the beginning of the conversation.")
    ] = None,
    response_format: Annotated[
        dict[str, Any] | None,
        Doc("Configuration options for a text response from the model."),
    ] = None,
    base_url: Annotated[
        str | None,
        Doc("Base URL for OpenAI API. Defaults to `https://api.openai.com`."),
    ] = None,
) -> dict[str, Any]:
    response = await async_openai_chat_completion(
        prompt=prompt,
        model=model,
        memory=memory,  # type: ignore
        system_prompt=system_prompt,
        response_format=response_format,  # type: ignore
        api_key=secrets.get("OPENAI_API_KEY"),
        base_url=base_url,
    )
    json_response = response.model_dump()
    return json_response
