"""AI agent with tool calling capabilities. Returns the output and full message history."""

from typing import Annotated, Any, Literal
from tracecat_registry import registry
from tracecat.agent.agent.runtime import run_agent
from tracecat.agent.agent.providers import PYDANTIC_AI_REGISTRY_SECRETS

from tracecat.registry.fields import ActionType, TextArea
from typing_extensions import Doc


@registry.register(
    default_title="AI agent",
    description="AI agent with tool calling capabilities. Returns the output and full message history.",
    display_group="AI",
    doc_url="https://ai.pydantic.dev/agents/",
    secrets=[*PYDANTIC_AI_REGISTRY_SECRETS],
    namespace="ai",
)
async def agent(
    user_prompt: Annotated[
        str,
        Doc("User prompt to the agent."),
        TextArea(),
    ],
    model_name: Annotated[str, Doc("Name of the model to use.")],
    model_provider: Annotated[str, Doc("Provider of the model to use.")],
    actions: Annotated[
        list[str] | str,
        Doc("Actions (e.g. 'tools.slack.post_message') to include in the agent."),
        ActionType(multiple=True),
    ],
    fixed_arguments: Annotated[
        dict[str, dict[str, Any]] | None,
        Doc(
            "Fixed action arguments: keys are action names, values are keyword arguments. "
            "E.g. {'tools.slack.post_message': {'channel_id': 'C123456789', 'text': 'Hello, world!'}}"
        ),
    ] = None,
    instructions: Annotated[
        str | None, Doc("Instructions for the agent."), TextArea()
    ] = None,
    output_type: Annotated[
        Literal[
            "bool",
            "float",
            "int",
            "str",
            "list[bool]",
            "list[float]",
            "list[int]",
            "list[str]",
        ]
        | dict[str, Any]
        | None,
        Doc(
            "Output type for agent responses. Select from a list of supported types or provide a JSONSchema."
        ),
    ] = None,
    model_settings: Annotated[
        dict[str, Any] | None, Doc("Model settings for the agent.")
    ] = None,
    retries: Annotated[int, Doc("Number of retries for the agent.")] = 6,
    base_url: Annotated[str | None, Doc("Base URL of the model to use.")] = None,
) -> dict[str, str | dict[str, Any] | list[dict[str, Any]]]:
    return await run_agent(
        user_prompt=user_prompt,
        model_name=model_name,
        model_provider=model_provider,
        actions=actions if isinstance(actions, list) else [actions],
        fixed_arguments=fixed_arguments,
        instructions=instructions,
        output_type=output_type,
        model_settings=model_settings,
        retries=retries,
        base_url=base_url,
    )
