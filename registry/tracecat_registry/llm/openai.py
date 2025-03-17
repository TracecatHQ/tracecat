from typing import Annotated, Any

from typing_extensions import Doc

from tracecat.llm import async_openai_call, OpenAIModel, DEFAULT_OPENAI_MODEL
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
    default_title="Call OpenAI",
    description="Call an LLM via OpenAI API.",
    display_group="OpenAI",
    doc_url="https://platform.openai.com/docs/api-reference/responses/create",
    namespace="llm.openai",
    secrets=[openai_secret],
)
async def call(
    prompt: Annotated[
        str | list[dict[str, Any]],
        Doc("Prompt or conversation history to send to the LLM"),
    ],
    model: Annotated[
        str,
        Doc("Model to use"),
    ] = DEFAULT_OPENAI_MODEL.value,
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
) -> dict[str, Any]:
    response = await async_openai_call(
        prompt=prompt,
        model=OpenAIModel(model),
        memory=memory,  # type: ignore
        instructions=instructions,
        text_format=text_format,
        api_key=secrets.get("OPENAI_API_KEY"),
    )
    return response.model_dump()
