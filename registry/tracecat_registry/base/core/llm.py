"""Core LLM actions."""
# XXX(WARNING): Do not import __future__ annotations from typing
# This will cause class types to be resolved as strings

from typing import Annotated, Any, Literal, cast

from tracecat.llm import DEFAULT_MODEL_TYPE, ModelType, route_llm_call
from typing_extensions import Doc

from tracecat_registry import RegistrySecret, registry

DEFAULT_SYSTEM_CONTEXT = "You will be provided with a body of text and your task is to do exactly as instructed."

llm_secret = RegistrySecret(
    name="llm",
    optional_keys=["OPENAI_API_KEY"],
)
"""OpenAI secret.

- name: `openai`
- keys:
    - `OPENAI_API_KEY`
"""


@registry.register(
    namespace="core",
    description="Call an AI model with a prompt and return the response.",
    default_title="AI Action",
    secrets=[llm_secret],
)
async def ai_action(
    prompt: Annotated[
        str,
        Doc("The prompt to send to the AI"),
    ],
    system_context: Annotated[
        str,
        Doc("The system context"),
    ] = DEFAULT_SYSTEM_CONTEXT,
    execution_context: Annotated[
        dict[str, Any] | None,
        Doc("The current execution context"),
    ] = None,
    model: Annotated[
        Literal[
            # Ollama Models
            "llama3.2",
            "llama3.2:1b",
            # OpenAI Models
            "gpt-4o",
            "gpt-4-turbo",
            "gpt-4-turbo-preview",
            "gpt-4-0125-preview",
            "gpt-4-vision-preview",
            "gpt-3.5-turbo-0125",
        ],
        Doc(
            "The AI Model to use. If you use an OpenAI model (gpt family),"
            " you must have the `OPENAI_API_KEY` secret set."
        ),
    ] = DEFAULT_MODEL_TYPE.value,
    additional_config: Annotated[
        dict[str, Any] | None,
        Doc("Additional configuration"),
    ] = None,
):
    exec_ctx_str = (
        (
            "Additional Instructions:"
            "\nYou have also been provided with the following JSON object of the previous task execution results,"
            " delimited by triple backticks (```)."
            " The object keys are the action ids and the values are the results of the actions."
            "\n```"
            f"\n{execution_context}"
            "\n```"
            "You may use the past action run results to help you complete your task."
            " If you think it isn't helpful, you may ignore it."
        )
        if execution_context
        else None
    )
    if exec_ctx_str:
        system_context += exec_ctx_str

    return await route_llm_call(
        prompt=prompt,
        system_context=system_context,
        model=cast(ModelType, model),
        additional_config=additional_config,
    )
