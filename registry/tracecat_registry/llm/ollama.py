from typing import Annotated, Any

from typing_extensions import Doc

from tracecat.llm import async_ollama_call
from tracecat_registry import registry


@registry.register(
    default_title="Call Ollama",
    description="Call an LLM via OpenAI API",
    display_group="Ollama",
    namespace="llm.ollama",
)
async def call(
    prompt: Annotated[str, Doc("Prompt to send to the LLM")],
    base_url: Annotated[str, Doc("Base URL for the Ollama API")],
    model: Annotated[str, Doc("Model to use")],
    memory: Annotated[
        list[dict[str, Any]] | None, Doc("Past messages to include in the conversation")
    ] = None,
    system_prompt: Annotated[
        str | None, Doc("System prompt to use for the LLM")
    ] = None,
    format: Annotated[
        dict[str, Any] | None,
        Doc("JSON schema for structured output."),
    ] = None,
) -> dict[str, Any]:
    response = await async_ollama_call(  # type: ignore
        prompt=prompt,
        model=model,
        memory=memory,
        system_prompt=system_prompt,
        format=format,
        base_url=base_url,
    )
    return response.model_dump()
