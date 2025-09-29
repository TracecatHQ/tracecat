"""AI agent with tool calling capabilities. Returns the output and full message history."""

from typing import Annotated, Any, Literal
from tracecat_registry import registry, RegistrySecret
from tracecat.agent.runtime import run_agent

from tracecat.registry.fields import ActionType, TextArea
from typing_extensions import Doc

anthropic_secret = RegistrySecret(
    name="anthropic",
    optional_keys=["ANTHROPIC_API_KEY"],
    optional=True,
)
"""Anthropic API key.

- name: `anthropic`
- optional_keys:
    - `ANTHROPIC_API_KEY`: Optional Anthropic API key.
"""

openai_secret = RegistrySecret(
    name="openai",
    optional_keys=["OPENAI_API_KEY"],
    optional=True,
)
"""OpenAI API key.

- name: `openai`
- optional_keys:
    - `OPENAI_API_KEY`: Optional OpenAI API key.
"""

gemini_secret = RegistrySecret(
    name="gemini",
    optional_keys=["GEMINI_API_KEY"],
    optional=True,
)
"""Gemini API key.

- name: `gemini`
- optional_keys:
    - `GEMINI_API_KEY`: Optional Gemini API key.
"""


bedrock_secret = RegistrySecret(
    name="amazon_bedrock",
    optional_keys=[
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_REGION",
        "AWS_PROFILE",
        "AWS_ROLE_ARN",
        "AWS_ROLE_SESSION_NAME",
        "AWS_SESSION_TOKEN",
    ],
    optional=True,
)
"""AWS credentials.

- name: `amazon_bedrock`
- optional_keys:
    Either:
        - `AWS_ACCESS_KEY_ID`
        - `AWS_SECRET_ACCESS_KEY`
        - `AWS_REGION`
    Or:
        - `AWS_PROFILE`
    Or:
        - `AWS_ROLE_ARN`
        - `AWS_ROLE_SESSION_NAME` (optional)
    Or:
        - `AWS_SESSION_TOKEN`
"""


google_secret = RegistrySecret(
    name="google",
    optional_keys=["GOOGLE_API_CREDENTIALS"],
    optional=True,
)
"""Google API credentials.

- name: `google`
- optional_keys:
    - `GOOGLE_API_CREDENTIALS`: Optional Google API credentials.

Note: `GOOGLE_API_CREDENTIALS` should be a JSON string of the service account credentials.
"""


custom_model_provider_secret = RegistrySecret(
    name="custom-model-provider",
    optional_keys=[
        "CUSTOM_MODEL_PROVIDER_API_KEY",
        "CUSTOM_MODEL_PROVIDER_MODEL_NAME",
        "CUSTOM_MODEL_PROVIDER_BASE_URL",
    ],
    optional=True,
)
"""Custom model provider credentials.

- name: `custom-model-provider`
- optional_keys:
    - `CUSTOM_MODEL_PROVIDER_API_KEY`: Optional custom model provider API key.
    - `CUSTOM_MODEL_PROVIDER_MODEL_NAME`: Optional custom model provider model name.
    - `CUSTOM_MODEL_PROVIDER_BASE_URL`: Optional custom model provider base URL.
"""

langfuse_secret = RegistrySecret(
    name="langfuse",
    optional_keys=[
        "LANGFUSE_HOST",
        "LANGFUSE_PUBLIC_KEY",
        "LANGFUSE_SECRET_KEY",
    ],
    optional=True,
)
"""Langfuse observability configuration.

- name: `langfuse`
- optional_keys:
    - `LANGFUSE_HOST`: Optional Langfuse host URL.
    - `LANGFUSE_PUBLIC_KEY`: Optional Langfuse public key.
    - `LANGFUSE_SECRET_KEY`: Optional Langfuse secret key.
"""

PYDANTIC_AI_REGISTRY_SECRETS = [
    anthropic_secret,
    openai_secret,
    gemini_secret,
    bedrock_secret,
    custom_model_provider_secret,
]


@registry.register(
    default_title="AI agent",
    description="AI agent with tool calling capabilities. Returns the output and full message history.",
    display_group="AI",
    doc_url="https://ai.pydantic.dev/agents/",
    secrets=[*PYDANTIC_AI_REGISTRY_SECRETS, langfuse_secret],
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
        list[str],
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
    max_tools_calls: Annotated[
        int, Doc("Maximum number of tool calls for the agent.")
    ] = 15,
    max_requests: Annotated[int, Doc("Maximum number of requests for the agent.")] = 45,
    retries: Annotated[int, Doc("Number of retries for the agent.")] = 3,
    base_url: Annotated[str | None, Doc("Base URL of the model to use.")] = None,
) -> Any:
    return await run_agent(
        user_prompt=user_prompt,
        model_name=model_name,
        model_provider=model_provider,
        actions=actions,
        fixed_arguments=fixed_arguments,
        instructions=instructions,
        output_type=output_type,
        model_settings=model_settings,
        retries=retries,
        max_tools_calls=max_tools_calls,
        max_requests=max_requests,
        base_url=base_url,
    )


@registry.register(
    default_title="AI action",
    description="Call an LLM with a given prompt and model.",
    display_group="AI",
    doc_url="https://ai.pydantic.dev/agents/",
    namespace="ai",
    secrets=[*PYDANTIC_AI_REGISTRY_SECRETS],
)
async def action(
    user_prompt: Annotated[
        str,
        Doc("User prompt to the agent."),
        TextArea(),
    ],
    model_name: Annotated[str, Doc("Name of the model to use.")],
    model_provider: Annotated[str, Doc("Provider of the model to use.")],
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
    max_requests: Annotated[int, Doc("Maximum number of requests for the agent.")] = 20,
    retries: Annotated[int, Doc("Number of retries for the agent.")] = 6,
    base_url: Annotated[str | None, Doc("Base URL of the model to use.")] = None,
) -> Any:
    return await run_agent(
        user_prompt=user_prompt,
        model_name=model_name,
        model_provider=model_provider,
        instructions=instructions,
        output_type=output_type,
        model_settings=model_settings,
        retries=retries,
        max_requests=max_requests,
        base_url=base_url,
    )
