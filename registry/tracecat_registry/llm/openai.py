from typing import Annotated, Any

from typing_extensions import Doc

from tracecat.llm import async_openai_call, OpenAIModel
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
    namespace="llm.openai",
    secrets=[openai_secret],
)
async def call(
    prompt: Annotated[str, Doc("Prompt to send to the LLM")],
    model: Annotated[str, Doc("Model to use")],
    memory: Annotated[
        list[dict[str, Any]] | None, Doc("Past messages to include in the conversation")
    ] = None,
    system_prompt: Annotated[
        str | None, Doc("System prompt to use for the LLM")
    ] = None,
) -> dict[str, Any] | list[dict[str, Any]]:
    # NOTE: The type ignore is a workaround to avoid type mismatch
    # between the action types shown to the user and the
    # types used by internal Python functions.
    response = await async_openai_call(
        prompt=prompt,
        model=OpenAIModel(model),
        memory=memory,  # type: ignore
        system_prompt=system_prompt,
        api_key=secrets.get("OPENAI_API_KEY"),
    )
    return response.model_dump()
