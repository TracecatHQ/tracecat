from typing import Annotated, Any

from typing_extensions import Doc

from tracecat.llm import async_ollama_call, OllamaModel
from tracecat_registry import RegistrySecret, registry, secrets

ollama_secret = RegistrySecret(
    name="ollama",
    optional_keys=["OLLAMA_API_URL"],
    optional=True,
)
"""Ollama secret.

- name: `ollama`
- keys:
    - `OLLAMA_API_URL`
"""


@registry.register(
    default_title="Call Ollama",
    description="Call an LLM via OpenAI API",
    display_group="Ollama",
    namespace="llm.ollama",
    secrets=[ollama_secret],
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
) -> dict[str, Any]:
    response = await async_ollama_call(  # type: ignore
        prompt=prompt,
        model=OllamaModel(model),
        memory=memory,
        system_prompt=system_prompt,
        api_url=secrets.get("OLLAMA_API_URL", None),
    )
    return response.model_dump()
