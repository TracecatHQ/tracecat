"""Core LLM actions."""
# XXX(WARNING): Do not import __future__ annotations from typing
# This will cause class types to be resolved as strings

from typing import Annotated, Any

from pydantic import Field

from tracecat.llm import DEFAULT_MODEL_TYPE, ModelType, async_openai_call
from tracecat.registry import registry


def _event_context_instructions(event_context: dict[str, Any]) -> str:
    return (
        "Additional Instructions:"
        "\nYou have also been provided with the following JSON object of the previous task execution results,"
        " delimited by triple backticks (```)."
        " The object keys are the action ids and the values are the results of the actions."
        "\n```"
        f"\n{event_context}"
        "\n```"
        "You may use the past action run results to help you complete your task."
        " If you think it isn't helpful, you may ignore it."
    )


DEFAULT_SYSTEM_CONTEXT = "You will be provided with a body of text and your task is to do exactly as instructed."


@registry.register(
    namespace="core",
    version="0.1.0",
    description="Call an LLM.",
    default_title="AI Action",
    secrets=["openai"],
)
async def ai_action(
    prompt: Annotated[str, Field(description="The prompt to send to the LLM")],
    system_context: Annotated[
        str, Field(description="The system context")
    ] = DEFAULT_SYSTEM_CONTEXT,
    execution_context: Annotated[
        dict[str, Any] | None, Field(description="The execution context")
    ] = None,
    model: Annotated[
        ModelType, Field(description="The AI Model to use")
    ] = DEFAULT_MODEL_TYPE,
    additional_config: Annotated[
        dict[str, Any] | None, Field(description="Additional configuration")
    ] = None,
):
    exec_ctx_str = (
        _event_context_instructions(execution_context) if execution_context else None
    )
    if exec_ctx_str:
        system_context += exec_ctx_str

    return await async_openai_call(
        text=prompt,
        system_context=system_context,
        model=model,
        **(additional_config or {}),
    )
